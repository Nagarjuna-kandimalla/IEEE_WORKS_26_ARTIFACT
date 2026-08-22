"""Persistent, completion-time-safe multi-scope history for CAMP."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from camp_ml.task_metadata import REGISTRY_VERSION, classify_task


HISTORY_SCOPES = (
    "exact_process",
    "local_process",
    "canonical_operation",
    "feature_class",
    "workflow",
    "global",
)
SCOPE_SPECIFICITY = {
    "exact_process": 1.00,
    "local_process": 0.90,
    "canonical_operation": 0.70,
    "feature_class": 0.50,
    "workflow": 0.40,
    "global": 0.25,
}
DEFAULT_SUPPORT_PRIOR = 20.0
DEFAULT_RECENCY_DAYS = 30.0


def timestamp_epoch(value: str | datetime) -> float:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def timestamp_text(value: str | datetime) -> str:
    return datetime.fromtimestamp(
        timestamp_epoch(value), timezone.utc
    ).isoformat()


def _scope_key(*parts: str) -> str:
    return " | ".join(str(part).strip() or "__UNKNOWN__" for part in parts)


@dataclass(frozen=True)
class TaskIdentity:
    workflow: str
    process: str
    version: str
    system_config_id: str
    canonical_operation: str = ""
    feature_classes: tuple[str, ...] = ()
    task_instance: str = ""
    input_identity: str = ""

    @property
    def task_bucket_id(self) -> str:
        return _scope_key(self.workflow, self.process, self.version)

    def normalized(self) -> "TaskIdentity":
        metadata = classify_task(self.process)
        return TaskIdentity(
            workflow=self.workflow.strip(),
            process=self.process.strip(),
            version=self.version.strip(),
            system_config_id=(
                self.system_config_id.strip() or "__UNKNOWN_CONFIG__"
            ),
            canonical_operation=(
                self.canonical_operation.strip()
                or metadata.canonical_operation
            ),
            feature_classes=(
                tuple(sorted(set(self.feature_classes)))
                if self.feature_classes
                else metadata.feature_classes
            ),
            task_instance=self.task_instance.strip(),
            input_identity=self.input_identity.strip(),
        )


@dataclass(frozen=True)
class TaskOutcome:
    task_id: str
    workflow: str
    process: str
    version: str
    decision_time: str
    completion_time: str
    static_input_bytes: int
    peak_memory_bytes: int | None
    consumed_bytes: int | None = None
    runtime_seconds: float | None = None
    oom_flag: bool = False
    system_config_id: str = ""
    source_run_id: str = ""
    static_prediction_mb: float | None = None
    history_prediction_mb: float | None = None
    allocated_memory_mb: float | None = None
    model_version: str = ""
    canonical_operation: str = ""
    feature_classes: tuple[str, ...] = ()
    task_instance: str = ""
    input_identity: str = ""

    def identity(self) -> TaskIdentity:
        return TaskIdentity(
            workflow=self.workflow,
            process=self.process,
            version=self.version,
            system_config_id=self.system_config_id,
            canonical_operation=self.canonical_operation,
            feature_classes=self.feature_classes,
            task_instance=self.task_instance,
            input_identity=self.input_identity,
        ).normalized()


@dataclass(frozen=True)
class HistoryScopeSnapshot:
    history_scope: str
    history_scope_key: str
    history_confidence: float
    confidence_source: str
    history_completed_count: int
    history_consumed_data_count: int
    history_support_count: int
    history_oom_count: int
    consumed_data_coverage: float
    scope_specificity: float
    recency_score: float
    stability_score: float
    input_similarity_score: float
    prior_peak_median_bytes: int | None
    prior_peak_q95_bytes: int | None
    prior_peak_q99_bytes: int | None
    prior_consumed_median_bytes: int | None
    prior_consumed_q95_bytes: int | None
    prior_consumed_to_input_median: float | None
    prior_consumed_to_input_q95: float | None
    prior_runtime_median_seconds: float | None
    prior_oom_rate: float | None
    prior_static_log_residual_median: float | None
    prior_static_log_residual_q95: float | None
    prior_static_log_residual_mad: float | None
    prior_allocation_failure_rate: float | None
    last_completion_time: str | None
    median_input_bytes: int | None
    prior_memory_to_consumed_median: float | None
    prior_recent32_peak_median_bytes: int | None
    prior_recent32_consumed_to_input_median: float | None
    prior_recent32_memory_to_consumed_median: float | None
    prior_recent32_consumed_median_bytes: int | None = None
    prior_recent32_consumed_q95_bytes: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class HistoryContext:
    as_of_time: str
    identity: TaskIdentity
    scopes: tuple[HistoryScopeSnapshot, ...]
    exact_instance_count: int = 0
    exact_input_count: int = 0
    instance_distinct_processes: int = 0
    instance_consumed_median_bytes: int | None = None
    input_consumed_median_bytes: int | None = None
    nearest32_mean_log_input_distance: float | None = None
    nearest32_consumed_median_bytes: int | None = None
    nearest32_consumed_q95_bytes: int | None = None
    nearest32_consumed_to_input_median: float | None = None
    nearest32_memory_to_consumed_median: float | None = None

    @property
    def best_scope(self) -> HistoryScopeSnapshot | None:
        supported = [
            scope for scope in self.scopes if scope.history_support_count > 0
        ]
        return max(
            supported,
            key=lambda scope: scope.history_confidence,
            default=None,
        )

    def to_dict(self) -> dict[str, object]:
        identity = asdict(self.identity)
        identity["task_bucket_id"] = self.identity.task_bucket_id
        return {
            "as_of_time": self.as_of_time,
            "identity": identity,
            "scopes": [scope.to_dict() for scope in self.scopes],
            "instance_history": {
                "exact_instance_count": self.exact_instance_count,
                "exact_input_count": self.exact_input_count,
                "instance_distinct_processes": self.instance_distinct_processes,
                "instance_consumed_median_bytes": (
                    self.instance_consumed_median_bytes
                ),
                "input_consumed_median_bytes": (
                    self.input_consumed_median_bytes
                ),
                "nearest32_mean_log_input_distance": (
                    self.nearest32_mean_log_input_distance
                ),
                "nearest32_consumed_median_bytes": (
                    self.nearest32_consumed_median_bytes
                ),
                "nearest32_consumed_q95_bytes": (
                    self.nearest32_consumed_q95_bytes
                ),
                "nearest32_consumed_to_input_median": (
                    self.nearest32_consumed_to_input_median
                ),
                "nearest32_memory_to_consumed_median": (
                    self.nearest32_memory_to_consumed_median
                ),
            },
            "best_scope": (
                None if self.best_scope is None else self.best_scope.history_scope
            ),
        }


def scope_definitions(
    identity: TaskIdentity,
) -> tuple[tuple[str, str, str | None], ...]:
    item = identity.normalized()
    config = item.system_config_id
    scopes: list[tuple[str, str, str | None]] = [
        (
            "exact_process",
            _scope_key(
                item.workflow, item.process, item.version, config
            ),
            None,
        ),
        (
            "local_process",
            _scope_key(item.workflow, item.process, config),
            None,
        ),
        (
            "canonical_operation",
            _scope_key(item.canonical_operation, config),
            None,
        ),
    ]
    scopes.extend(
        (
            "feature_class",
            _scope_key(feature_class, config),
            feature_class,
        )
        for feature_class in item.feature_classes
    )
    scopes.extend(
        [
            (
                "workflow",
                _scope_key(item.workflow, config),
                None,
            ),
            ("global", _scope_key(config), None),
        ]
    )
    return tuple(scopes)


def _positive_int(value: int | float | None, field: str) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{field} must be positive")
    return parsed


def _finite_float(value: float | None, field: str) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return parsed


def _order_quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def _integer_quantile(
    values: list[float], probability: float
) -> int | None:
    value = _order_quantile(values, probability)
    return None if value is None else int(round(value))


def _median_absolute_deviation(values: list[float]) -> float | None:
    if not values:
        return None
    array = np.asarray(values, dtype=float)
    median = np.median(array)
    return float(np.median(np.abs(array - median)))


class SQLiteHistoryStore:
    """Store one task outcome and derive all applicable history scopes."""

    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = Path(path).resolve()
        self.read_only = read_only
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            self.connection = sqlite3.connect(
                f"file:{self.path}?mode=ro",
                uri=True,
                timeout=30.0,
            )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path, timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._row_cache: dict[tuple[object, ...], list[sqlite3.Row]] = {}
        if not read_only:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self._create_schema()
            self._backfill_metadata()

    def __enter__(self) -> "SQLiteHistoryStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_outcomes (
                task_id TEXT PRIMARY KEY,
                workflow TEXT NOT NULL,
                process TEXT NOT NULL,
                version TEXT NOT NULL,
                decision_time TEXT NOT NULL,
                decision_epoch REAL NOT NULL,
                completion_time TEXT NOT NULL,
                completion_epoch REAL NOT NULL,
                static_input_bytes INTEGER NOT NULL CHECK (static_input_bytes > 0),
                peak_memory_bytes INTEGER CHECK (
                    peak_memory_bytes IS NULL OR peak_memory_bytes > 0
                ),
                consumed_bytes INTEGER CHECK (
                    consumed_bytes IS NULL OR consumed_bytes >= 0
                ),
                runtime_seconds REAL CHECK (
                    runtime_seconds IS NULL OR runtime_seconds >= 0
                ),
                oom_flag INTEGER NOT NULL CHECK (oom_flag IN (0, 1)),
                system_config_id TEXT NOT NULL,
                source_run_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (completion_epoch >= decision_epoch)
            );
            CREATE TABLE IF NOT EXISTS task_feature_classes (
                task_id TEXT NOT NULL,
                feature_class TEXT NOT NULL,
                PRIMARY KEY (task_id, feature_class),
                FOREIGN KEY (task_id) REFERENCES task_outcomes(task_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_history_completion
                ON task_outcomes(completion_epoch);
            CREATE INDEX IF NOT EXISTS idx_history_exact
                ON task_outcomes(
                    workflow, process, version, system_config_id,
                    completion_epoch
                );
            CREATE INDEX IF NOT EXISTS idx_history_local
                ON task_outcomes(
                    workflow, process, system_config_id, completion_epoch
                );
            CREATE INDEX IF NOT EXISTS idx_history_workflow
                ON task_outcomes(
                    workflow, system_config_id, completion_epoch
                );
            CREATE INDEX IF NOT EXISTS idx_history_global
                ON task_outcomes(system_config_id, completion_epoch);
            CREATE INDEX IF NOT EXISTS idx_history_feature_class
                ON task_feature_classes(feature_class, task_id);
            """
        )
        existing = {
            str(row["name"])
            for row in self.connection.execute(
                "PRAGMA table_info(task_outcomes)"
            ).fetchall()
        }
        migrations = {
            "static_prediction_mb": "REAL",
            "history_prediction_mb": "REAL",
            "allocated_memory_mb": "REAL",
            "model_version": "TEXT NOT NULL DEFAULT ''",
            "canonical_operation": "TEXT NOT NULL DEFAULT ''",
            "metadata_version": "TEXT NOT NULL DEFAULT ''",
            "task_instance": "TEXT NOT NULL DEFAULT ''",
            "input_identity": "TEXT NOT NULL DEFAULT ''",
        }
        for column, declaration in migrations.items():
            if column not in existing:
                self.connection.execute(
                    f"ALTER TABLE task_outcomes ADD COLUMN {column} {declaration}"
                )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_history_operation
            ON task_outcomes(
                canonical_operation, system_config_id, completion_epoch
            )
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_history_instance
            ON task_outcomes(
                workflow, task_instance, system_config_id, completion_epoch
            )
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_history_input_identity
            ON task_outcomes(
                workflow, input_identity, system_config_id, completion_epoch
            )
            """
        )
        self.connection.commit()

    def _backfill_metadata(self) -> None:
        rows = self.connection.execute(
            """
            SELECT task_id, process
            FROM task_outcomes
            WHERE canonical_operation = '' OR metadata_version = ''
            """
        ).fetchall()
        if not rows:
            return
        with self.connection:
            for row in rows:
                metadata = classify_task(str(row["process"]))
                self.connection.execute(
                    """
                    UPDATE task_outcomes
                    SET canonical_operation = ?, metadata_version = ?
                    WHERE task_id = ?
                    """,
                    (
                        metadata.canonical_operation,
                        metadata.registry_version,
                        row["task_id"],
                    ),
                )
                self.connection.execute(
                    "DELETE FROM task_feature_classes WHERE task_id = ?",
                    (row["task_id"],),
                )
                self.connection.executemany(
                    """
                    INSERT INTO task_feature_classes(task_id, feature_class)
                    VALUES (?, ?)
                    """,
                    [
                        (row["task_id"], feature_class)
                        for feature_class in metadata.feature_classes
                    ],
                )

    def record(self, outcome: TaskOutcome) -> None:
        if self.read_only:
            raise RuntimeError("cannot record into a read-only history store")
        with self.connection:
            self._record(outcome)
        self._row_cache.clear()

    def _record(self, outcome: TaskOutcome) -> None:
        if not outcome.task_id.strip():
            raise ValueError("task_id is required")
        identity = outcome.identity()
        if not identity.workflow or not identity.process:
            raise ValueError("workflow and process are required")
        decision_epoch = timestamp_epoch(outcome.decision_time)
        completion_epoch = timestamp_epoch(outcome.completion_time)
        if completion_epoch < decision_epoch:
            raise ValueError("completion_time cannot precede decision_time")
        static_input = _positive_int(
            outcome.static_input_bytes, "static_input_bytes"
        )
        peak = _positive_int(outcome.peak_memory_bytes, "peak_memory_bytes")
        consumed = outcome.consumed_bytes
        if consumed is not None and int(consumed) < 0:
            raise ValueError("consumed_bytes must be non-negative")
        runtime = _finite_float(outcome.runtime_seconds, "runtime_seconds")
        static_prediction = _finite_float(
            outcome.static_prediction_mb, "static_prediction_mb"
        )
        history_prediction = _finite_float(
            outcome.history_prediction_mb, "history_prediction_mb"
        )
        allocation = _finite_float(
            outcome.allocated_memory_mb, "allocated_memory_mb"
        )
        for field, value in (
            ("static_prediction_mb", static_prediction),
            ("history_prediction_mb", history_prediction),
            ("allocated_memory_mb", allocation),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{field} must be positive")
        values = (
            outcome.task_id.strip(),
            identity.workflow,
            identity.process,
            identity.version,
            timestamp_text(outcome.decision_time),
            decision_epoch,
            timestamp_text(outcome.completion_time),
            completion_epoch,
            static_input,
            peak,
            None if consumed is None else int(consumed),
            runtime,
            int(bool(outcome.oom_flag)),
            identity.system_config_id,
            outcome.source_run_id.strip(),
            static_prediction,
            history_prediction,
            allocation,
            outcome.model_version.strip(),
            identity.canonical_operation,
            REGISTRY_VERSION,
            identity.task_instance,
            identity.input_identity,
        )
        self.connection.execute(
            """
            INSERT INTO task_outcomes (
                task_id, workflow, process, version,
                decision_time, decision_epoch, completion_time,
                completion_epoch, static_input_bytes, peak_memory_bytes,
                consumed_bytes, runtime_seconds, oom_flag,
                system_config_id, source_run_id, static_prediction_mb,
                history_prediction_mb, allocated_memory_mb, model_version,
                canonical_operation, metadata_version, task_instance,
                input_identity
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?
            )
            ON CONFLICT(task_id) DO UPDATE SET
                workflow = excluded.workflow,
                process = excluded.process,
                version = excluded.version,
                decision_time = excluded.decision_time,
                decision_epoch = excluded.decision_epoch,
                completion_time = excluded.completion_time,
                completion_epoch = excluded.completion_epoch,
                static_input_bytes = excluded.static_input_bytes,
                peak_memory_bytes = excluded.peak_memory_bytes,
                consumed_bytes = excluded.consumed_bytes,
                runtime_seconds = excluded.runtime_seconds,
                oom_flag = excluded.oom_flag,
                system_config_id = excluded.system_config_id,
                source_run_id = excluded.source_run_id,
                static_prediction_mb = excluded.static_prediction_mb,
                history_prediction_mb = excluded.history_prediction_mb,
                allocated_memory_mb = excluded.allocated_memory_mb,
                model_version = excluded.model_version,
                canonical_operation = excluded.canonical_operation,
                metadata_version = excluded.metadata_version,
                task_instance = excluded.task_instance,
                input_identity = excluded.input_identity,
                recorded_at = CURRENT_TIMESTAMP
            """,
            values,
        )
        self.connection.execute(
            "DELETE FROM task_feature_classes WHERE task_id = ?",
            (outcome.task_id.strip(),),
        )
        self.connection.executemany(
            """
            INSERT INTO task_feature_classes(task_id, feature_class)
            VALUES (?, ?)
            """,
            [
                (outcome.task_id.strip(), feature_class)
                for feature_class in identity.feature_classes
            ],
        )

    def record_many(self, outcomes: Iterable[TaskOutcome]) -> int:
        if self.read_only:
            raise RuntimeError("cannot record into a read-only history store")
        count = 0
        with self.connection:
            for outcome in outcomes:
                self._record(outcome)
                count += 1
        self._row_cache.clear()
        return count

    def count(self, workflow: str | None = None) -> int:
        if workflow is None:
            row = self.connection.execute(
                "SELECT COUNT(*) AS n FROM task_outcomes"
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT COUNT(*) AS n FROM task_outcomes WHERE workflow = ?",
                (workflow,),
            ).fetchone()
        return int(row["n"])

    @staticmethod
    def _scope_predicate(
        scope: str,
        identity: TaskIdentity,
        feature_class: str | None,
    ) -> tuple[str, tuple[str, ...], bool]:
        item = identity.normalized()
        config = item.system_config_id
        if scope == "exact_process":
            return (
                "t.workflow = ? AND t.process = ? AND t.version = ? "
                "AND t.system_config_id = ?",
                (item.workflow, item.process, item.version, config),
                False,
            )
        if scope == "local_process":
            return (
                "t.workflow = ? AND t.process = ? "
                "AND t.system_config_id = ?",
                (item.workflow, item.process, config),
                False,
            )
        if scope == "canonical_operation":
            return (
                "t.canonical_operation = ? AND t.system_config_id = ?",
                (item.canonical_operation, config),
                False,
            )
        if scope == "feature_class":
            if not feature_class:
                raise ValueError("feature_class scope requires a class")
            return (
                "fc.feature_class = ? AND t.system_config_id = ?",
                (feature_class, config),
                True,
            )
        if scope == "workflow":
            return (
                "t.workflow = ? AND t.system_config_id = ?",
                (item.workflow, config),
                False,
            )
        if scope == "global":
            return "t.system_config_id = ?", (config,), False
        raise ValueError(f"unknown history scope: {scope}")

    def _rows_for_scope(
        self,
        scope: str,
        identity: TaskIdentity,
        feature_class: str | None,
        as_of_epoch: float,
        exclude_task_id: str | None,
    ) -> list[sqlite3.Row]:
        predicate, parameters, join_feature = self._scope_predicate(
            scope, identity, feature_class
        )
        feature_join = (
            "JOIN task_feature_classes fc ON fc.task_id = t.task_id"
            if join_feature
            else ""
        )
        exclude_sql = ""
        query_parameters: tuple[object, ...] = (*parameters, as_of_epoch)
        if exclude_task_id:
            exclude_sql = " AND t.task_id != ?"
            query_parameters = (*query_parameters, exclude_task_id)
        cache_key = (
            scope,
            *query_parameters,
            feature_class,
            bool(join_feature),
        )
        cached = self._row_cache.get(cache_key)
        if cached is not None:
            return cached
        rows = self.connection.execute(
            f"""
            SELECT
                t.peak_memory_bytes, t.consumed_bytes, t.static_input_bytes,
                t.runtime_seconds, t.oom_flag, t.static_prediction_mb,
                t.allocated_memory_mb, t.completion_epoch, t.completion_time,
                t.process, t.task_instance, t.input_identity
            FROM task_outcomes t
            {feature_join}
            WHERE {predicate}
              AND t.completion_epoch < ?
              {exclude_sql}
            ORDER BY t.completion_epoch ASC
            """,
            query_parameters,
        ).fetchall()
        self._row_cache[cache_key] = rows
        return rows

    def _summarize_scope(
        self,
        scope: str,
        key: str,
        rows: list[sqlite3.Row],
        as_of_epoch: float,
        current_static_input_bytes: int,
    ) -> HistoryScopeSnapshot:
        completed_count = len(rows)
        consumed_count = sum(
            row["consumed_bytes"] is not None for row in rows
        )
        support_rows = [
            row
            for row in rows
            if row["peak_memory_bytes"] is not None
            and row["consumed_bytes"] is not None
            and row["static_input_bytes"] is not None
            and row["static_input_bytes"] > 0
        ]
        peaks = [
            float(row["peak_memory_bytes"])
            for row in rows
            if row["peak_memory_bytes"] is not None
        ]
        consumed = [
            float(row["consumed_bytes"])
            for row in support_rows
        ]
        inputs = [
            float(row["static_input_bytes"])
            for row in rows
            if row["static_input_bytes"] is not None
            and row["static_input_bytes"] > 0
        ]
        ratios = [
            float(row["consumed_bytes"]) / float(row["static_input_bytes"])
            for row in support_rows
        ]
        memory_to_consumed = [
            float(row["peak_memory_bytes"]) / float(row["consumed_bytes"])
            for row in support_rows
            if row["consumed_bytes"] > 0
        ]
        recent_support_rows = support_rows[-32:]
        recent_peaks = [
            float(row["peak_memory_bytes"]) for row in recent_support_rows
        ]
        recent_consumed_to_input = [
            float(row["consumed_bytes"]) / float(row["static_input_bytes"])
            for row in recent_support_rows
        ]
        recent_memory_to_consumed = [
            float(row["peak_memory_bytes"]) / float(row["consumed_bytes"])
            for row in recent_support_rows
            if row["consumed_bytes"] > 0
        ]
        recent_consumed = [
            float(row["consumed_bytes"])
            for row in recent_support_rows
        ]
        runtimes = [
            float(row["runtime_seconds"])
            for row in rows
            if row["runtime_seconds"] is not None
        ]
        residuals = [
            math.log(
                (float(row["peak_memory_bytes"]) / (1024.0**2))
                / float(row["static_prediction_mb"])
            )
            for row in rows
            if row["peak_memory_bytes"] is not None
            and row["static_prediction_mb"] is not None
            and row["static_prediction_mb"] > 0
        ]
        allocation_failures = [
            float(row["peak_memory_bytes"]) / (1024.0**2)
            > float(row["allocated_memory_mb"])
            for row in rows
            if row["peak_memory_bytes"] is not None
            and row["allocated_memory_mb"] is not None
            and row["allocated_memory_mb"] > 0
        ]
        completion_epochs = [
            float(row["completion_epoch"]) for row in rows
        ]
        consumed_coverage = (
            consumed_count / completed_count if completed_count else 0.0
        )
        support_strength = (
            len(support_rows) / (len(support_rows) + DEFAULT_SUPPORT_PRIOR)
            if support_rows
            else 0.0
        )
        if completion_epochs:
            age_seconds = max(0.0, as_of_epoch - max(completion_epochs))
            recency_score = math.exp(
                -age_seconds / (DEFAULT_RECENCY_DAYS * 86400.0)
            )
            latest_row = max(
                rows, key=lambda row: float(row["completion_epoch"])
            )
            last_completion_time = str(latest_row["completion_time"])
        else:
            recency_score = 0.0
            last_completion_time = None
        residual_mad = _median_absolute_deviation(residuals)
        stability_score = (
            0.5 if residual_mad is None else 1.0 / (1.0 + residual_mad)
        )
        median_input = _order_quantile(inputs, 0.50)
        input_similarity = (
            math.exp(
                -abs(
                    math.log1p(float(current_static_input_bytes))
                    - math.log1p(float(median_input))
                )
            )
            if median_input is not None
            else 0.0
        )
        specificity = SCOPE_SPECIFICITY[scope]
        confidence = (
            support_strength
            * math.sqrt(max(consumed_coverage, 0.0))
            * recency_score
            * stability_score
            * input_similarity
            * specificity
        )

        def integer_quantile(
            values: list[float], probability: float
        ) -> int | None:
            value = _order_quantile(values, probability)
            return None if value is None else int(value)

        return HistoryScopeSnapshot(
            history_scope=scope,
            history_scope_key=key,
            history_confidence=float(np.clip(confidence, 0.0, 1.0)),
            confidence_source="bootstrap_scope_reliability_v1",
            history_completed_count=completed_count,
            history_consumed_data_count=consumed_count,
            history_support_count=len(support_rows),
            history_oom_count=sum(int(row["oom_flag"]) for row in rows),
            consumed_data_coverage=consumed_coverage,
            scope_specificity=specificity,
            recency_score=recency_score,
            stability_score=stability_score,
            input_similarity_score=input_similarity,
            prior_peak_median_bytes=integer_quantile(peaks, 0.50),
            prior_peak_q95_bytes=integer_quantile(peaks, 0.95),
            prior_peak_q99_bytes=integer_quantile(peaks, 0.99),
            prior_consumed_median_bytes=integer_quantile(consumed, 0.50),
            prior_consumed_q95_bytes=integer_quantile(consumed, 0.95),
            prior_consumed_to_input_median=_order_quantile(ratios, 0.50),
            prior_consumed_to_input_q95=_order_quantile(ratios, 0.95),
            prior_runtime_median_seconds=_order_quantile(runtimes, 0.50),
            prior_oom_rate=(
                sum(int(row["oom_flag"]) for row in rows) / completed_count
                if completed_count
                else None
            ),
            prior_static_log_residual_median=_order_quantile(
                residuals, 0.50
            ),
            prior_static_log_residual_q95=_order_quantile(
                residuals, 0.95
            ),
            prior_static_log_residual_mad=residual_mad,
            prior_allocation_failure_rate=(
                sum(allocation_failures) / len(allocation_failures)
                if allocation_failures
                else None
            ),
            last_completion_time=last_completion_time,
            median_input_bytes=(
                None if median_input is None else int(median_input)
            ),
            prior_memory_to_consumed_median=_order_quantile(
                memory_to_consumed, 0.50
            ),
            prior_recent32_peak_median_bytes=integer_quantile(
                recent_peaks, 0.50
            ),
            prior_recent32_consumed_to_input_median=_order_quantile(
                recent_consumed_to_input, 0.50
            ),
            prior_recent32_memory_to_consumed_median=_order_quantile(
                recent_memory_to_consumed, 0.50
            ),
            prior_recent32_consumed_median_bytes=integer_quantile(
                recent_consumed, 0.50
            ),
            prior_recent32_consumed_q95_bytes=integer_quantile(
                recent_consumed, 0.95
            ),
        )

    def _instance_history_values(
        self,
        identity: TaskIdentity,
        exact_rows: list[sqlite3.Row],
        as_of_epoch: float,
        current_static_input_bytes: int,
        exclude_task_id: str | None,
    ) -> dict[str, object]:
        item = identity.normalized()
        exclude_sql = ""
        instance_parameters: list[object] = [
            item.workflow,
            item.task_instance,
            item.system_config_id,
            as_of_epoch,
        ]
        input_parameters: list[object] = [
            item.workflow,
            item.input_identity,
            item.system_config_id,
            as_of_epoch,
        ]
        if exclude_task_id:
            exclude_sql = " AND task_id != ?"
            instance_parameters.append(exclude_task_id)
            input_parameters.append(exclude_task_id)

        instance_rows: list[sqlite3.Row] = []
        if item.task_instance:
            instance_rows = self.connection.execute(
                f"""
                SELECT consumed_bytes, process
                FROM task_outcomes
                WHERE workflow = ?
                  AND task_instance = ?
                  AND system_config_id = ?
                  AND completion_epoch < ?
                  {exclude_sql}
                ORDER BY completion_epoch ASC
                """,
                tuple(instance_parameters),
            ).fetchall()
        input_rows: list[sqlite3.Row] = []
        if item.input_identity:
            input_rows = self.connection.execute(
                f"""
                SELECT consumed_bytes
                FROM task_outcomes
                WHERE workflow = ?
                  AND input_identity = ?
                  AND system_config_id = ?
                  AND completion_epoch < ?
                  {exclude_sql}
                ORDER BY completion_epoch ASC
                """,
                tuple(input_parameters),
            ).fetchall()

        usable = [
            row
            for row in exact_rows
            if row["consumed_bytes"] is not None
            and row["peak_memory_bytes"] is not None
            and row["static_input_bytes"] is not None
            and row["static_input_bytes"] > 0
        ]
        target_log = math.log1p(current_static_input_bytes)
        nearest = sorted(
            usable,
            key=lambda row: abs(
                math.log1p(float(row["static_input_bytes"])) - target_log
            ),
        )[:32]
        distances = [
            abs(math.log1p(float(row["static_input_bytes"])) - target_log)
            for row in nearest
        ]
        nearest_consumed = [
            float(row["consumed_bytes"]) for row in nearest
        ]
        nearest_ratios = [
            float(row["consumed_bytes"]) / float(row["static_input_bytes"])
            for row in nearest
        ]
        nearest_memory_ratios = [
            float(row["peak_memory_bytes"]) / float(row["consumed_bytes"])
            for row in nearest
            if row["consumed_bytes"] > 0
        ]
        instance_consumed = [
            float(row["consumed_bytes"])
            for row in instance_rows
            if row["consumed_bytes"] is not None
        ]
        input_consumed = [
            float(row["consumed_bytes"])
            for row in input_rows
            if row["consumed_bytes"] is not None
        ]
        return {
            "exact_instance_count": len(instance_rows),
            "exact_input_count": len(input_rows),
            "instance_distinct_processes": len(
                {str(row["process"]) for row in instance_rows}
            ),
            "instance_consumed_median_bytes": _integer_quantile(
                instance_consumed, 0.50
            ),
            "input_consumed_median_bytes": _integer_quantile(
                input_consumed, 0.50
            ),
            "nearest32_mean_log_input_distance": (
                None if not distances else float(np.mean(distances))
            ),
            "nearest32_consumed_median_bytes": _integer_quantile(
                nearest_consumed, 0.50
            ),
            "nearest32_consumed_q95_bytes": _integer_quantile(
                nearest_consumed, 0.95
            ),
            "nearest32_consumed_to_input_median": _order_quantile(
                nearest_ratios, 0.50
            ),
            "nearest32_memory_to_consumed_median": _order_quantile(
                nearest_memory_ratios, 0.50
            ),
        }

    def history_context(
        self,
        identity: TaskIdentity,
        as_of_time: str | datetime,
        current_static_input_bytes: int,
        *,
        exclude_task_id: str | None = None,
        most_specific_only: bool = False,
    ) -> HistoryContext:
        if current_static_input_bytes <= 0:
            raise ValueError("current_static_input_bytes must be positive")
        item = identity.normalized()
        as_of_epoch = timestamp_epoch(as_of_time)
        snapshots = []
        exact_rows: list[sqlite3.Row] = []
        for scope, key, feature_class in scope_definitions(item):
            rows = self._rows_for_scope(
                scope,
                item,
                feature_class,
                as_of_epoch,
                exclude_task_id,
            )
            if scope == "exact_process":
                exact_rows = rows
            snapshot = self._summarize_scope(
                scope,
                key,
                rows,
                as_of_epoch,
                current_static_input_bytes,
            )
            snapshots.append(snapshot)
            if (
                most_specific_only
                and snapshot.history_support_count > 0
            ):
                break
        instance_values = self._instance_history_values(
            item,
            exact_rows,
            as_of_epoch,
            current_static_input_bytes,
            exclude_task_id,
        )
        return HistoryContext(
            as_of_time=timestamp_text(as_of_time),
            identity=item,
            scopes=tuple(snapshots),
            **instance_values,
        )
