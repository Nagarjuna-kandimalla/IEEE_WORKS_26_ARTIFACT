from __future__ import annotations

import inspect
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from design3_apc_online.policy import (
    APCResidualCalibration,
    allocate_apc_only,
)


class APCOnlyPolicyTests(unittest.TestCase):
    def calibration(self) -> APCResidualCalibration:
        return APCResidualCalibration(
            scope_window=512,
            process_min_support=16,
            workflow_min_support=32,
        )

    def test_allocator_has_no_a_prediction_argument(self) -> None:
        parameters = inspect.signature(allocate_apc_only).parameters
        self.assertNotIn("a_prediction", parameters)
        self.assertNotIn("a_request", parameters)
        self.assertNotIn("a_allocation", parameters)

    def test_allocation_uses_only_apc_values(self) -> None:
        result = allocate_apc_only(
            row={"workflow": "wf", "process": "p"},
            values={
                0.5: 40.0,
                0.9: 42.0,
                0.95: 43.0,
                0.99: 44.2,
                0.995: 45.0,
            },
            policy={
                "base_policy": "model_q99",
                "residual_quantile": 0.95,
            },
            calibration=self.calibration(),
            model_scope="global",
        )
        self.assertEqual(result["request_mb"], 45)
        self.assertFalse(result["uses_a_prediction"])

    def test_history_is_strictly_prior(self) -> None:
        calibration = self.calibration()
        row = {"workflow": "wf", "process": "p"}
        before = calibration.correction(row, 0.95)
        calibration.add(row, base_mib=40.0, actual_mib=80.0)
        after = calibration.correction(row, 0.95)
        self.assertEqual(before[0], 0.0)
        self.assertGreater(after[0], 0.0)

    def test_scope_fallback_stays_inside_apc(self) -> None:
        calibration = self.calibration()
        target = {"workflow": "wf", "process": "target"}
        for index in range(32):
            calibration.add(
                {"workflow": "wf", "process": f"other-{index}"},
                base_mib=40.0,
                actual_mib=42.0,
            )
        _, workflow_scope = calibration.correction(target, 0.95)
        self.assertEqual(workflow_scope["scope"], "workflow")
        for _ in range(16):
            calibration.add(target, base_mib=40.0, actual_mib=41.0)
        _, process_scope = calibration.correction(target, 0.95)
        self.assertEqual(process_scope["scope"], "process")

    def test_engine_source_has_no_dual_bound_or_point_stack(self) -> None:
        source = (ROOT / "design3_apc_online/engine.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("max(\\n                int(a_allocation", source)
        self.assertNotIn("point_sources", source)
        self.assertIn("design3_apc_only_warm_calibrated", source)

    def test_engine_requires_version_matched_calibration(self) -> None:
        source = (ROOT / "design3_apc_online/engine.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("version_calibration.tsv", source)
        self.assertIn("calibration/model version mismatch", source)
        self.assertNotIn(
            "self.history_frame.iterrows():\n"
            "            self.apc_residuals.add",
            source,
        )

    def test_online_learner_holds_out_version_calibration(self) -> None:
        source = (ROOT / "scripts/run_online_learner.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("calibration_live = live.tail", source)
        self.assertIn("training_live = live.iloc", source)
        self.assertIn("version_calibration.tsv", source)
        self.assertNotIn('metadata[f"{scope}s"]', source)
        self.assertIn('"process": "processes"', source)
        self.assertIn('"workflow": "workflows"', source)

    def test_p_and_c_feature_contract_is_separated(self) -> None:
        modeling_path = ROOT.parent / "scripts" / "modeling.py"
        sys.path.insert(0, str(modeling_path.parent))
        specification = importlib.util.spec_from_file_location(
            "fresh_design3_modeling",
            modeling_path,
        )
        self.assertIsNotNone(specification)
        self.assertIsNotNone(specification.loader)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        self.assertFalse(
            any("consumed" in column for column in module.NUMERIC_P)
        )
        self.assertTrue(
            any("consumed" in column for column in module.NUMERIC_C)
        )
        self.assertIn("log1p_c_hat_bytes", module.NUMERIC_C)

    def test_offline_apc_is_not_replaced_by_a_point_stack(self) -> None:
        source = (
            ROOT.parent
            / "original_offline_replay"
            / "scripts"
            / "run_design3.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("crossfit_and_freeze_stack", source)
        self.assertIn('"enabled": False', source)

    def test_tail_metadata_plural_keys_are_loadable(self) -> None:
        learner_path = ROOT / "scripts" / "run_online_learner.py"
        specification = importlib.util.spec_from_file_location(
            "fresh_online_learner",
            learner_path,
        )
        self.assertIsNotNone(specification)
        self.assertIsNotNone(specification.loader)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            model_root = Path(directory)
            for scope, key in (
                ("process", "processes"),
                ("workflow", "workflows"),
            ):
                metadata_root = model_root / f"apc_{scope}_tail"
                metadata_root.mkdir()
                (metadata_root / "metadata.json").write_text(
                    json.dumps({key: {"expected": {"scope": scope}}}),
                    encoding="utf-8",
                )
                observed = module.OnlineLearner._tail_metadata(
                    model_root,
                    "apc",
                    scope,
                )
                self.assertEqual(observed["expected"]["scope"], scope)

    def test_live_runner_watches_learner_and_limits_submission_rate(self) -> None:
        config = (ROOT / "configs" / "bowtie2_online.config").read_text(
            encoding="utf-8"
        )
        runner = (ROOT / "slurm" / "run_fresh_bowtie2.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("executor.submitRateLimit = '4/1sec'", config)
        self.assertIn("learner exited while Nextflow was active", runner)

    def test_learner_databases_are_local_then_snapshotted(self) -> None:
        wrapper = (
            ROOT / "slurm" / "run_fresh_learner.slurm"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'local_state_db="/tmp/camp-fresh-learner-${SLURM_JOB_ID}.sqlite3"',
            wrapper,
        )
        self.assertIn(
            'learner_args[$value_index]="$local_state_db"',
            wrapper,
        )
        self.assertIn(
            'local_history_db="/tmp/camp-fresh-learner-${SLURM_JOB_ID}-history.sqlite3"',
            wrapper,
        )
        self.assertIn(
            'learner_args[$value_index]="$local_history_db"',
            wrapper,
        )
        self.assertIn(
            'cp -p "$shared_history_db" "$local_history_db"',
            wrapper,
        )
        self.assertIn(
            'snapshot_one "$local_state_db" "$shared_state_db"',
            wrapper,
        )
        self.assertIn(
            'snapshot_one "$local_history_db" "$shared_history_db"',
            wrapper,
        )
        self.assertNotIn(
            'exec python "$learner_script" "$@"',
            wrapper,
        )

    def test_bowtie_orchestrator_is_cluster_overridable(self) -> None:
        wrapper = (ROOT / "slurm" / "run_fresh_bowtie2.slurm").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("mempred-coordinator", wrapper)
        runner = (ROOT / "slurm" / "run_fresh_bowtie2.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("CAMP_LEARNER_NODELIST", runner)


if __name__ == "__main__":
    unittest.main()
