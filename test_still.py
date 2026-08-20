#!/usr/bin/env python3
"""Tests for still — the yogic diagnosis over scan facts.

Tests the PURE computation (compute) against hand-built project dicts so the
decision tree is deterministic without re-running a real scan. A fixed `today`
is passed so staleness is reproducible. Run:  python3 test_still.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import still  # noqa: E402

TODAY = "2026-06-28"


def _repo(name, branch="main", days_ago=1, dirty=0, langs=None, path=None):
    """Build a scan-shaped project dict with last_commit `days_ago` before TODAY."""
    import datetime
    d = datetime.date.fromisoformat(TODAY) - datetime.timedelta(days=days_ago)
    return {
        "name": name,
        "path": path or f"/x/{name}",
        "kind": "repo",
        "is_git": True,
        "branch": branch,
        "last_commit": {"hash": "abc1234", "date": d.isoformat(), "subject": "work"},
        "dirty_files": dirty,
        "ahead": 0, "behind": 0,
        "languages": langs or [["py", 40], ["md", 5]],
        "docs": ["README.md"], "markers": [".git"],
    }


def _scan_env(projects):
    return {
        "tool_name": "scan_projects", "success": True,
        "data": {"roots": ["/x"], "count": len(projects), "projects": projects},
        "metadata": {}, "errors": [],
    }


def main():
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    # --- vritti classification ---
    check(still.classify_vritti(_repo("a", dirty=5, days_ago=1), TODAY)[0] == "pramana",
          "recent + dirty should be pramana (live)")
    check(still.classify_vritti(_repo("b", days_ago=120, dirty=0), TODAY)[0] == "nidra",
          "120-day-old clean repo should be nidra (dormant)")
    check(still.classify_vritti(_repo("c", days_ago=2, dirty=0,
          langs=[["md", 9], ["txt", 3]]), TODAY)[0] == "vikalpa",
          "recent doc-only/tiny-code repo should be vikalpa (idea)")
    check(still.classify_vritti(_repo("d", days_ago=20, dirty=0), TODAY)[0] == "smriti",
          "recent-ish clean settled repo should be smriti (record)")
    ws = {"name": "w", "path": "/x/w", "kind": "workspace", "is_git": False,
          "branch": None, "last_commit": None, "dirty_files": 0, "ahead": 0,
          "behind": 0, "languages": [["ts", 3]], "docs": [], "markers": ["CLAUDE.md"]}
    check(still.classify_vritti(ws, TODAY)[0] == "container",
          "non-git workspace should be container, not a vritti")

    # --- antaraya diagnosis ---
    projects = [
        _repo("app", branch="feature/x", days_ago=1, dirty=10),
        _repo("app", days_ago=1, dirty=0, path="/y/app"),   # duplicate name
        _repo("app-next", days_ago=2, dirty=3),
        _repo("lonely", days_ago=200, dirty=0),
    ]
    diag = still.diagnose_antarayas(projects)
    alasya_names = {r["name"] for r in diag["alasya"]["repos"]}
    check("app" in alasya_names and "app-next" in alasya_names,
          "alasya (uncommitted) should list dirty repos")
    check(diag["alasya"]["total_dirty"] == 13, "total_dirty should sum dirty files")
    check(any("feature/x" == r["branch"] for r in diag["anavasthitatva"]["repos"]),
          "anavasthitatva should catch off-main branch")
    check(diag["samshaya"]["exact_name_collisions"], "samshaya should flag the two 'app'")

    # --- nirodha index: monotonic (calm > scattered) ---
    calm = [_repo(f"calm{i}", days_ago=3, dirty=0, branch="main") for i in range(3)]
    calm_env = still.compute(_scan_env(calm), TODAY)
    scattered_env = still.compute(_scan_env(projects), TODAY)
    cs = calm_env["data"]["nirodha"]["stillness"]
    ss = scattered_env["data"]["nirodha"]["stillness"]
    check(0 <= ss <= 100 and 0 <= cs <= 100, "stillness must be within 0..100")
    check(cs > ss, f"calm workspace must score higher than scattered ({cs} !> {ss})")
    check(cs >= 90, f"a clean, on-main, recent workspace should be near-still ({cs})")

    # --- envelope shape + serialization ---
    for key in ("success", "data", "metadata", "errors"):
        check(key in scattered_env, f"envelope missing key: {key}")
    d = scattered_env["data"]
    for key in ("projects", "antarayas", "nirodha"):
        check(key in d, f"data missing key: {key}")
    check(all("vritti" in p for p in d["projects"]),
          "every project should carry a 'vritti' classification")
    check("breakdown" in d["nirodha"], "nirodha should expose its scatter breakdown")
    try:
        json.dumps(scattered_env)
    except (TypeError, ValueError) as e:
        check(False, f"envelope not JSON-serializable: {e}")

    if failures:
        print("FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS — all assertions green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
