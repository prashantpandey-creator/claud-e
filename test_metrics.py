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


class TestMetricsAreTRUE(unittest.TestCase):
    """The tests above assert fields EXIST. That is why drift.downgrades read
    0 for the tool's whole life while 41 real demotions sat in the journal:
    `assertIn("downgrades", d)` and `assertGreaterEqual(drift_rate, 0)` are
    both satisfied forever by zero. This whole suite would have passed if
    every metric returned 0.

    These assert the NUMBERS, against a journal whose right answer is known.
    """

    def _journal(self, now, spec):
        """spec: [(days_ago, event, detail)] -> journal rows."""
        import datetime as dt
        rows = []
        for days_ago, event, detail in spec:
            ts = (now - dt.timedelta(days=days_ago)).isoformat()
            rows.append({"ts": ts, "event": event, "detail": detail, "id": "m1"})
        return rows

    def _now(self):
        import datetime as dt
        return dt.datetime(2026, 8, 25, 12, 0, tzinfo=dt.timezone.utc)

    def test_a_real_demotion_is_COUNTED_as_a_downgrade(self):
        """The bug, pinned. run_sleep emits sleep.demoted for drift and never
        a sleep.regraded carrying 'machine_checked ->', so counting only the
        latter could not see a single real downgrade. Verified live 2026-08-25
        against nidra: [regraded, completed, DEMOTED, completed]."""
        from metrics import compute_metrics
        now = self._now()
        j = self._journal(now, [(1, "sleep.demoted", "evidence drift: gone"),
                                (1, "sleep.completed", "")])
        d = compute_metrics(journal=j, memories=[{"active": True}], now=now)["drift"]
        self.assertEqual(d["downgrades"], 1, "a real demotion was not counted")
        self.assertGreater(d["drift_rate"], 0, "drift_rate stayed pinned at zero")

    def test_non_drift_downgrades_are_still_counted(self):
        """The other path must not be lost while fixing the first."""
        from metrics import compute_metrics
        now = self._now()
        j = self._journal(now, [(1, "sleep.regraded", "machine_checked -> source_linked")])
        d = compute_metrics(journal=j, memories=[{"active": True}], now=now)["drift"]
        self.assertEqual(d["downgrades"], 1)

    def test_a_clean_journal_reports_zero_downgrades(self):
        """FALSIFIER. The fix must not make everything look broken."""
        from metrics import compute_metrics
        now = self._now()
        j = self._journal(now, [(1, "sleep.regraded", "unverified -> machine_checked"),
                                (1, "sleep.completed", "")])
        d = compute_metrics(journal=j, memories=[{"active": True}], now=now)["drift"]
        self.assertEqual(d["downgrades"], 0)
        self.assertEqual(d["upgrades"], 1)

    def test_trend_can_go_DOWN(self):
        """The property no cumulative counter has, and the reason this exists.

        A total can only rise, so it can never report a regression. A rate
        over a window can fall — that is what makes a change measurable after
        the day it shipped.
        """
        from metrics import compute_metrics
        now = self._now()
        j = self._journal(now, [(20, "sleep.completed", "")]      # baseline exists
                               + [(10, "sleep.completed", "")] * 8
                               + [(2, "sleep.completed", "")] * 3)
        t = compute_metrics(journal=j, memories=[], now=now)["trend"]
        self.assertEqual(t["current"]["sleep.completed"], 3)
        self.assertEqual(t["previous"]["sleep.completed"], 8)
        self.assertEqual(t["delta"]["sleep.completed"], -5, "a fall was not reported as a fall")

    def test_a_lane_that_STOPPED_is_named_dead(self):
        """The Tutor shape: it ran, then silently stopped, and every lifetime
        counter kept its old total and looked healthy for 132 sessions."""
        from metrics import compute_metrics
        now = self._now()
        j = self._journal(now, [(20, "import.meditate", "")]      # baseline exists
                               + [(10, "import.meditate", "")] * 5
                               + [(2, "sleep.completed", "")])
        t = compute_metrics(journal=j, memories=[], now=now)["trend"]
        self.assertIn("import.meditate", t["dead"])
        self.assertNotIn("sleep.completed", t["dead"])

    def test_a_lane_that_never_ran_is_NOT_called_dead(self):
        """FALSIFIER. 'Never started' is not 'stopped'. Reporting silence as
        breakage is the same can't-say-I-don't-know defect this tool keeps
        finding in itself."""
        from metrics import compute_metrics
        now = self._now()
        t = compute_metrics(journal=self._journal(
            now, [(20, "sleep.completed", ""), (2, "sleep.completed", "")]),
            memories=[], now=now)["trend"]
        self.assertEqual(t["dead"], [], "an event that never ran was reported as dead")

    def test_a_window_with_no_baseline_refuses_to_report_a_delta(self):
        """4.5 days of journal cannot support a 7-day-vs-prior-7-day claim.

        Left unguarded, every count in the empty prior window is 0 and each
        delta reads as spectacular growth — an artefact of the window hanging
        off the end of the data, not a measurement. On the live journal today
        that printed "+5130". A missing value is honest; a fake one gets
        quoted back at you a month later.
        """
        from metrics import compute_metrics
        now = self._now()
        j = self._journal(now, [(2, "sleep.completed", "")] * 4)   # 2 days of history
        t = compute_metrics(journal=j, memories=[], now=now)["trend"]
        self.assertFalse(t["comparable"])
        self.assertIsNone(t["delta"], "a delta was reported with no baseline to compare against")
        self.assertEqual(t["dead"], [])

    def test_enough_history_DOES_report_a_delta(self):
        """FALSIFIER for the guard: it must not suppress real comparisons."""
        from metrics import compute_metrics
        now = self._now()
        j = self._journal(now, [(20, "sleep.completed", "")]
                               + [(10, "sleep.completed", "")] * 8
                               + [(2, "sleep.completed", "")] * 3)
        t = compute_metrics(journal=j, memories=[], now=now)["trend"]
        self.assertTrue(t["comparable"])
        self.assertEqual(t["delta"]["sleep.completed"], -5)

    def test_the_live_journal_agrees_with_its_own_event_counts(self):
        """Cross-check the two numbers that disagreed in the real report:
        drift.downgrades printed 0 eleven lines above sleep.demoted: 41."""
        if _fresh.is_fresh():
            self.skipTest("fresh install")
        from metrics import compute_metrics
        m = compute_metrics()
        demoted = m["activity"]["events_by_type"].get("sleep.demoted", 0)
        self.assertGreaterEqual(
            m["drift"]["downgrades"], demoted,
            "downgrades (%d) is below the journal's own sleep.demoted count (%d) — "
            "the report contradicts itself" % (m["drift"]["downgrades"], demoted))


if __name__ == "__main__":
    unittest.main()
