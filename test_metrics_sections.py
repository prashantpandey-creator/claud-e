"""Tests for the four measurement sections added 2026-08-26.

WHY: every number that mattered this session was produced by a throwaway bash
script and then lost. A measurement you have to re-derive by hand is not a
measurement — it is an anecdote you happen to remember. `meditate metrics` is
the one place, so each of these went in:

  serving    — of N active memories, how many can EVER reach a session, and
               how concentrated the hopped ones are (the hub check). Measured
               by hand first: 71 direct (12%), +192 via one hop (46%), and
               before the session-scoped key one fact landed on 13 of 109
               paths.
  warranty   — how much of MEMORY.md an agent could re-check. 271 lines, 144
               world (53%), 127 unwarrantied of which 123 are `internal`.
  agents     — dispatched vs. accounted for. 28 dispatches over 3 days, zero
               outcomes recorded, 22 with no window id and no log.
  assessment — projects tracked vs. projects with a goal. 83 / 75 real / 4
               assessed.

All four are REPORTS, never gates. Nothing here fails a build; a health check
that goes red on a standing property is one you learn to ignore.

Run: python3 ~/.claude/skills/meditate/test_metrics_sections.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metrics  # noqa: E402


def _ledger(tmp, rows):
    p = os.path.join(tmp, "dispatch.jsonl")
    with open(p, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return p


# ---- agents ---------------------------------------------------------------

def test_a_dispatch_with_no_outcome_is_counted_as_unaccounted():
    """The live shape: 28 dispatches, 0 outcomes. The ledger recorded intent
    and called it a record."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _ledger(tmp, [{"goal": "g", "ts": "2026-08-25T10:00:00+00:00"},
                          {"goal": "h", "ts": "2026-08-25T11:00:00+00:00"}])
        a = metrics.agent_stats(p)
        assert a["dispatched"] == 2
        assert a["with_outcome"] == 0
        assert a["unaccounted"] == 2, a


def test_an_outcome_row_is_matched_to_its_dispatch():
    with tempfile.TemporaryDirectory() as tmp:
        p = _ledger(tmp, [
            {"goal": "g", "name": "n1", "ts": "2026-08-25T10:00:00+00:00"},
            {"event": "finished", "name": "n1", "exit": 0,
             "ts": "2026-08-25T10:05:00+00:00"}])
        a = metrics.agent_stats(p)
        assert a["dispatched"] == 1 and a["with_outcome"] == 1
        assert a["unaccounted"] == 0, a


def test_a_failed_agent_is_distinguished_from_a_successful_one():
    with tempfile.TemporaryDirectory() as tmp:
        p = _ledger(tmp, [
            {"goal": "g", "name": "n1", "ts": "2026-08-25T10:00:00+00:00"},
            {"event": "finished", "name": "n1", "exit": 1,
             "ts": "2026-08-25T10:05:00+00:00"}])
        a = metrics.agent_stats(p)
        assert a["failed"] == 1 and a["succeeded"] == 0, a


def test_an_empty_ledger_is_zero_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        a = metrics.agent_stats(os.path.join(tmp, "nope.jsonl"))
        assert a["dispatched"] == 0 and a["unaccounted"] == 0


def test_agent_stats_never_guesses_an_outcome():
    """FALSIFIER. 'No outcome row' means UNKNOWN, not success. Counting a
    silent agent as successful is the exact defect this repo keeps finding."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _ledger(tmp, [{"goal": "g", "name": "n", "ts": "2026-08-25T10:00:00+00:00"}])
        a = metrics.agent_stats(p)
        assert a["succeeded"] == 0, "a silent agent was counted as a success"
        assert a["failed"] == 0, "a silent agent was counted as a failure"
        assert a["unaccounted"] == 1


# ---- serving --------------------------------------------------------------

def test_serving_reports_direct_and_hop_reach():
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "store"); os.makedirs(d)
        mems = [
            {"id": "m1", "statement": "direct", "active": True,
             "epistemic": {"evidence_status": "machine_checked"},
             "evidence": [{"source": "/m/a.md", "locator": "wikilink:[[b]]"}]},
            {"id": "m2", "statement": "hopped", "active": True,
             "epistemic": {"evidence_status": "machine_checked"},
             "evidence": [{"source": "/m/b.md", "locator": "path:/x"}]},
            {"id": "m3", "statement": "unreachable", "active": True,
             "epistemic": {"evidence_status": "machine_checked"},
             "evidence": [{"source": "/m/c.md", "locator": "path:/y"}]},
        ]
        with open(os.path.join(d, "memories.jsonl"), "w") as f:
            for m in mems:
                f.write(json.dumps(m) + "\n")
        with open(os.path.join(d, "path_index.json"), "w") as f:
            json.dump({"/work/a.py": [{"id": "m1", "statement": "direct",
                                       "status": "machine_checked"}]}, f)
        s = metrics.serving_stats(d)
        assert s["active"] == 3
        assert s["direct"] == 1, s
        assert s["one_hop"] == 1, s
        assert s["unreachable"] == 1, s


def test_serving_on_a_missing_store_is_zero_not_an_error():
    s = metrics.serving_stats("/nonexistent/store")
    assert s["active"] == 0 and s["direct"] == 0


# ---- the envelope ---------------------------------------------------------

def test_all_four_sections_are_present_in_compute_metrics():
    d = metrics.compute_metrics(journal=[], memories=[])
    for k in ("serving", "warranty", "agents", "assessment"):
        assert k in d, "missing section: %s" % k


def test_a_section_that_fails_does_not_take_the_report_down():
    """FALSIFIER. metrics is read by the dashboard and the hook. One broken
    sub-measurement must degrade to a marked-unavailable section, never raise
    — the whole report going dark is worse than one number missing."""
    real = metrics.serving_stats
    metrics.serving_stats = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        d = metrics.compute_metrics(journal=[], memories=[])
        assert d["serving"].get("checked") is False, d["serving"]
    finally:
        metrics.serving_stats = real


def test_dispatched_counts_ROWS_not_distinct_goals():
    """The bug that shipped for ten minutes and was caught by disagreement.

    Keying on `goal` collapsed 28 real dispatches into 5, because the same
    goal is dispatched repeatedly. The live report said 5 while a hand count
    of the ledger said 28. Two numbers for one question is always a defect;
    this asserts they agree.
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _ledger(tmp, [{"goal": "same-goal", "ts_epoch": 100 + i,
                           "ts": "2026-08-25T10:00:0%d+00:00" % i}
                          for i in range(4)])
        a = metrics.agent_stats(p)
        assert a["dispatched"] == 4, "repeat dispatches of one goal collapsed: %s" % a


def test_the_live_ledger_row_count_equals_the_reported_count():
    """Self-consistency against the raw file — the check that caught it."""
    import os as _os
    p = _os.path.expanduser("~/.claude/meditation/dispatch.jsonl")
    if not _os.path.exists(p):
        print("       (skipped: no live ledger)")
        return
    rows = sum(1 for l in open(p, errors="replace") if l.strip())
    a = metrics.agent_stats(p)
    assert a["dispatched"] + a["with_outcome"] == rows, \
        "report says %d launches + %d outcomes, file has %d rows" % (
            a["dispatched"], a["with_outcome"], rows)


def test_live_report_has_every_section_populated():
    d = metrics.compute_metrics()
    print("       live: serving=%s warranty=%s agents=%s assessment=%s"
          % (d["serving"].get("direct"), d["warranty"].get("world"),
             d["agents"].get("dispatched"), d["assessment"].get("assessed")))
    for k in ("serving", "warranty", "agents", "assessment"):
        assert isinstance(d[k], dict)


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok   %s" % fn.__name__)
        except AssertionError as e:
            failed += 1; print("  FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:
            failed += 1; print("  ERR  %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
