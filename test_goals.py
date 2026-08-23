"""Tests for goals.py — long-term goal tracking across projects (Rule 0, A).

Contract:
  - a goal is a .md file: frontmatter (name/title/project/cwd/status) +
    '## Milestones' checkboxes. done/total -> percentage. Deterministic.
  - EVOLVING is first-class: every scan snapshots (done,total) to history;
    when total grows, the report shows scope widening (+N) instead of
    silently diluting the percentage.
  - next = first unchecked milestone
  - goal_for_cwd: one-line nudge when a session's cwd sits under a goal's cwd
  - launch kickoff: contains title, next milestones, and the project cwd
  - envelope always; empty world = zeros, never invented numbers

Run: python3 ~/.claude/skills/meditate/test_goals.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import goals as gl

GOAL_MD = """---
name: test-goal
title: Ship the widget
project: widgetco
cwd: /repo/widget
status: evolving
---
Why: the widget matters.

## Milestones
- [x] design locked
- [x] backend built
- [ ] frontend wired
- [ ] shipped to prod
"""


def _world(t, md=GOAL_MD, fname="test-goal.md"):
    gdir = os.path.join(t, "goals"); os.makedirs(gdir, exist_ok=True)
    with open(os.path.join(gdir, fname), "w") as f:
        f.write(md)
    hist = os.path.join(t, "goals-history.jsonl")
    return gdir, hist


def test_parse_and_percentage():
    with tempfile.TemporaryDirectory() as t:
        gdir, hist = _world(t)
        gs = gl.scan(goals_dir=gdir, history_path=hist)
        assert len(gs) == 1
        g = gs[0]
        assert g["name"] == "test-goal"
        assert g["done"] == 2 and g["total"] == 4
        assert abs(g["pct"] - 50.0) < 0.01
        assert g["next"] == "frontend wired"
        assert g["status"] == "evolving"


def test_history_snapshot_and_widening():
    with tempfile.TemporaryDirectory() as t:
        gdir, hist = _world(t)
        gl.scan(goals_dir=gdir, history_path=hist)          # snapshot 2/4
        widened = GOAL_MD.replace("- [ ] shipped to prod",
                                  "- [ ] shipped to prod\n- [ ] android build\n- [ ] marketing page")
        with open(os.path.join(gdir, "test-goal.md"), "w") as f:
            f.write(widened)
        gs = gl.scan(goals_dir=gdir, history_path=hist)     # now 2/6
        g = gs[0]
        assert g["total"] == 6
        assert g["scope_delta"] == 2, f"widening not detected: {g}"
        assert abs(g["pct"] - 33.3) < 0.1                   # honest drop
        rows = [json.loads(l) for l in open(hist)]
        assert len(rows) == 2, "each change must snapshot"


def test_no_history_row_when_unchanged():
    with tempfile.TemporaryDirectory() as t:
        gdir, hist = _world(t)
        gl.scan(goals_dir=gdir, history_path=hist)
        gl.scan(goals_dir=gdir, history_path=hist)
        rows = [l for l in open(hist)]
        assert len(rows) == 1, "unchanged scan must not append history"


def test_goal_for_cwd_match_and_miss():
    with tempfile.TemporaryDirectory() as t:
        gdir, hist = _world(t)
        line = gl.goal_for_cwd("/repo/widget/src/deep", goals_dir=gdir, history_path=hist)
        assert "Ship the widget" in line and "50" in line and "frontend wired" in line
        assert gl.goal_for_cwd("/elsewhere", goals_dir=gdir, history_path=hist) == ""


def test_kickoff_prompt_content():
    with tempfile.TemporaryDirectory() as t:
        gdir, hist = _world(t)
        k = gl.kickoff("test-goal", goals_dir=gdir, history_path=hist)
        assert k is not None
        assert "Ship the widget" in k["prompt"]
        assert "Ship discipline" in k["prompt"], "kickoff must carry the push gate"
        assert "pre-authorized" in k["prompt"]
        assert "frontend wired" in k["prompt"]
        assert k["cwd"] == "/repo/widget"


def test_done_goal_excluded_from_nudge():
    md = GOAL_MD.replace("- [ ] frontend wired", "- [x] frontend wired") \
                .replace("- [ ] shipped to prod", "- [x] shipped to prod")
    with tempfile.TemporaryDirectory() as t:
        gdir, hist = _world(t, md=md)
        assert gl.goal_for_cwd("/repo/widget", goals_dir=gdir, history_path=hist) == "", \
            "a 100% goal must not nudge"


def test_empty_world():
    with tempfile.TemporaryDirectory() as t:
        gdir = os.path.join(t, "goals"); os.makedirs(gdir)
        assert gl.scan(goals_dir=gdir, history_path=os.path.join(t, "h.jsonl")) == []


def test_cli_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "goals.py"), "--json"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env


# ---- ranking and stall: order by need, not by filename --------------------

def _goalfile(d, name, title, done, total):
    body = ["---", "name: %s" % name, "title: %s" % title,
            "project: p", "cwd: /tmp", "status: active", "---", "", "## Milestones"]
    body += ["- [x] m%d" % i for i in range(done)]
    body += ["- [ ] m%d" % i for i in range(done, total)]
    p = os.path.join(d, name + ".md")
    open(p, "w").write("\n".join(body) + "\n")
    return p


def test_goals_are_ordered_by_need_not_by_filename():
    """They came back in sorted(os.listdir()) order, which put a 0% goal with
    payments down beneath three goals that were merely further along."""
    import tempfile, json, time
    with tempfile.TemporaryDirectory() as d:
        gdir = os.path.join(d, "goals"); os.makedirs(gdir)
        _goalfile(gdir, "aaa-early", "Barely started", 1, 10)   # 10%
        _goalfile(gdir, "zzz-nearly", "Nearly done", 9, 10)     # 90%
        rows = gl.scan(goals_dir=gdir, history_path=os.path.join(d, "h.jsonl"))
        assert [r["name"] for r in rows] == ["zzz-nearly", "aaa-early"], \
            "closest to done must come first, not alphabetical"


def test_a_goal_that_stopped_moving_outranks_one_that_is_further_along():
    import tempfile, json, time
    with tempfile.TemporaryDirectory() as d:
        gdir = os.path.join(d, "goals"); os.makedirs(gdir)
        _goalfile(gdir, "stuck", "Stuck goal", 2, 10)
        _goalfile(gdir, "moving", "Moving goal", 9, 10)
        hp = os.path.join(d, "h.jsonl")
        old = time.strftime("%Y-%m-%dT%H:%M:%S",
                            time.localtime(time.time() - 30 * 86400))
        older = time.strftime("%Y-%m-%dT%H:%M:%S",
                              time.localtime(time.time() - 31 * 86400))
        with open(hp, "w") as f:
            f.write(json.dumps({"name": "stuck", "done": 1, "total": 10, "ts": older}) + "\n")
            f.write(json.dumps({"name": "stuck", "done": 2, "total": 10, "ts": old}) + "\n")
        rows = gl.scan(goals_dir=gdir, history_path=hp)
        stuck = [r for r in rows if r["name"] == "stuck"][0]
        assert stuck["stalled"] is True, stuck
        assert stuck["idle_days"] > 25, stuck["idle_days"]
        assert rows[0]["name"] == "stuck", "a stuck goal must surface first"


def test_a_goal_with_no_recorded_movement_is_not_accused_of_stalling():
    """No history is not evidence of neglect."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        gdir = os.path.join(d, "goals"); os.makedirs(gdir)
        _goalfile(gdir, "fresh", "Brand new", 0, 5)
        rows = gl.scan(goals_dir=gdir, history_path=os.path.join(d, "h.jsonl"))
        assert rows[0]["stalled"] is False
        assert rows[0]["idle_days"] is None
        assert "no movement recorded" in rows[0]["idle_basis"]


def test_a_finished_goal_is_never_stuck():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        gdir = os.path.join(d, "goals"); os.makedirs(gdir)
        _goalfile(gdir, "done", "All done", 5, 5)
        rows = gl.scan(goals_dir=gdir, history_path=os.path.join(d, "h.jsonl"))
        assert rows[0]["stalled"] is False


def test_the_kickoff_tells_an_agent_the_facts_exist():
    """Agents were dispatched blind past 465 verified facts about the very
    project they were sent to work on."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        gdir = os.path.join(d, "goals"); os.makedirs(gdir)
        _goalfile(gdir, "g", "A goal", 0, 3)
        k = gl.kickoff("g", goals_dir=gdir, history_path=os.path.join(d, "h.jsonl"))
        assert "meditate recall" in k["prompt"]
        assert "stale" in k["prompt"], "must say what to do when a fact is wrong"


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
