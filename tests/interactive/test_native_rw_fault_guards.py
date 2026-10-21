import copy
import unittest

from sat_sim.interactive.native_rw_fault_checkpoint import (
    capture_fault_checkpoint, validate_fault_checkpoint,
)

S = 1_000_000_000

class RWCommandDropoutContractTest(unittest.TestCase):
    def make(self, now=2*S, start=1_800_000_000, end=3_200_000_000):
        return capture_fault_checkpoint(
            fault_id="rw-motor-drop-1", checkpoint_ns=now,
            start_ns=start, end_ns=end,
        )

    def test_scheduled_active_cleared(self):
        for now, phase in ((2*S, "scheduled"), (2_200_000_000, "active"),
                           (3_400_000_000, "cleared")):
            doc = self.make(now, start=2_200_000_000)
            self.assertEqual(validate_fault_checkpoint(doc)["phase"], phase)
            self.assertFalse(doc["resume_supported"])

    def test_active_at_checkpoint(self):
        self.assertEqual(validate_fault_checkpoint(self.make())["effect_route"],
                         "zero_motor_command")

    def test_digest_tamper_rejected(self):
        doc = self.make()
        doc["start_ns"] = 2_200_000_000
        with self.assertRaisesRegex(ValueError, "DIGEST"):
            validate_fault_checkpoint(doc)

    def test_forged_native_resume_rejected_even_if_rehashed(self):
        from sat_sim.interactive.native_rw_fault_checkpoint import _hash
        doc = self.make()
        doc["resume_supported"] = True
        doc.pop("digest_sha256")
        doc["digest_sha256"] = _hash(doc)
        with self.assertRaisesRegex(ValueError, "FALSE_RESUME"):
            validate_fault_checkpoint(doc)

    def test_invalid_phase_or_clearance_rejected(self):
        from sat_sim.interactive.native_rw_fault_checkpoint import _hash
        doc = self.make()
        doc["cleared_ns"] = 3_200_000_000
        doc.pop("digest_sha256")
        doc["digest_sha256"] = _hash(doc)
        with self.assertRaisesRegex(ValueError, "LIFECYCLE"):
            validate_fault_checkpoint(doc)

    def test_bad_window_and_misaligned_time_rejected(self):
        for start, end in ((2*S, 2*S), (2_100_000_000, 3_200_000_000)):
            with self.assertRaises(ValueError):
                self.make(start=start, end=end)

if __name__ == "__main__":
    unittest.main()
