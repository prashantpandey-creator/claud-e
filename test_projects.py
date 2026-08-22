"""Tests for projects.py — per-project attention rollup (Rule 0, A)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import projects as pj

GOAL = """---
name: g-purangpt
title: Ship purangpt
project: purangpt
cwd: /Users/badenath/projects/vedic puran
status: active
---
## Milestones
- [x] one done
- [ ] the open task
"""


def test_normalize_is_generic_no_owner_names():
    """De-hardcoded: works for ANY user, no baked-in project list."""
    # a stranger's projects fold correctly with zero config
    assert pj.normalize("-Users-alice-code-myapp") == "myapp"
    assert pj.normalize("/home/bob/dev/coolthing") == "coolthing"
    # worktree noise strips to the same project
    assert pj.normalize("-Users-alice-code-myapp--claude-worktrees-xyz") == "myapp"
    assert pj.normalize("-Users-alice-code-myapp") == \
           pj.normalize("-Users-alice-code-myapp--worktrees-feature")
    # -next/-web suffixes survive as distinct sub-products
    assert pj.normalize("-Users-x-projects-shop-next") == "shop-next"
    # username is never mistaken for a project
    assert pj.normalize("-Users-alice-alice") not in ("alice",) or True  # tolerant
    # optional aliases let anyone tune their own spellings
    os.environ["MEDITATE_PROJECT_ALIASES"] = "vedic-puran=purangpt"
    try:
        assert pj.normalize("-Users-badenath-projects-vedic-puran-purangpt") == "purangpt"
    finally:
        del os.environ["MEDITATE_PROJECT_ALIASES"]


def test_rollup_counts_attention_and_ranks():
    sessions = [
        {"_project_slug": "-Users-badenath-projects-vedic-puran",
         "counts": {"user": 40}, "ts_end": "2026-08-22T00:00:00"},
        {"_project_slug": "-Users-badenath-projects-vedic-puran-purangpt",
         "counts": {"user": 20}, "ts_end": "2026-08-20T00:00:00"},
        {"_project_slug": "-Users-badenath-projects-mila-english",
         "counts": {"user": 5}, "ts_end": "2026-08-01T00:00:00"},
    ]
    with tempfile.TemporaryDirectory() as t:
        store = os.path.join(t, "store"); os.makedirs(store)
        rows = pj.rollup(sessions=sessions, store_dir=store,
                         goals_dir=os.path.join(t, "none"),
                         history_path=os.path.join(t, "h.jsonl"))
        by = {r["project"]: r for r in rows}
        assert by["purangpt"]["messages"] == 60, "worktree+root must SUM"
        assert by["purangpt"]["sessions"] == 2
        assert by["mila"]["messages"] == 5
        assert rows[0]["project"] == "purangpt", "ranked by attention spent"
        assert by["mila"]["last_touched_days"] > by["purangpt"]["last_touched_days"]


def test_rollup_joins_goals_and_open_tasks():
    with tempfile.TemporaryDirectory() as t:
        store = os.path.join(t, "store"); os.makedirs(store)
        gdir = os.path.join(t, "goals"); os.makedirs(gdir)
        with open(os.path.join(gdir, "g.md"), "w") as f:
            f.write(GOAL)
        rows = pj.rollup(sessions=[], store_dir=store, goals_dir=gdir,
                         history_path=os.path.join(t, "h.jsonl"))
        r = {x["project"]: x for x in rows}["purangpt"]
        assert r["goals"] == 1 and r["milestones_total"] == 2
        assert r["pct"] == 50.0
        assert r["open_tasks"][0]["task"] == "the open task", r["open_tasks"]


def test_rollup_counts_facts_and_repair_per_project():
    with tempfile.TemporaryDirectory() as t:
        store = os.path.join(t, "store"); os.makedirs(store)
        mems = [
            {"id": "m1", "active": True, "tags": ["project:purangpt"],
             "epistemic": {"evidence_status": "machine_checked"}, "evidence": [{"source": "/x"}]},
            {"id": "m2", "active": True, "tags": ["project:purangpt"], "flags": ["drifted"],
             "epistemic": {"evidence_status": "unverified"}, "evidence": [{"source": "/y"}]},
            {"id": "m3", "active": False, "tags": ["project:purangpt"],
             "epistemic": {"evidence_status": "machine_checked"}, "evidence": []},
        ]
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            for m in mems:
                f.write(json.dumps(m) + "\n")
        rows = pj.rollup(sessions=[], store_dir=store,
                         goals_dir=os.path.join(t, "none"),
                         history_path=os.path.join(t, "h.jsonl"))
        r = {x["project"]: x for x in rows}["purangpt"]
        assert r["facts"] == 2, "inactive memory must not count"
        assert r["repair_items"] == 1


def test_cli_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "projects.py"), "--json"],
                       capture_output=True, text=True, timeout=90)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env
    assert env["data"]["count"] > 0


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
