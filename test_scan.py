#!/usr/bin/env python3
"""Tests for scan_projects — real-fixture-in, envelope-out.

Builds a throwaway workspace on disk (a real git repo, a non-git workspace
folder, and a node_modules trap), runs the scanner against it, and asserts the
envelope shape + the facts it reports. Run:  python3 test_scan.py
Exits 0 on success, 1 on failure.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import scan_projects  # noqa: E402


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t.t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def _build_fixture(base):
    # A real git repo with python + markdown, one commit.
    repo = os.path.join(base, "proj_a")
    os.makedirs(repo)
    with open(os.path.join(repo, "main.py"), "w") as f:
        f.write("print('hi')\n")
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("# proj a\n")
    with open(os.path.join(repo, "CLAUDE.md"), "w") as f:
        f.write("guidance\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial commit")

    # A non-git workspace folder (marker only).
    ws = os.path.join(base, "wsdir")
    os.makedirs(ws)
    with open(os.path.join(ws, "CLAUDE.md"), "w") as f:
        f.write("workspace\n")

    # A node_modules trap that must be pruned (would look like a project).
    trap = os.path.join(base, "node_modules", "foo")
    os.makedirs(trap)
    with open(os.path.join(trap, "package.json"), "w") as f:
        f.write("{}\n")


def main():
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    with tempfile.TemporaryDirectory() as base:
        _build_fixture(base)
        env = scan_projects.scan([base], max_depth=4)

        # --- envelope shape (precondition B) ---
        for key in ("success", "data", "metadata", "errors"):
            check(key in env, f"envelope missing key: {key}")
        check(env["success"] is True, "expected success=True")
        check(isinstance(env["errors"], list), "errors must be a list")
        check(isinstance(env["data"].get("projects"), list), "data.projects must be a list")
        check(env["data"]["count"] == len(env["data"]["projects"]),
              "data.count must equal len(projects)")

        names = {p["name"]: p for p in env["data"]["projects"]}

        # --- discovery ---
        check("proj_a" in names, "did not find the git repo proj_a")
        check("wsdir" in names, "did not find the workspace folder wsdir")
        check("foo" not in names, "node_modules trap was NOT pruned (found 'foo')")

        # --- facts on the git repo ---
        if "proj_a" in names:
            p = names["proj_a"]
            check(p["kind"] == "repo", f"proj_a kind should be repo, got {p['kind']}")
            check(p["is_git"] is True, "proj_a should be is_git=True")
            check(p["branch"] == "main", f"proj_a branch should be main, got {p['branch']}")
            check(p["last_commit"] and p["last_commit"]["subject"] == "initial commit",
                  "proj_a last_commit.subject wrong")
            check(p["last_commit"]["date"], "proj_a last_commit.date missing")
            langs = dict(p["languages"])
            check("py" in langs, f"proj_a languages should include py, got {p['languages']}")
            check("CLAUDE.md" in p["docs"], "proj_a docs should list CLAUDE.md")
            check(isinstance(p["dirty_files"], int), "dirty_files must be int")

        # --- facts on the workspace folder ---
        if "wsdir" in names:
            w = names["wsdir"]
            check(w["kind"] == "workspace", f"wsdir kind should be workspace, got {w['kind']}")
            check(w["is_git"] is False, "wsdir should be is_git=False")

        # --- JSON-serializable (it crosses the tool boundary as text) ---
        try:
            json.dumps(env)
        except (TypeError, ValueError) as e:
            check(False, f"envelope is not JSON-serializable: {e}")

    if failures:
        print("FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS — all assertions green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
