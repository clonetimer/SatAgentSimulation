"""Safety and canonicality checks for an experimental mode journal."""
import copy
import unittest

from sat_sim.interactive.native_mode_checkpoint import (
    capture_mode_checkpoint, validate_mode_checkpoint,
)


class NativeModeCheckpointContractTest(unittest.TestCase):
    def setUp(self):
        self.event = {
            "event_id": "tc-1", "revision": 1,
            "requested_ns": 600_000_000, "resolved_ns": 1_200_000_000,
            "from_mode": "standby", "to_mode": "inertial3d", "status": "applied",
        }

    def test_roundtrip(self):
        doc = capture_mode_checkpoint(
            checkpoint_ns=2_000_000_000, current_mode="inertial3d",
            transitions=[self.event],
        )
        self.assertFalse(validate_mode_checkpoint(doc)["resume_supported"])

    def test_pending_does_not_advance_mode(self):
        pending = dict(self.event, resolved_ns=None, status="pending")
        doc = capture_mode_checkpoint(
            checkpoint_ns=2_000_000_000, current_mode="standby",
            transitions=[pending],
        )
        self.assertEqual(validate_mode_checkpoint(doc)["current_mode"], "standby")

    def test_forged_resume_rejected(self):
        doc = capture_mode_checkpoint(
            checkpoint_ns=2_000_000_000, current_mode="inertial3d",
            transitions=[self.event],
        )
        doc["resume_supported"] = True
        with self.assertRaises(ValueError):
            validate_mode_checkpoint(doc)

    def test_wrong_physical_state_rejected(self):
        with self.assertRaisesRegex(ValueError, "CURRENT_MISMATCH"):
            capture_mode_checkpoint(
                checkpoint_ns=2_000_000_000, current_mode="standby",
                transitions=[self.event],
            )

    def test_off_grid_transition_rejected(self):
        bad = dict(self.event, resolved_ns=1_300_000_000)
        with self.assertRaisesRegex(ValueError, "PHASE"):
            capture_mode_checkpoint(
                checkpoint_ns=2_000_000_000, current_mode="inertial3d",
                transitions=[bad],
            )

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            capture_mode_checkpoint(
                checkpoint_ns=2_000_000_000, current_mode="inertial3d",
                transitions=[self.event, dict(self.event, revision=2)],
            )


if __name__ == "__main__":
    unittest.main()
