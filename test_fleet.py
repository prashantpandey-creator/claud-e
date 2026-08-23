"""Tests for fleet — acting on dispatched agents, not just watching them.

Contract:
  - a row with no milestone can never finish, so `clear` drops it
  - a row whose GOAL does not exist can never finish either, so it goes too
  - clearing one goal by name leaves the others alone
  - clearing never touches rows that are still genuinely open
  - the ledger survives being rewritten (no half-written file)

Run: python3 ~/.claude/skills/meditate/test_fleet.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

import fleet as fl

GOAL_MD = """# Ship the thing

- [x] first step
- [ ] second step
"""


def _ledger(tmp, rows):
    p = os.path.join(tmp, "dispatch.jsonl")
    with open(p, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return p


def _goals(tmp, names):
    d = os.path.join(tmp, "goals")
    os.makedirs(d, exist_ok=True)
    for n in names:
        with open(os.path.join(d, n + ".md"), "w") as f:
            f.write(GOAL_MD)
    return d


def test_row_with_no_milestone_is_dropped():
    """It reported '88 minutes, worth a look' forever: a milestone that does
    not exist can never tick, so the row could never resolve on its own."""
    with tempfile.TemporaryDirectory() as t:
        gdir = _goals(t, ["real-goal"])
        p = _ledger(t, [{"goal": "probe-x", "milestone": None},
                        {"goal": "real-goal", "milestone": "second step"}])
        res = fl.clear(ledger_path=p, goals_dir=gdir)
        assert res["cleared"] == 1, res
        left = [json.loads(l) for l in open(p) if l.strip()]
        assert [r["goal"] for r in left] == ["real-goal"], left


def test_row_for_a_goal_that_does_not_exist_is_dropped():
    """The harder case: a real-LOOKING milestone on a goal nobody has. Tests
    wrote 'prove red' against 'probe-fleet-button' into the live ledger, and
    it climbed past 96 minutes with nothing able to close it."""
    with tempfile.TemporaryDirectory() as t:
        gdir = _goals(t, ["real-goal"])
        p = _ledger(t, [{"goal": "probe-fleet-button", "milestone": "prove red"},
                        {"goal": "real-goal", "milestone": "second step"}])
        res = fl.clear(ledger_path=p, goals_dir=gdir)
        assert res["cleared"] == 1 and "probe-fleet-button" in res["goals"], res


def test_open_work_is_never_cleared():
    """The one thing clearing must not do: forget a job that is still running."""
    with tempfile.TemporaryDirectory() as t:
        gdir = _goals(t, ["real-goal"])
        p = _ledger(t, [{"goal": "real-goal", "milestone": "second step"}])
        res = fl.clear(ledger_path=p, goals_dir=gdir)
        assert res["cleared"] == 0 and res["remaining"] == 1, res


def test_clearing_one_goal_leaves_the_others():
    with tempfile.TemporaryDirectory() as t:
        gdir = _goals(t, ["a", "b"])
        p = _ledger(t, [{"goal": "a", "milestone": "second step"},
                        {"goal": "b", "milestone": "second step"}])
        res = fl.clear(goal="a", ledger_path=p, goals_dir=gdir)
        assert res["cleared"] == 1, res
        left = [json.loads(l)["goal"] for l in open(p) if l.strip()]
        assert left == ["b"], left


def test_dead_only_spares_finished_work():
    """--dead is the narrow broom: it takes what can never finish and nothing
    else, so it is safe to run without reading the list first."""
    with tempfile.TemporaryDirectory() as t:
        gdir = _goals(t, ["real-goal"])
        p = _ledger(t, [{"goal": "ghost", "milestone": "nope"},
                        {"goal": "real-goal", "milestone": "first step"}])
        res = fl.clear(dead_only=True, ledger_path=p, goals_dir=gdir)
        assert res["cleared"] == 1 and res["goals"] == ["ghost"], res


def test_ledger_is_still_valid_json_lines_after_rewrite():
    with tempfile.TemporaryDirectory() as t:
        gdir = _goals(t, ["real-goal"])
        p = _ledger(t, [{"goal": "ghost", "milestone": None},
                        {"goal": "real-goal", "milestone": "second step"}])
        fl.clear(ledger_path=p, goals_dir=gdir)
        for line in open(p):
            if line.strip():
                json.loads(line)          # raises if the rewrite tore a line


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1; print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
