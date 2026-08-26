"""Tests for go.autosplit — split a session before the ceiling, unasked.

WHY: coordination.ceiling_check() has been MEASURING the live context and then
telling the human to go run `/meditate <sid>` themselves. A nudge is not a
mechanism. It fired repeatedly during a 672k-token session and the split
happened only because someone read the line and typed the command.

The split is safe to automate because it does not touch the live session: it
reads the transcript, groups threads, and writes continuation .md files
beside it. Nothing is modified, nothing is deleted, and if it never runs the
only cost is the thing that already happens — a compaction summary flattening
the threads.

AND IT RECORDS ITS OUTCOME. Measured 2026-08-26: 28 dispatches, ZERO outcome
rows, 22 leaving no trace at all. Adding a 29th blind dispatch would be
building the defect I had just finished measuring, so autosplit writes a
`finished` row with an exit code, and `metrics.agent_stats` picks it up.

Run: python3 ~/.claude/skills/meditate/test_autosplit.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import go  # noqa: E402


def _transcript(tmp, ctx_tokens, sid="abcd1234-dead-beef-0000-000000000000"):
    """A transcript whose LAST usage row reports ctx_tokens of context."""
    d = os.path.join(tmp, "projects", "-tmp-proj")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, sid + ".jsonl")
    with open(p, "w") as f:
        f.write(json.dumps({"cwd": "/tmp/proj"}) + "\n")
        f.write(json.dumps({"message": {"usage": {
            "input_tokens": ctx_tokens, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0}}}) + "\n")
    return p


def _ledger(tmp):
    return os.path.join(tmp, "dispatch.jsonl")


def _rows(p):
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p) if l.strip()]


# ---------------------------------------------------------------------------

def test_a_session_under_the_band_is_left_alone():
    """FALSIFIER, and the one that matters most. Splitting early is worse than
    not splitting: it spends an agent and writes files nobody asked for."""
    with tempfile.TemporaryDirectory() as tmp:
        _transcript(tmp, 120_000)
        led = _ledger(tmp)
        r = go.autosplit(projects_root=os.path.join(tmp, "projects"),
                         ledger_path=led, runner=lambda *a, **k: 0)
        assert r["split"] == [], r
        assert _rows(led) == [], "a dispatch was recorded for a session under the band"


def test_a_session_OVER_the_band_is_split():
    with tempfile.TemporaryDirectory() as tmp:
        _transcript(tmp, 700_000)
        led = _ledger(tmp)
        calls = []
        r = go.autosplit(projects_root=os.path.join(tmp, "projects"),
                         ledger_path=led,
                         runner=lambda cwd, prompt, name, **k: calls.append(name) or 0)
        assert len(r["split"]) == 1, r
        assert len(calls) == 1, calls


def test_the_prompt_names_the_session_to_split():
    with tempfile.TemporaryDirectory() as tmp:
        _transcript(tmp, 700_000)
        seen = {}
        go.autosplit(projects_root=os.path.join(tmp, "projects"),
                     ledger_path=_ledger(tmp),
                     runner=lambda cwd, prompt, name, **k: seen.update(
                         prompt=prompt, cwd=cwd) or 0)
        assert "abcd1234" in seen["prompt"], seen["prompt"][:200]
        assert "meditate" in seen["prompt"].lower()


def test_the_same_session_is_never_split_TWICE():
    """The debounce. A hook or heartbeat that re-fires must not spawn a second
    agent for work already done."""
    with tempfile.TemporaryDirectory() as tmp:
        _transcript(tmp, 700_000)
        led, calls = _ledger(tmp), []
        kw = dict(projects_root=os.path.join(tmp, "projects"), ledger_path=led,
                  runner=lambda *a, **k: calls.append(1) or 0)
        go.autosplit(**kw)
        go.autosplit(**kw)
        assert len(calls) == 1, "the same session was split twice"


def test_the_OUTCOME_is_recorded_not_just_the_launch():
    """28 dispatches, 0 outcomes was the measured state. A 29th blind dispatch
    would be building the defect I had just finished measuring."""
    with tempfile.TemporaryDirectory() as tmp:
        _transcript(tmp, 700_000)
        led = _ledger(tmp)
        go.autosplit(projects_root=os.path.join(tmp, "projects"),
                     ledger_path=led, runner=lambda *a, **k: 0)
        rows = _rows(led)
        assert len(rows) == 2, rows
        fin = [r for r in rows if r.get("event") == "finished"]
        assert len(fin) == 1 and fin[0]["exit"] == 0, rows
        assert fin[0]["name"] == rows[0]["name"], "outcome not matched to its launch"


def test_a_FAILED_split_records_a_nonzero_exit():
    with tempfile.TemporaryDirectory() as tmp:
        _transcript(tmp, 700_000)
        led = _ledger(tmp)
        go.autosplit(projects_root=os.path.join(tmp, "projects"),
                     ledger_path=led, runner=lambda *a, **k: 3)
        fin = [r for r in _rows(led) if r.get("event") == "finished"]
        assert fin and fin[0]["exit"] == 3, _rows(led)


def test_a_CRASHING_runner_still_records_an_outcome():
    """A dispatch that raises must not vanish. Silence is the failure mode."""
    with tempfile.TemporaryDirectory() as tmp:
        _transcript(tmp, 700_000)
        led = _ledger(tmp)
        def boom(*a, **k):
            raise RuntimeError("launcher died")
        go.autosplit(projects_root=os.path.join(tmp, "projects"),
                     ledger_path=led, runner=boom)
        fin = [r for r in _rows(led) if r.get("event") == "finished"]
        assert fin and fin[0]["exit"] != 0, _rows(led)


def test_metrics_can_read_what_autosplit_wrote():
    """The two halves must actually meet — a format nobody reads is no record."""
    import metrics
    with tempfile.TemporaryDirectory() as tmp:
        _transcript(tmp, 700_000)
        led = _ledger(tmp)
        go.autosplit(projects_root=os.path.join(tmp, "projects"),
                     ledger_path=led, runner=lambda *a, **k: 0)
        a = metrics.agent_stats(led)
        assert a["dispatched"] == 1 and a["with_outcome"] == 1, a
        assert a["succeeded"] == 1 and a["unaccounted"] == 0, a


def test_an_unreadable_transcript_is_silent_not_split():
    """Unmeasurable is not over-the-ceiling. Splitting on a number you could
    not read is the can't-say-I-don't-know defect again."""
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "projects", "-tmp-proj"); os.makedirs(d)
        open(os.path.join(d, "sid-no-usage.jsonl"), "w").write("{}\n")
        r = go.autosplit(projects_root=os.path.join(tmp, "projects"),
                         ledger_path=_ledger(tmp), runner=lambda *a, **k: 0)
        assert r["split"] == [], r


def test_a_DEAD_session_is_not_split():
    """From the dry run, not imagination: 16 of the 21 transcripts over the
    floor were more than 24h old. Splitting a session that already ended costs
    an agent and delivers nothing — the value is splitting a LIVE one before
    it compacts."""
    import time as _t
    with tempfile.TemporaryDirectory() as tmp:
        p = _transcript(tmp, 700_000)
        old = _t.time() - 48 * 3600
        os.utime(p, (old, old))
        r = go.autosplit(projects_root=os.path.join(tmp, "projects"),
                         ledger_path=_ledger(tmp), runner=lambda *a, **k: 0)
        assert r["split"] == [], "a 48h-dead session was split"


def test_the_per_run_cap_stops_a_stampede():
    """Unguarded, the first live dry run would have spawned 21 headless agents
    in one pass."""
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(5):
            _transcript(tmp, 700_000, sid="sid-%d-aaaa-bbbb-cccc-dddddddddddd" % i)
        calls = []
        go.autosplit(projects_root=os.path.join(tmp, "projects"),
                     ledger_path=_ledger(tmp), max_splits=2,
                     runner=lambda *a, **k: calls.append(1) or 0)
        assert len(calls) == 2, "the cap did not hold: %d agents" % len(calls)


def test_a_missing_projects_root_is_empty_not_an_error():
    r = go.autosplit(projects_root="/nonexistent/xyz",
                     ledger_path="/nonexistent/l.jsonl", runner=lambda *a, **k: 0)
    assert r["split"] == []


def test_it_never_runs_during_tests_without_an_injected_runner():
    """MEDITATE_TESTING is set by doctor; a real `claude -p` must never fire
    from a suite. Four side effects have leaked into the owner's real state
    this way already."""
    with tempfile.TemporaryDirectory() as tmp:
        _transcript(tmp, 700_000)
        os.environ["MEDITATE_TESTING"] = "1"
        try:
            r = go.autosplit(projects_root=os.path.join(tmp, "projects"),
                             ledger_path=_ledger(tmp))
            assert r["split"] == [], "a live agent would have been dispatched from a test"
        finally:
            os.environ.pop("MEDITATE_TESTING", None)


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
