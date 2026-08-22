from __future__ import annotations

import inspect
import math
import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
CAMP_SCRIPTS = ROOT.parent / "scripts"
sys.path.insert(0, str(CAMP_SCRIPTS))
sys.path.insert(0, str(ROOT))

import common
from calibration import ResidualIndex
from camp_online.policy import allocate_memory


common.CONFIG_PATH = (
    ROOT.parent
    / "original_offline_replay"
    / "config"
    / "experiment.json"
)


class CampPolicyTests(unittest.TestCase):
    def calibration(self) -> ResidualIndex:
        return ResidualIndex(
            nearest_k=48,
            scope_window=512,
        )

    @staticmethod
    def row(
        *,
        workflow: str = "wf",
        process: str = "p",
        task: str = "task",
    ) -> dict[str, str]:
        return {
            "workflow": workflow,
            "process": process,
            "version": "v1",
            "system_config_id": "system",
            "task_instance": task,
            "split_group_id": task,
            "input_identity": task,
        }

    def test_allocator_has_no_a_prediction_argument(self) -> None:
        parameters = inspect.signature(allocate_memory).parameters
        self.assertNotIn("a_prediction", parameters)
        self.assertNotIn("a_request", parameters)
        self.assertNotIn("a_allocation", parameters)

    def test_allocation_uses_only_camp_values(self) -> None:
        result = allocate_memory(
            row=self.row(),
            vector=np.asarray([0.0]),
            values={
                0.5: 40.0,
                0.9: 42.0,
                0.95: 43.0,
                0.99: 44.2,
                0.995: 45.0,
            },
            policy={
                "base_policy": "model_q99",
                "mode": "hierarchical",
                "residual_quantile": 0.95,
            },
            calibration=self.calibration(),
            model_scope="global",
        )
        self.assertEqual(result["request_mb"], 45)
        self.assertFalse(result["uses_a_prediction"])

    def test_history_is_strictly_prior(self) -> None:
        calibration = self.calibration()
        row = self.row()
        vector = np.asarray([0.0])
        before, _ = calibration.corrections(
            row,
            vector,
            [0.95],
            mode="hierarchical",
        )
        calibration.add(row, vector, math.log(80.0 / 40.0))
        after, _ = calibration.corrections(
            row,
            vector,
            [0.95],
            mode="hierarchical",
        )
        self.assertEqual(before[0.95], 0.0)
        self.assertGreater(after[0.95], 0.0)

    def test_rq2_i0_i5_scope_selection_stays_inside_camp(self) -> None:
        calibration = self.calibration()
        target = self.row(process="target", task="target")
        vector = np.asarray([0.0])
        for index in range(32):
            calibration.add(
                self.row(process=f"other-{index}", task=f"other-{index}"),
                vector,
                math.log(42.0 / 40.0),
            )
        _, workflow_scope = calibration.corrections(
            target,
            vector,
            [0.95],
            mode="hierarchical",
        )
        self.assertEqual(workflow_scope["calibration_scope"], "I4")
        for index in range(16):
            calibration.add(
                self.row(process="target", task=f"target-{index}"),
                vector,
                math.log(41.0 / 40.0),
            )
        _, same_context_scope = calibration.corrections(
            target,
            vector,
            [0.95],
            mode="hierarchical",
        )
        self.assertEqual(same_context_scope["calibration_scope"], "I2")

    def test_engine_source_has_no_dual_bound_or_point_stack(self) -> None:
        source = (ROOT / "camp_online/engine.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("max(\\n                int(a_allocation", source)
        self.assertNotIn("point_sources", source)
        self.assertNotIn("a_plus_p_plus_c_workflow_tail", source)
        self.assertNotIn("_workflow_group", source)
        self.assertIn('model_scope = "global"', source)
        self.assertIn('model_scope = "process"', source)
        self.assertIn("camp_warm_calibrated", source)

    def test_engine_requires_version_matched_calibration(self) -> None:
        source = (ROOT / "camp_online/engine.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("version_calibration.tsv", source)
        self.assertIn("calibration/model version mismatch", source)
        self.assertIn("from calibration import ResidualIndex", source)
        self.assertNotIn("ResidualCalibration", source)
        self.assertNotIn(
            "self.history_frame.iterrows():\n"
            "            self.camp_residuals.add",
            source,
        )

    def test_live_residual_calibration_is_the_rq2_implementation(self) -> None:
        rq2 = (
            ROOT.parents[3] / "RQ2" / "calibration.py"
        ).read_bytes()
        live_support = (
            ROOT.parent / "scripts" / "calibration.py"
        ).read_bytes()
        self.assertEqual(live_support, rq2)

    def test_online_learner_holds_out_version_calibration(self) -> None:
        source = (ROOT / "scripts/run_online_learner.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("calibration_live = live.tail", source)
        self.assertIn("training_live = live.iloc", source)
        self.assertIn("version_calibration.tsv", source)
        self.assertIn("_process_tail_metadata", source)
        self.assertNotIn("_workflow_tail", source)
        self.assertNotIn('"workflows"', source)

    def test_p_and_c_feature_contract_is_separated(self) -> None:
        modeling_path = ROOT.parent / "scripts" / "modeling.py"
        source = modeling_path.read_text(encoding="utf-8")
        self.assertIn('if "consumed" not in column', source)
        self.assertIn('if "consumed" in column', source)
        self.assertIn('("log1p_c_hat_bytes",)', source)

    def test_offline_camp_is_not_replaced_by_a_point_stack(self) -> None:
        source = (
            ROOT.parent
            / "original_offline_replay"
            / "scripts"
            / "run_camp.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("crossfit_and_freeze_stack", source)
        self.assertIn('"enabled": False', source)

    def test_online_learner_has_only_process_tail_metadata(self) -> None:
        source = (
            ROOT / "scripts" / "run_online_learner.py"
        ).read_text(encoding="utf-8")
        self.assertIn('return json.load(handle)["processes"]', source)
        self.assertNotIn("workflow_tail", source)

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
