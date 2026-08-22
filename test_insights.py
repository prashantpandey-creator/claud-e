"""Tests for insights.py — patterns that emerge from the data (Rule 0, A).

Contract: insights() joins live sessions + goals + fleet + repair into
patterns the flat lists hide — clustered by project, a one-line headline of
what is happening, and what needs the human vs what is moving itself. All
derived, no invented numbers; empty world -> empty patterns + honest headline.
"""
from __future__ import annotations

import os
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import insights as ins


def _state(live=None, goals=None, fleet=None, repair=None):
    return {"live_sessions": live or [], "goals": goals or [],
            "fleet": fleet or [], "repair": repair or []}


def test_clusters_by_project():
    s = _state(
        live=[{"cwd": "/x/purangpt", "label": "astro", "age_s": 30},
              {"cwd": "/x/purangpt", "label": "chat", "age_s": 60},
              {"cwd": "/x/mila", "label": "voice", "age_s": 90}],
        goals=[{"title": "PuranGPT mobile", "cwd": "/x/purangpt", "pct": 38,
                "next": "iOS subs", "name": "pg"}],
    )
    d = ins.insights(s)
    byp = {p["project"]: p for p in d["projects"]}
    assert byp["purangpt"]["live"] == 2
    assert byp["mila"]["live"] == 1
    assert byp["purangpt"]["goals"] and byp["purangpt"]["goals"][0]["pct"] == 38


def test_headline_names_busiest_project():
    s = _state(live=[{"cwd": "/x/purangpt", "label": "a", "age_s": 10},
                     {"cwd": "/x/purangpt", "label": "b", "age_s": 20},
                     {"cwd": "/x/mila", "label": "c", "age_s": 30}])
    d = ins.insights(s)
    assert "purangpt" in d["headline"] and "3" in d["headline"]


def test_attention_vs_moving():
    s = _state(
        repair=[{"id": "m1", "statement": "stale", "fails": ["path:/gone"]}],
        goals=[{"title": "G", "cwd": "/x/p", "pct": 50, "next": "step", "name": "g"}],
        fleet=[{"goal": "g", "says": "pushing backend", "says_done": False}],
    )
    d = ins.insights(s)
    assert any("repair" in a.lower() for a in d["needs_you"]), d["needs_you"]
    assert any("pushing backend" in m for m in d["moving"]), d["moving"]


def test_empty_world_honest():
    d = ins.insights(_state())
    assert d["projects"] == []
    assert "quiet" in d["headline"].lower() or "nothing" in d["headline"].lower()


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
