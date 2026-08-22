"""Tests for coordination.py — the sangama (confluence) layer.

Contract under test:
  - presence: every edit records (session, cwd, file, ts); stale sessions ignored
  - collision: session B editing a file session A touched recently gets ONE calm
    warning naming A and the age; same-session re-edits warn nothing
  - facts: editing a path with machine_checked claims serves them ONCE per
    session (capped), never unverified claims
  - session-start: census + drift (journal downgrades) + live-session summary
  - hook-edit must ALWAYS print valid JSON and exit 0, even on garbage stdin

Run: python3 ~/.claude/skills/meditate/test_coordination.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import coordination as co


def _env(tmp):
    """Isolated dirs for one test."""
    coord = os.path.join(tmp, "coord")
    store = os.path.join(tmp, "store")
    os.makedirs(coord, exist_ok=True)
    os.makedirs(store, exist_ok=True)
    return coord, store


def _payload(sid, path, cwd="/repo", event="PreToolUse", tool="Edit"):
    return {"session_id": sid, "cwd": cwd, "hook_event_name": event,
            "tool_name": tool, "tool_input": {"file_path": path}}


# ---- presence ---------------------------------------------------------------

def test_edit_records_presence():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-a", "/repo/x.py"), coord_dir=coord, store_dir=store)
        p = co.load_presence("sid-a", coord_dir=coord)
        assert p["cwd"] == "/repo"
        assert "/repo/x.py" in p["files"]


def test_stale_session_ignored():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-old", "/repo/x.py"), coord_dir=coord, store_dir=store)
        # age the file beyond the live window
        f = os.path.join(coord, "sid-old.json")
        old = time.time() - co.LIVE_WINDOW - 60
        os.utime(f, (old, old))
        msg = co.hook_edit(_payload("sid-b", "/repo/x.py"), coord_dir=coord, store_dir=store)
        assert "sid-old" not in msg, "stale session must not trigger a collision"


# ---- collision --------------------------------------------------------------

def test_collision_warns_second_session():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-a", "/repo/shared.py"), coord_dir=coord, store_dir=store)
        msg = co.hook_edit(_payload("sid-b", "/repo/shared.py"), coord_dir=coord, store_dir=store)
        assert "sid-a"[:8] in msg and "shared.py" in msg, f"no collision warning: {msg!r}"


def test_same_session_no_collision():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-a", "/repo/x.py"), coord_dir=coord, store_dir=store)
        msg = co.hook_edit(_payload("sid-a", "/repo/x.py"), coord_dir=coord, store_dir=store)
        assert "touched" not in msg, "a session must not collide with itself"


def test_collision_warns_only_once():
    """Repeat edits of a contested file must not re-warn — that is pressure."""
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-a", "/repo/shared.py"), coord_dir=coord, store_dir=store)
        first = co.hook_edit(_payload("sid-b", "/repo/shared.py"), coord_dir=coord, store_dir=store)
        second = co.hook_edit(_payload("sid-b", "/repo/shared.py"), coord_dir=coord, store_dir=store)
        assert "SANGAMA" in first
        assert "SANGAMA" not in second, f"collision re-warned: {second!r}"


def test_different_files_no_collision():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-a", "/repo/a.py"), coord_dir=coord, store_dir=store)
        msg = co.hook_edit(_payload("sid-b", "/repo/b.py"), coord_dir=coord, store_dir=store)
        assert "touched" not in msg


# ---- fact serving -----------------------------------------------------------

def _write_index(store, path, entries):
    with open(os.path.join(store, "path_index.json"), "w") as f:
        json.dump({path: entries}, f)


def test_facts_served_for_known_path():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        _write_index(store, "/repo/main.py",
                     [{"statement": "PROMPTS registry lives in main.py", "status": "machine_checked"}])
        msg = co.hook_edit(_payload("sid-a", "/repo/main.py"), coord_dir=coord, store_dir=store)
        assert "PROMPTS registry" in msg, f"fact not served: {msg!r}"


def test_facts_served_once_per_session():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        _write_index(store, "/repo/main.py",
                     [{"statement": "PROMPTS registry lives in main.py", "status": "machine_checked"}])
        co.hook_edit(_payload("sid-a", "/repo/main.py"), coord_dir=coord, store_dir=store)
        msg2 = co.hook_edit(_payload("sid-a", "/repo/main.py"), coord_dir=coord, store_dir=store)
        assert "PROMPTS registry" not in msg2, "fact must serve once per session per file"


def test_unverified_facts_never_served():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        _write_index(store, "/repo/main.py",
                     [{"statement": "stale claim", "status": "unverified"}])
        msg = co.hook_edit(_payload("sid-a", "/repo/main.py"), coord_dir=coord, store_dir=store)
        assert "stale claim" not in msg


def test_facts_capped():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        _write_index(store, "/repo/main.py",
                     [{"statement": f"fact {i}", "status": "machine_checked"} for i in range(9)])
        msg = co.hook_edit(_payload("sid-a", "/repo/main.py"), coord_dir=coord, store_dir=store)
        assert sum(1 for i in range(9) if f"fact {i}" in msg) <= co.FACT_CAP


def test_events_logged_durably():
    """Serves and warns must land in events.jsonl — the report reads it."""
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        _write_index(store, "/repo/main.py",
                     [{"statement": "a graded fact", "status": "machine_checked"}])
        co.hook_edit(_payload("sid-a", "/repo/main.py"), coord_dir=coord, store_dir=store)
        co.hook_edit(_payload("sid-b", "/repo/main.py"), coord_dir=coord, store_dir=store)
        ev_path = os.path.join(os.path.dirname(coord.rstrip("/")), "events.jsonl")
        assert os.path.exists(ev_path), "events.jsonl not written"
        types = [json.loads(l)["type"] for l in open(ev_path)]
        assert "fact_served" in types and "collision_warned" in types, types


# ---- guard rules (moved from bash) ------------------------------------------

def test_pipeline_rule_fires():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        msg = co.hook_edit(_payload("s", "/r/backend/main.py"), coord_dir=coord, store_dir=store)
        assert "chat pipeline" in msg


def test_native_rule_fires_case_insensitive():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        for p in ("/x/App.swift", "/x/Foo.SWIFT", "/x/ios/Thing.m"):
            msg = co.hook_edit(_payload("s", p), coord_dir=coord, store_dir=store)
            assert "web app" in msg, p


# ---- session-start ----------------------------------------------------------

def test_session_start_reports_drift():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        now = co._iso(time.time())
        with open(os.path.join(store, "journal.jsonl"), "w") as f:
            f.write(json.dumps({"event": "sleep.regraded", "id": "mem_x",
                                "detail": "machine_checked -> unverified (path missing: /gone.py)",
                                "ts": now}) + "\n")
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            f.write(json.dumps({"id": "mem_x", "active": True, "statement": "s",
                                "epistemic": {"evidence_status": "unverified"}}) + "\n")
        out = co.session_start({"session_id": "s", "cwd": "/repo"},
                               coord_dir=coord, store_dir=store)
        assert "drift" in out.lower() and "mem_x" in out, f"drift not reported: {out!r}"


def test_session_start_reports_live_sessions():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-other", "/repo/x.py", cwd="/repo"), coord_dir=coord, store_dir=store)
        out = co.session_start({"session_id": "sid-me", "cwd": "/repo"},
                               coord_dir=coord, store_dir=store)
        assert "1 other live session" in out, f"presence not reported: {out!r}"


def test_session_start_nudges_repair_queue():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        # queue lives beside the store dir (parent = meditation dir)
        with open(os.path.join(os.path.dirname(store), "repair-queue.md"), "w") as f:
            f.write("# Repair queue\n")
        out = co.session_start({"session_id": "s", "cwd": "/repo"},
                               coord_dir=coord, store_dir=store)
        assert "Repair queue" in out, f"queue nudge missing: {out!r}"


def test_done_digest_reports_silent_work():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        now = co._iso(time.time())
        with open(os.path.join(store, "journal.jsonl"), "w") as f:
            f.write(json.dumps({"event": "sleep.completed", "actions": 3,
                                "contested": 0, "ts": now}) + "\n")
            f.write(json.dumps({"event": "formation.commit_facts", "formed": 5,
                                "ts": now}) + "\n")
        out = co.session_start({"session_id": "s", "cwd": "/repo"},
                               coord_dir=coord, store_dir=store)
        assert "Done silently" in out and "formed 5" in out and "graded 1x" in out, out


def test_done_digest_empty_day_is_silent():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        assert co.done_digest(store_dir=store, coord_dir=coord) == ""


def test_session_start_quiet_when_alone_and_clean():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        out = co.session_start({"session_id": "s", "cwd": "/repo"},
                               coord_dir=coord, store_dir=store)
        assert out == "", f"expected silence, got {out!r}"


# ---- CLI robustness ---------------------------------------------------------

def test_cli_hook_edit_survives_garbage():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "coordination.py"), "hook-edit"],
                       input="not json", capture_output=True, text=True, timeout=15)
    assert r.returncode == 0
    json.loads(r.stdout.strip())  # must be valid JSON


def test_cli_drift_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "coordination.py"), "drift", "--json"],
                       capture_output=True, text=True, timeout=15)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env


def test_cli_who_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "coordination.py"), "who", "--json"],
                       capture_output=True, text=True, timeout=15)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    assert env["success"] is True


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
