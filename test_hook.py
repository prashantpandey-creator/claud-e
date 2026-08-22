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
    with tempfile.TemporaryDirectory() as t:
        for c in cases:
            rc, out, raw = fire(c, env=_iso_env(t))  # isolated: edit cases must not
            assert rc == 0, f"exit {rc} on {c}"      # write live presence files
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


def _iso_env(tmp):
    """Isolated sangama dirs so hook tests never touch live presence/store."""
    return {"MEDITATE_COORD_DIR": os.path.join(tmp, "coord"),
            "MEDITATE_STORE_DIR": os.path.join(tmp, "store")}


def test_native_paths_fire_case_insensitively():
    with tempfile.TemporaryDirectory() as t:
        for p in ["/x/App.swift", "/x/Foo.SWIFT", "/x/ios/Thing.m", "/x/IOS/Thing.m"]:
            _, out, _ = fire(edit(p), env=_iso_env(t))
            assert "web app" in context_of(out), f"native rule did not fire for: {p}"


def test_pipeline_paths_fire():
    with tempfile.TemporaryDirectory() as t:
        for p in ["/r/backend/main.py", "/r/query_processor.py", "/r/agents/deep_research.py"]:
            _, out, _ = fire(edit(p), env=_iso_env(t))
            assert "chat pipeline" in context_of(out), f"pipeline rule did not fire for: {p}"


def test_irrelevant_calls_stay_silent():
    with tempfile.TemporaryDirectory() as t:
        for c in [bash("ls -la"), bash("echo hi"), edit("/x/Chat.tsx"), edit("/x/README.md")]:
            _, out, _ = fire(c, env=_iso_env(t))
            assert out == {}, f"expected silence, got {out}"


# ---- sangama through the full hook -----------------------------------------

def _edit_as(sid, path):
    return {"hook_event_name": "PreToolUse", "session_id": sid, "cwd": "/repo",
            "tool_input": {"file_path": path}}


def test_hook_collision_between_two_sessions():
    with tempfile.TemporaryDirectory() as t:
        env = _iso_env(t)
        fire(_edit_as("session-aaaa", "/repo/shared.py"), env=env)
        _, out, _ = fire(_edit_as("session-bbbb", "/repo/shared.py"), env=env)
        ctx = context_of(out)
        assert "SANGAMA" in ctx and "session-" in ctx, f"no collision warning: {ctx!r}"


def test_hook_serves_graded_fact_once():
    with tempfile.TemporaryDirectory() as t:
        env = _iso_env(t)
        os.makedirs(env["MEDITATE_STORE_DIR"], exist_ok=True)
        with open(os.path.join(env["MEDITATE_STORE_DIR"], "path_index.json"), "w") as f:
            json.dump({"/repo/engine.py": [
                {"statement": "engine.py is empty; code lives in main.py",
                 "status": "machine_checked"}]}, f)
        _, out1, _ = fire(_edit_as("sid-x", "/repo/engine.py"), env=env)
        _, out2, _ = fire(_edit_as("sid-x", "/repo/engine.py"), env=env)
        assert "GRADED FACT" in context_of(out1), "fact not served on first edit"
        assert "GRADED FACT" not in context_of(out2), "fact served twice"


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
    # Parse the CHECKPOINT line specifically — the done-digest line also
    # contains "sessions," (archived N sessions) and matched first.
    cp = [l for l in ctx.split("\n") if l.startswith("Meditation checkpoint:")]
    if not cp:
        return  # not overdue, no census emitted
    reported = int(cp[0].split(" sessions,")[0].split()[-1])
    assert reported == expected, f"hook says {reported} sessions, filesystem says {expected}"


def test_bash_calls_refresh_presence_so_a_shell_session_is_not_declared_dead():
    """Presence used to update only on Write/Edit. A session doing shell work
    went stale inside the hour and the timing layer called it away — with the
    session still running, which silenced the companion permanently."""
    import subprocess, time
    sid = "hooktest-" + str(int(time.time()))
    d = os.path.expanduser("~/.claude/coordination/sessions")
    os.makedirs(d, exist_ok=True)
    pf = os.path.join(d, sid + ".json")
    with open(pf, "w") as f:
        f.write('{"sid": "%s", "cwd": "", "files": {}, "served": []}' % sid)
    try:
        os.utime(pf, (1, 1))                      # ancient
        payload = ('{"session_id": "%s", "hook_event_name": "PreToolUse",'
                   ' "tool_input": {"command": "echo hi"}}' % sid)
        subprocess.run(["bash", HOOK], input=payload, capture_output=True,
                       text=True, timeout=20)
        age = time.time() - os.path.getmtime(pf)
        assert age < 30, "a Bash call did not refresh presence (age %.0fs)" % age
    finally:
        try: os.remove(pf)
        except OSError: pass


def test_presence_touch_never_creates_a_file_for_an_unknown_session():
    """Touch-if-exists only — the hook must not litter the coordination dir."""
    import subprocess
    d = os.path.expanduser("~/.claude/coordination/sessions")
    ghost = os.path.join(d, "definitely-not-a-real-session-xyz.json")
    assert not os.path.exists(ghost)
    subprocess.run(["bash", HOOK],
                   input='{"session_id": "definitely-not-a-real-session-xyz",'
                         ' "hook_event_name": "PreToolUse",'
                         ' "tool_input": {"command": "echo hi"}}',
                   capture_output=True, text=True, timeout=20)
    assert not os.path.exists(ghost), "hook invented a presence file"


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
