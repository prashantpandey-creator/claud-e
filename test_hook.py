"""Tests for hooks/meditate-hook.sh (Rule 0, precondition A).

The hook had no test when it was written. These are the assertions that would
have caught the three defects the audit found:
  - prefilter narrower than the branches (docker-compose --build, .SWIFT)
  - set -e + unguarded find/du dropping ALL rules on a missing projects dir
  - find without -maxdepth reporting 670 sessions where metrics.py says 91

Run: python3 ~/.claude/skills/meditate/test_hook.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks", "meditate-hook.sh")


def fire(payload, env=None):
    """Run the hook with a JSON payload. Returns (returncode, parsed_stdout)."""
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(
        ["bash", HOOK],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=20, env=e,
    )
    try:
        out = json.loads(r.stdout.strip().splitlines()[0]) if r.stdout.strip() else None
    except (json.JSONDecodeError, IndexError):
        out = None
    return r.returncode, out, r.stdout


def context_of(out):
    if not out or "hookSpecificOutput" not in out:
        return ""
    return out["hookSpecificOutput"].get("additionalContext", "")


def bash(cmd):
    return {"hook_event_name": "PreToolUse", "tool_input": {"command": cmd}}


def edit(path):
    return {"hook_event_name": "PreToolUse", "tool_input": {"file_path": path}}


# ---- contract: always exit 0, always valid JSON ----------------------------

def test_always_exits_zero_and_emits_json():
    cases = [
        {}, {"hook_event_name": "SessionStart"}, bash("ls"), edit("/tmp/x.txt"),
        {"hook_event_name": "PreToolUse"}, {"hook_event_name": "Stop"},
        {"hook_event_name": "PreToolUse", "tool_input": None},
        {"hook_event_name": "PreToolUse", "tool_input": {"command": 'git push "a\nb" ünïcode'}},
    ]
    for c in cases:
        rc, out, raw = fire(c)
        assert rc == 0, f"exit {rc} on {c}"
        assert out is not None, f"non-JSON stdout {raw!r} on {c}"


def test_malformed_stdin_is_survivable():
    r = subprocess.run(["bash", HOOK], input="not json at all",
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0
    assert json.loads(r.stdout.strip().splitlines()[0]) == {}


def test_empty_stdin_is_survivable():
    r = subprocess.run(["bash", HOOK], input="", capture_output=True, text=True, timeout=20)
    assert r.returncode == 0
    assert json.loads(r.stdout.strip().splitlines()[0]) == {}


# ---- the prefilter must not be narrower than the branches ------------------

def test_deploy_variants_all_fire():
    """Every spelling the DEPLOY branch claims to catch must survive the prefilter."""
    for cmd in [
        "docker compose up -d --build frontend",
        "docker-compose up -d --build frontend",   # hyphenated — used to return {}
        "npm run build -- --build",                 # bare --build — used to return {}
        "./deploy.sh",
        "DOCKER COMPOSE UP",                        # case — used to return {}
    ]:
        _, out, _ = fire(bash(cmd))
        assert "RULE" in context_of(out), f"deploy rule did not fire for: {cmd}"


def test_git_variants_all_fire():
    for cmd in ["git commit -m x", "git push origin main", "GIT PUSH origin main"]:
        _, out, _ = fire(bash(cmd))
        assert "LOCAL branch" in context_of(out), f"git rule did not fire for: {cmd}"


def test_native_paths_fire_case_insensitively():
    for p in ["/x/App.swift", "/x/Foo.SWIFT", "/x/ios/Thing.m", "/x/IOS/Thing.m"]:
        _, out, _ = fire(edit(p))
        assert "web app" in context_of(out), f"native rule did not fire for: {p}"


def test_pipeline_paths_fire():
    for p in ["/r/backend/main.py", "/r/query_processor.py", "/r/agents/deep_research.py"]:
        _, out, _ = fire(edit(p))
        assert "chat pipeline" in context_of(out), f"pipeline rule did not fire for: {p}"


def test_irrelevant_calls_stay_silent():
    for c in [bash("ls -la"), bash("echo hi"), edit("/x/Chat.tsx"), edit("/x/README.md")]:
        _, out, _ = fire(c)
        assert out == {}, f"expected silence, got {out}"


# ---- SessionStart must never lose the rules -------------------------------

def test_session_start_carries_all_seven_rules():
    _, out, _ = fire({"hook_event_name": "SessionStart"})
    ctx = context_of(out)
    for n in range(1, 8):
        assert f"\n{n}. " in ctx, f"rule {n} missing from SessionStart output"


def test_session_start_survives_missing_projects_dir():
    """set -e + unguarded find used to kill the hook and drop ALL rules."""
    with tempfile.TemporaryDirectory() as td:
        _, out, raw = fire({"hook_event_name": "SessionStart"}, env={"HOME": td})
        ctx = context_of(out)
        assert "OPERATING RULES" in ctx, f"rules lost on empty HOME: {raw!r}"


def test_session_count_matches_maxdepth_two():
    """The hook and metrics.py must agree on what a 'session' is."""
    projects = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(projects):
        return
    expected = sum(
        1 for slug in os.listdir(projects)
        if os.path.isdir(os.path.join(projects, slug))
        for f in os.listdir(os.path.join(projects, slug))
        if f.endswith(".jsonl")
    )
    _, out, _ = fire({"hook_event_name": "SessionStart"})
    ctx = context_of(out)
    if "sessions," not in ctx:
        return  # not overdue, no census emitted
    reported = int(ctx.split(" sessions,")[0].split()[-1])
    assert reported == expected, f"hook says {reported} sessions, filesystem says {expected}"


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
