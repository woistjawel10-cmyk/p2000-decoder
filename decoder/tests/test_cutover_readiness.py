import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cutover_readiness import assess_readiness


class TestCutoverReadiness(unittest.TestCase):
    def test_all_quality_gates_must_pass(self):
        result = assess_readiness(
            {"expected": 250, "recall": 0.996, "precision": 1.0},
            {"sdr_connected": True, "decoder_dropped_chunks": 0, "decoder_last_error": None},
        )
        self.assertTrue(result["ready"])

    def test_fails_closed_on_insufficient_evidence_and_health_errors(self):
        result = assess_readiness(
            {"expected": 83, "recall": 1.0, "precision": 1.0},
            {"sdr_connected": True, "decoder_dropped_chunks": 1, "decoder_last_error": "boom"},
        )
        self.assertFalse(result["ready"])
        self.assertEqual(len(result["reasons"]), 3)

    def test_rejects_below_quality_thresholds(self):
        result = assess_readiness(
            {"expected": 250, "recall": 0.98, "precision": 0.998},
            {"sdr_connected": True, "decoder_dropped_chunks": 0, "decoder_last_error": None},
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("recall" in reason for reason in result["reasons"]))
        self.assertTrue(any("precisie" in reason for reason in result["reasons"]))


if __name__ == "__main__":
    unittest.main()
