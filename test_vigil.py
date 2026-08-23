"""Tests for vigil.py — the absence gate (Rule 0, precondition A).

Every test runs against a TEMPORARY ledger and an INJECTED presence reader.
Never the live ledger, never the real Mac: a test that needs someone to walk
away from a keyboard is not a test.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vigil


def _present():
    return {"idle_s": 5.0, "away": False, "why": "idle 0 min"}


def _away():
    return {"idle_s": 3600.0, "away": True, "why": "idle 60 min"}


def _unreadable():
    return {"idle_s": None, "away": False, "why": "idle time unreadable"}


def test_holds_while_someone_is_here():
    with tempfile.TemporaryDirectory() as d:
        led = os.path.join(d, "v.jsonl")
        got = vigil.decide(led, presence_fn=_present)
        assert got["run"] is False, got
        assert got["budget"] == 0
        assert "here" in got["reason"]


def test_runs_when_away():
    with tempfile.TemporaryDirectory() as d:
        led = os.path.join(d, "v.jsonl")
        got = vigil.decide(led, presence_fn=_away)
        assert got["run"] is True, got
        assert got["budget"] == vigil.MAX_AGENTS_PER_NIGHT


def test_unreadable_presence_fails_CLOSED():
    """THE falsifier. If we cannot tell whether the owner is here, we must
    assume they ARE. Guessing 'away' dispatches agents onto files under
    someone's hands — the collision the whole sangama layer prevents.
    Unknown is not away: the three-valued rule, applied to a person."""
    with tempfile.TemporaryDirectory() as d:
        led = os.path.join(d, "v.jsonl")
        got = vigil.decide(led, presence_fn=_unreadable)
        assert got["run"] is False, got


def test_nightly_cap_is_enforced_and_reported():
    """A cap that is hit silently reads as 'there was nothing to do'."""
    with tempfile.TemporaryDirectory() as d:
        led = os.path.join(d, "v.jsonl")
        vigil.record("dispatched", led, count=vigil.MAX_AGENTS_PER_NIGHT)
        got = vigil.decide(led, presence_fn=_away)
        assert got["run"] is False
        assert got["budget"] == 0
        assert "cap" in got["reason"], got["reason"]


def test_budget_shrinks_as_the_night_is_spent():
    with tempfile.TemporaryDirectory() as d:
        led = os.path.join(d, "v.jsonl")
        vigil.record("dispatched", led, count=3)
        got = vigil.decide(led, presence_fn=_away)
        assert got["budget"] == vigil.MAX_AGENTS_PER_NIGHT - 3, got


def test_yesterdays_spend_does_not_count_against_tonight():
    with tempfile.TemporaryDirectory() as d:
        led = os.path.join(d, "v.jsonl")
        with open(led, "w") as f:
            f.write(json.dumps({"event": "dispatched", "count": 99,
                                "ts": time.time() - 48 * 3600}) + "\n")
        assert vigil.spent_tonight(led) == 0
        assert vigil.decide(led, presence_fn=_away)["budget"] == vigil.MAX_AGENTS_PER_NIGHT


def test_digest_covers_only_since_the_last_return():
    """Coming back twice must not re-report the first night's work."""
    with tempfile.TemporaryDirectory() as d:
        led = os.path.join(d, "v.jsonl")
        vigil.record("dispatched", led, count=2, what=["old work"])
        vigil.record("returned", led)
        vigil.record("dispatched", led, count=1, what=["new work"])
        rows = vigil.since_last_present(led)
        blob = json.dumps(rows)
        assert "new work" in blob and "old work" not in blob, rows


def test_digest_is_empty_when_nothing_ran():
    with tempfile.TemporaryDirectory() as d:
        assert vigil.since_last_present(os.path.join(d, "none.jsonl")) == []


def test_dry_run_never_dispatches():
    """A dry run that launched an agent would be the worst possible bug."""
    calls = []
    with tempfile.TemporaryDirectory() as d:
        led = os.path.join(d, "v.jsonl")
        env = vigil.run(dry=True, ledger_path=led, presence_fn=_away,
                        launcher=lambda *a, **k: calls.append(a) or True)
        assert env["success"]
        assert calls == [], "dry run dispatched"
        assert not os.path.exists(led) or "dispatched" not in open(led).read()


def test_envelope_shape():
    with tempfile.TemporaryDirectory() as d:
        env = vigil.run(dry=True, ledger_path=os.path.join(d, "v.jsonl"),
                        presence_fn=_present)
        for k in ("tool_name", "success", "data", "metadata", "errors"):
            assert k in env, "envelope missing " + k
        json.dumps(env)


def test_wake_schedule_is_reported_not_assumed():
    """Measured: this Mac sleeps and the journal has a 9-hour hole. Promising
    overnight work without a wake schedule is promising a plan nobody runs."""
    with tempfile.TemporaryDirectory() as d:
        env = vigil.run(dry=True, ledger_path=os.path.join(d, "v.jsonl"),
                        presence_fn=_away)
        assert "wake_scheduled" in env["data"]
        assert isinstance(env["data"]["wake_scheduled"], bool)


def test_does_not_touch_the_live_ledger():
    live = vigil.NIGHT_LEDGER
    before = os.path.getmtime(live) if os.path.exists(live) else None
    with tempfile.TemporaryDirectory() as d:
        vigil.run(dry=True, ledger_path=os.path.join(d, "v.jsonl"), presence_fn=_away)
    after = os.path.getmtime(live) if os.path.exists(live) else None
    assert before == after, "vigil tests wrote to the LIVE ledger"


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("  ok   %s" % fn.__name__)
        except AssertionError as e:
            failed += 1
            print("  FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:
            failed += 1
            print("  ERR  %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
