"""Tests for meditate metrics."""
from __future__ import annotations
import json
import os
import freshcheck as _fresh
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestMetrics(unittest.TestCase):
    def test_compute_returns_all_sections(self):
        from metrics import compute_metrics
        data = compute_metrics()
        for key in ("snapshot", "drift", "consolidation", "coverage", "activity", "timeline"):
            self.assertIn(key, data, f"missing section: {key}")

    def test_snapshot_fields(self):
        from metrics import compute_metrics
        s = compute_metrics()["snapshot"]
        self.assertIn("total_memories", s)
        self.assertIn("active", s)
        self.assertIn("verified_rate", s)
        self.assertIn("avg_confidence", s)
        self.assertGreaterEqual(s["verified_rate"], 0)
        self.assertLessEqual(s["verified_rate"], 1)

    def test_drift_fields(self):
        from metrics import compute_metrics
        d = compute_metrics()["drift"]
        self.assertIn("upgrades", d)
        self.assertIn("downgrades", d)
        self.assertIn("drift_rate", d)
        self.assertGreaterEqual(d["drift_rate"], 0)

    def test_envelope_shape(self):
        from metrics import _envelope
        env = _envelope({"test": True})
        self.assertEqual(env["tool_name"], "meditate_metrics")
        self.assertTrue(env["success"])
        self.assertEqual(env["data"], {"test": True})
        self.assertIsInstance(env["errors"], list)

    def test_bar_rendering(self):
        from metrics import _bar
        self.assertEqual(len(_bar(0.5, 20)), 20)
        self.assertEqual(len(_bar(1.0, 10)), 10)
        self.assertEqual(_bar(0.0, 5), "░░░░░")
        self.assertEqual(_bar(1.0, 5), "█████")

    def test_coverage_positive(self):
        if _fresh.is_fresh():
            self.skipTest("fresh install — nothing graded yet")
        from metrics import compute_metrics
        cov = compute_metrics()["coverage"]
        self.assertGreater(cov["sessions_known"], 0)
        self.assertGreater(cov["total_active"], 0)
        self.assertIn("session_coverage", cov)
        self.assertIn("md_file_coverage", cov)

    def test_consolidation_fields(self):
        from metrics import compute_metrics
        c = compute_metrics()["consolidation"]
        self.assertIn("sleep_runs", c)
        self.assertIn("merges", c)
        self.assertGreaterEqual(c["sleep_runs"], 0)


if __name__ == "__main__":
    unittest.main()
