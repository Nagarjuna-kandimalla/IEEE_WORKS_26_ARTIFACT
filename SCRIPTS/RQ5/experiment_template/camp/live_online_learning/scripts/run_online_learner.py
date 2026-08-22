#!/usr/bin/env python3
"""Ingest task outcomes and publish cumulative CAMP model versions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CAMP_ROOT = ROOT.parent
LEGACY_ROOT = CAMP_ROOT.parent / "WORKS_ML"
sys.path.insert(0, str(CAMP_ROOT / "scripts"))
sys.path.insert(0, str(LEGACY_ROOT))
sys.path.insert(0, str(ROOT))

import common
from modeling import fit_final_predictions, materialize_model_features
from camp_ml.online_history import SQLiteHistoryStore, TaskOutcome
from camp_online.engine import ModelGroup


QUANTILES = [0.5, 0.9, 0.95, 0.99, 0.995]
TAIL_QUANTILES = [0.9, 0.95, 0.99, 0.995]
VARIANTS = {
    "A": "a",
    "A+P": "a_plus_p",
    "A+P+C": "a_plus_p_plus_c",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    temporary.replace(path)


class OnlineLearner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        common.CONFIG_PATH = args.experiment_config.resolve()
        self.outcome_dir = args.outcome_dir.resolve()
        self.decision_dir = args.decision_dir.resolve()
        self.history_update_dir = args.history_update_dir.resolve()
        self.version_dir = args.version_dir.resolve()
        self.pointer_path = args.current_pointer.resolve()
        self.update_log = args.update_log.resolve()
        self.outcome_dir.mkdir(parents=True, exist_ok=True)
        self.history_update_dir.mkdir(parents=True, exist_ok=True)
        self.version_dir.mkdir(parents=True, exist_ok=True)
        self.base_training = pd.read_csv(
            args.base_training_frame.resolve(),
            sep="\t",
            low_memory=False,
        )
        self.base_history = pd.read_csv(
            args.base_history_features.resolve(),
            sep="\t",
            low_memory=False,
        )
        if self.base_training.empty or self.base_history.empty:
            raise ValueError("base training/history data must not be empty")
        if "c_hat_bytes" not in self.base_training:
            raise ValueError("base training frame lacks leakage-free C-hat")
        self.base_history_max_order = int(
            pd.to_numeric(
                self.base_history["history_order"],
                errors="raise",
            ).max()
        )
        self.initial_model_root = args.initial_model_root.resolve()
        with (
            self.initial_model_root / "online_manifest.json"
        ).open(encoding="utf-8") as handle:
            self.initial_manifest = json.load(handle)
        self.history = SQLiteHistoryStore(args.history_db.resolve())
        self.state_path = args.state_db.resolve()
        self.state = sqlite3.connect(
            self.state_path,
            timeout=30.0,
            check_same_thread=False,
        )
        self.state.row_factory = sqlite3.Row
        # The state database has one owning process. Rollback journaling avoids
        # unsupported cross-host WAL locks on the shared filesystem.
        self.state.execute("PRAGMA journal_mode=DELETE")
        self.state.execute("PRAGMA synchronous=FULL")
        self.state.execute("PRAGMA busy_timeout=30000")
        self._create_schema()
        self.lock = threading.RLock()
        self.training_thread: threading.Thread | None = None
        self.training_error: BaseException | None = None
        self.next_update_count = self._next_update_count()

    def close(self) -> None:
        thread = self.training_thread
        if thread is not None and thread.is_alive():
            thread.join()
        self.history.close()
        self.state.close()

    def _create_schema(self) -> None:
        self.state.executescript(
            """
            CREATE TABLE IF NOT EXISTS processed_outcomes (
                path TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                task_id TEXT NOT NULL,
                completion_time TEXT NOT NULL,
                exit_status INTEGER NOT NULL,
                oom_flag INTEGER NOT NULL,
                training_eligible INTEGER NOT NULL,
                ingested_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS online_rows (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                completion_time TEXT NOT NULL,
                workflow TEXT NOT NULL,
                process TEXT NOT NULL,
                row_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_updates (
                sequence INTEGER PRIMARY KEY,
                model_version TEXT NOT NULL UNIQUE,
                eligible_count INTEGER NOT NULL,
                training_rows INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                published_at TEXT NOT NULL,
                model_root TEXT NOT NULL,
                history_features TEXT NOT NULL,
                fit_seconds REAL NOT NULL
            );
            """
        )
        self.state.commit()

    def _next_update_count(self) -> int:
        row = self.state.execute(
            "SELECT MAX(eligible_count) AS n FROM model_updates"
        ).fetchone()
        completed = 0 if row["n"] is None else int(row["n"])
        if completed < self.args.first_update:
            return self.args.first_update
        return (
            (
                (completed - self.args.first_update)
                // self.args.update_every
                + 1
            )
            * self.args.update_every
            + self.args.first_update
        )

    def _decision_for_outcome(self, outcome_path: Path) -> dict[str, Any]:
        decision_path = self.decision_dir / outcome_path.name.replace(
            ".outcome.json",
            ".json",
        )
        if not decision_path.is_file():
            raise FileNotFoundError(
                f"decision is missing for {outcome_path.name}"
            )
        return json.loads(decision_path.read_text(encoding="utf-8"))

    def _training_row(
        self,
        decision: dict[str, Any],
        outcome: TaskOutcome,
        sequence: int,
    ) -> dict[str, Any]:
        row = dict(decision["camp_feature_row"])
        row.update(
            {
                "logical_task_id": str(outcome.task_id),
                "history_order": self.base_history_max_order + sequence,
                "history_role": "online_completed",
                "peak_memory_bytes": int(outcome.peak_memory_bytes),
                "ebpf_total_consumed_bytes": int(
                    outcome.consumed_bytes
                ),
                "completion_time": str(outcome.completion_time),
                "base_a_mib": float(
                    decision["variant_predictions"]["A"]["allocation"][
                        "base_mib"
                    ]
                ),
                "base_a_plus_p_mib": float(
                    decision["variant_predictions"]["A+P"][
                        "allocation"
                    ]["base_mib"]
                ),
                "base_a_plus_p_plus_c_mib": float(
                    decision["variant_predictions"]["A+P+C"][
                        "allocation"
                    ]["base_mib"]
                ),
                "prediction_model_version": str(
                    decision["model_version"]
                ),
            }
        )
        return row

    def ingest_available(self) -> int:
        ingested = 0
        for path in sorted(self.outcome_dir.glob("*.outcome.json")):
            relative = path.name
            digest = checksum(path)
            with self.lock:
                existing = self.state.execute(
                    "SELECT sha256 FROM processed_outcomes WHERE path = ?",
                    (relative,),
                ).fetchone()
            if existing is not None:
                if str(existing["sha256"]) != digest:
                    raise RuntimeError(
                        f"immutable outcome changed: {path}"
                    )
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw = dict(payload["task_outcome"])
            allowed = {field.name for field in fields(TaskOutcome)}
            outcome = TaskOutcome(
                **{
                    key: value
                    for key, value in raw.items()
                    if key in allowed
                }
            )
            exit_status = int(payload.get("exit_status", 0))
            eligible = bool(
                exit_status == 0
                and outcome.peak_memory_bytes is not None
                and outcome.consumed_bytes is not None
            )
            row_json = None
            if eligible:
                with self.lock:
                    next_sequence = int(
                        self.state.execute(
                            "SELECT COUNT(*) + 1 AS n FROM online_rows"
                        ).fetchone()["n"]
                    )
                decision = self._decision_for_outcome(path)
                training_row = self._training_row(
                    decision,
                    outcome,
                    next_sequence,
                )
                row_json = json.dumps(training_row, allow_nan=False)
                self.history.record(outcome)
            elif outcome.oom_flag:
                self.history.record(
                    replace(
                        outcome,
                        peak_memory_bytes=None,
                        consumed_bytes=None,
                    )
                )
            with self.lock:
                with self.state:
                    self.state.execute(
                        """
                        INSERT INTO processed_outcomes (
                            path, sha256, task_id, completion_time,
                            exit_status, oom_flag, training_eligible,
                            ingested_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            relative,
                            digest,
                            outcome.task_id,
                            outcome.completion_time,
                            exit_status,
                            int(bool(outcome.oom_flag)),
                            int(eligible),
                            utc_now(),
                        ),
                    )
                    if eligible:
                        self.state.execute(
                            """
                            INSERT INTO online_rows (
                                task_id, completion_time, workflow, process,
                                row_json
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                outcome.task_id,
                                outcome.completion_time,
                                outcome.workflow,
                                outcome.process,
                                row_json,
                            ),
                        )
            if eligible:
                write_json_atomic(
                    self.history_update_dir
                    / f"{next_sequence:08d}.json",
                    {
                        "sequence": next_sequence,
                        "row": training_row,
                    },
                )
            append_jsonl(
                self.update_log,
                {
                    "event": "outcome_ingested",
                    "path": relative,
                    "task_id": outcome.task_id,
                    "exit_status": exit_status,
                    "oom_flag": bool(outcome.oom_flag),
                    "training_eligible": eligible,
                },
            )
            ingested += 1
        return ingested

    def eligible_count(self) -> int:
        with self.lock:
            return int(
                self.state.execute(
                    "SELECT COUNT(*) AS n FROM online_rows"
                ).fetchone()["n"]
            )

    def latest_published_count(self) -> int:
        with self.lock:
            row = self.state.execute(
                "SELECT MAX(eligible_count) AS n FROM model_updates"
            ).fetchone()
        return 0 if row["n"] is None else int(row["n"])

    def live_frame(self, eligible_count: int) -> pd.DataFrame:
        with self.lock:
            rows = self.state.execute(
                """
                SELECT row_json
                FROM online_rows
                WHERE sequence <= ?
                ORDER BY sequence
                """,
                (eligible_count,),
            ).fetchall()
        return pd.DataFrame.from_records(
            [json.loads(row["row_json"]) for row in rows]
        )

    def _fit_process_tails(
        self,
        features: pd.DataFrame,
        target_mib: np.ndarray,
        model_root: Path,
    ) -> dict[str, int]:
        support_result = {}
        workflow_mask = (
            features["workflow"]
            .astype(str)
            .str.lower()
            .eq(self.args.workflow.lower())
        )
        for variant, path_name in (
            ("A", "a"),
            ("A+P+C", "a_plus_p_plus_c"),
        ):
            destination = model_root / f"{path_name}_process_tail"
            canonical = (
                self.args.canonical_model_root.resolve()
                / f"{path_name}_process_tail"
            )
            if destination.exists():
                shutil.rmtree(destination)
            if canonical.is_dir():
                shutil.copytree(canonical, destination)
                with (
                    destination / "metadata.json"
                ).open(encoding="utf-8") as handle:
                    metadata = json.load(handle)
            else:
                destination.mkdir(parents=True)
                metadata = {
                    "scope": "per_process",
                    "view": variant,
                    "quantiles": TAIL_QUANTILES,
                    "processes": {},
                }
            process_count = 0
            for (workflow, process), group in features.loc[
                workflow_mask
            ].groupby(["workflow", "process"], sort=True):
                if len(group) < self.args.process_min_support:
                    continue
                positions = group.index.to_numpy(dtype=int)
                local = group.reset_index(drop=True)
                process_key = f"{workflow}::{process}"
                process_label = common.stable_hash(process_key)[:16]
                _, fit_metadata = fit_final_predictions(
                    local,
                    local.iloc[:1].copy(),
                    target_mib[positions],
                    view=variant,
                    quantiles=TAIL_QUANTILES,
                    n_jobs=self.args.n_jobs,
                    model_root=destination / process_label,
                )
                metadata["processes"][process_key] = {
                    **fit_metadata,
                    "model_directory": process_label,
                    "online_support": int(len(group)),
                }
                process_count += 1
            common.write_json(destination / "metadata.json", metadata)
            support_result[variant] = process_count
        return support_result

    @staticmethod
    def _process_tail_metadata(
        model_root: Path,
        variant_path: str,
    ) -> dict[str, dict[str, Any]]:
        metadata_path = (
            model_root
            / f"{variant_path}_process_tail"
            / "metadata.json"
        )
        if not metadata_path.is_file():
            return {}
        with metadata_path.open(encoding="utf-8") as handle:
            return json.load(handle)["processes"]

    def _version_calibration(
        self,
        calibration_raw: pd.DataFrame,
        model_root: Path,
        model_version: str,
    ) -> pd.DataFrame:
        if calibration_raw.empty:
            raise ValueError("version calibration block is empty")
        features = materialize_model_features(calibration_raw.copy())
        result = calibration_raw.copy().reset_index(drop=True)

        def bases(path_name: str) -> tuple[np.ndarray, list[str]]:
            global_group = ModelGroup(
                model_root / path_name,
                tuple(QUANTILES),
            )
            global_values = global_group.predict(features)
            values = np.maximum(
                global_values[0.5],
                global_values[0.995],
            )
            scopes = np.full(len(features), "global", dtype=object)
            process_metadata = self._process_tail_metadata(
                model_root,
                path_name,
            )
            workflow_values = features["workflow"].astype(str)
            process_values = features["process"].astype(str)
            for key, details in process_metadata.items():
                workflow, process = key.split("::", 1)
                mask = (
                    workflow_values.str.lower().eq(workflow.lower())
                    & process_values.eq(process)
                ).to_numpy()
                if not mask.any():
                    continue
                group = ModelGroup(
                    model_root
                    / f"{path_name}_process_tail"
                    / details["model_directory"],
                    tuple(TAIL_QUANTILES),
                )
                local = group.predict(features.loc[mask])[0.995]
                values[mask] = np.maximum(
                    global_values[0.5][mask],
                    local,
                )
                scopes[mask] = "process"
            return values, scopes.tolist()

        a_base, a_scope = bases("a")
        camp_base, camp_scope = bases("a_plus_p_plus_c")
        result["calibration_model_version"] = model_version
        result["base_a_mib"] = a_base
        result["base_a_plus_p_plus_c_mib"] = camp_base
        result["a_calibration_model_scope"] = a_scope
        result["camp_calibration_model_scope"] = camp_scope
        return result

    def fit_and_publish(self, eligible_count: int) -> None:
        started = time.time()
        started_at = utc_now()
        live = self.live_frame(eligible_count)
        if len(live) <= self.args.calibration_window:
            raise ValueError(
                "eligible rows must exceed the calibration window: "
                f"eligible={len(live)}, "
                f"window={self.args.calibration_window}"
            )
        calibration_live = live.tail(
            self.args.calibration_window
        ).reset_index(drop=True)
        training_live = live.iloc[
            : -self.args.calibration_window
        ].reset_index(drop=True)
        training_pool = pd.concat(
            [self.base_training, live],
            ignore_index=True,
            sort=False,
        )
        fit_raw = pd.concat(
            [self.base_training, training_live],
            ignore_index=True,
            sort=False,
        )
        features = materialize_model_features(fit_raw)
        target_mib = (
            pd.to_numeric(
                features["peak_memory_bytes"],
                errors="raise",
            ).to_numpy(dtype=float)
            / 2**20
        )
        consumed_target = pd.to_numeric(
            features["ebpf_total_consumed_bytes"],
            errors="raise",
        ).to_numpy(dtype=float)
        ratio_target = (
            pd.to_numeric(
                features["peak_memory_bytes"],
                errors="raise",
            ).to_numpy(dtype=float)
            / np.maximum(consumed_target, 1.0)
            * 1_000_000.0
        )
        with self.lock:
            sequence = int(
                self.state.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS n "
                    "FROM model_updates"
                ).fetchone()["n"]
            )
        version_prefix = re.sub(
            r"[^A-Za-z0-9_.-]+",
            "-",
            self.args.version_prefix,
        ).strip("-")
        model_version = (
            f"{version_prefix}-{sequence:04d}-completed-"
            f"{eligible_count:05d}"
        )
        temporary = self.version_dir / f".{model_version}.tmp"
        final_root = self.version_dir / model_version
        if temporary.exists():
            shutil.rmtree(temporary)
        if final_root.exists():
            raise RuntimeError(f"model version exists: {final_root}")
        temporary.mkdir(parents=True)

        shutil.copy2(
            self.args.canonical_model_root.resolve()
            / "signature_scaler.joblib",
            temporary / "signature_scaler.joblib",
        )
        fit_final_predictions(
            features,
            features.iloc[:1].copy(),
            consumed_target,
            view="A+P",
            quantiles=[0.5],
            n_jobs=self.args.n_jobs,
            model_root=temporary / "consumed_c_hat",
        )
        fit_final_predictions(
            features,
            features.iloc[:1].copy(),
            ratio_target,
            view="A+P",
            quantiles=[0.5],
            n_jobs=self.args.n_jobs,
            model_root=temporary / "memory_per_consumed_ratio",
        )
        for variant, path_name in VARIANTS.items():
            fit_final_predictions(
                features,
                features.iloc[:1].copy(),
                target_mib,
                view=variant,
                quantiles=QUANTILES,
                n_jobs=self.args.n_jobs,
                model_root=temporary / path_name,
            )
        process_models = self._fit_process_tails(
            features,
            target_mib,
            temporary,
        )
        version_calibration = self._version_calibration(
            calibration_live,
            temporary,
            model_version,
        )
        version_calibration.to_csv(
            temporary / "version_calibration.tsv",
            sep="\t",
            index=False,
        )
        history = pd.concat(
            [self.base_history, live],
            ignore_index=True,
            sort=False,
        ).sort_values("history_order", kind="mergesort")
        history.to_csv(
            temporary / "history_features.tsv",
            sep="\t",
            index=False,
        )
        training_pool.to_csv(
            temporary / "base_training_frame.tsv",
            sep="\t",
            index=False,
        )
        manifest = {
            "design": "camp_live_online_learning",
            "model_version": model_version,
            "parent_model_version": self.current_model_version(),
            "created_at": utc_now(),
            "training_rows": int(len(fit_raw)),
            "training_pool_rows": int(len(training_pool)),
            "base_training_rows": int(len(self.base_training)),
            "online_training_rows": int(len(training_live)),
            "version_calibration_rows": int(len(version_calibration)),
            "version_calibration_model_version": model_version,
            "version_calibration_contract": (
                "newest completed online block is excluded from fitting; "
                "the exact published model predicts that block"
            ),
            "eligible_count": eligible_count,
            "known_workflows": sorted(
                fit_raw["workflow"].astype(str).str.lower().unique()
            ),
            "feature_views": ["A", "A+P", "A+P+C"],
            "process_tail_models_refit": process_models,
            "allocation_contract": (
                "cold leave-one-workflow-out A+P+C; otherwise calibrated "
                "process/global A+P+C with RQ2 I0-I5 residual calibration "
                "and no A prediction floor"
            ),
            "frozen_components": {
                "quantiles": QUANTILES,
                "model_seeds": common.load_config()["models"][
                    "model_seeds"
                ],
                "point_stack_weights": False,
                "calibration_policy": True,
                "calibration_window": self.args.calibration_window,
                "retry_multiplier": 1.5,
            },
        }
        (temporary / "online_manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(final_root)
        pointer = {
            "model_version": model_version,
            "model_root": str(final_root.resolve()),
            "history_features": str(
                (final_root / "history_features.tsv").resolve()
            ),
            "eligible_count": eligible_count,
            "history_sequence": eligible_count,
            "published_at": utc_now(),
        }
        write_json_atomic(self.pointer_path, pointer)
        elapsed = time.time() - started
        with self.lock, self.state:
            self.state.execute(
                """
                INSERT INTO model_updates (
                    sequence, model_version, eligible_count, training_rows,
                    started_at, published_at, model_root,
                    history_features, fit_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sequence,
                    model_version,
                    eligible_count,
                    len(fit_raw),
                    started_at,
                    pointer["published_at"],
                    pointer["model_root"],
                    pointer["history_features"],
                    elapsed,
                ),
            )
            while self.next_update_count <= eligible_count:
                self.next_update_count += self.args.update_every
        append_jsonl(
            self.update_log,
            {
                "event": "model_published",
                "sequence": sequence,
                "model_version": model_version,
                "eligible_count": eligible_count,
                "training_rows": len(fit_raw),
                "fit_seconds": elapsed,
                "process_tail_models_refit": process_models,
            },
        )

    def current_model_version(self) -> str:
        if self.pointer_path.is_file():
            return str(
                json.loads(
                    self.pointer_path.read_text(encoding="utf-8")
                )["model_version"]
            )
        return str(self.initial_manifest["model_version"])

    def _training_target(self, eligible: int) -> int | None:
        with self.lock:
            if self.training_error is not None:
                raise RuntimeError("online model training failed") from (
                    self.training_error
                )
            if (
                self.training_thread is not None
                and self.training_thread.is_alive()
            ):
                return None
            if eligible < self.next_update_count:
                return None
            return eligible

    def _training_worker(self, eligible_count: int) -> None:
        try:
            self.fit_and_publish(eligible_count)
        except BaseException as error:
            with self.lock:
                self.training_error = error
            append_jsonl(
                self.update_log,
                {
                    "event": "model_publish_failed",
                    "eligible_count": eligible_count,
                    "error": f"{type(error).__name__}: {error}",
                },
            )

    def start_training_if_due(self) -> None:
        eligible = self.eligible_count()
        target = self._training_target(eligible)
        if target is None:
            return
        thread = threading.Thread(
            target=self._training_worker,
            args=(target,),
            name=f"camp-fit-{target}",
            daemon=False,
        )
        with self.lock:
            self.training_thread = thread
        thread.start()

    def wait_for_training(self) -> None:
        thread = self.training_thread
        if thread is not None:
            thread.join()
        if self.training_error is not None:
            raise RuntimeError("online model training failed") from (
                self.training_error
            )

    def publish_final_if_needed(self) -> None:
        self.wait_for_training()
        eligible = self.eligible_count()
        if eligible > self.latest_published_count():
            self.fit_and_publish(eligible)

    def run(self) -> None:
        append_jsonl(
            self.update_log,
            {
                "event": "learner_started",
                "initial_model_version": self.initial_manifest[
                    "model_version"
                ],
                "base_training_rows": len(self.base_training),
                "base_history_rows": len(self.base_history),
                "first_update": self.args.first_update,
                "update_every": self.args.update_every,
                "workflow": self.args.workflow,
            },
        )
        self.args.ready_file.write_text(
            (
                f"pid={os.getpid()}\n"
                f"workflow={self.args.workflow}\n"
                f"first_update={self.args.first_update}\n"
                f"update_every={self.args.update_every}\n"
            ),
            encoding="utf-8",
        )
        try:
            while not self.args.stop_file.exists():
                self.ingest_available()
                self.start_training_if_due()
                if self.training_error is not None:
                    self.wait_for_training()
                time.sleep(self.args.poll_seconds)
            self.ingest_available()
            self.publish_final_if_needed()
            append_jsonl(
                self.update_log,
                {
                    "event": "learner_stopped",
                    "eligible_count": self.eligible_count(),
                    "final_model_version": self.current_model_version(),
                },
            )
        finally:
            self.args.ready_file.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--version-prefix", required=True)
    parser.add_argument("--history-db", type=Path, required=True)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--history-update-dir", type=Path, required=True)
    parser.add_argument("--outcome-dir", type=Path, required=True)
    parser.add_argument("--decision-dir", type=Path, required=True)
    parser.add_argument("--base-training-frame", type=Path, required=True)
    parser.add_argument("--base-history-features", type=Path, required=True)
    parser.add_argument("--initial-model-root", type=Path, required=True)
    parser.add_argument("--canonical-model-root", type=Path, required=True)
    parser.add_argument("--experiment-config", type=Path, required=True)
    parser.add_argument("--current-pointer", type=Path, required=True)
    parser.add_argument("--version-dir", type=Path, required=True)
    parser.add_argument("--update-log", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--first-update", type=int, default=32)
    parser.add_argument("--update-every", type=int, default=64)
    parser.add_argument("--process-min-support", type=int, default=16)
    parser.add_argument("--calibration-window", type=int, default=64)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--n-jobs", type=int, default=16)
    args = parser.parse_args()
    if min(
        args.first_update,
        args.update_every,
        args.process_min_support,
        args.calibration_window,
        args.n_jobs,
    ) < 1:
        parser.error("counts and n-jobs must be positive")
    return args


def main() -> None:
    args = parse_args()
    learner = OnlineLearner(args)
    try:
        learner.run()
    finally:
        learner.close()


if __name__ == "__main__":
    main()
