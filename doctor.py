"""meditate doctor — self-diagnostic for the meditation system.

Checks prerequisites, test suite health, hook registration, STILLNESS.md
freshness, and meditation output state. Returns a JSON envelope.

Run:  python3 ~/.claude/skills/meditate/doctor.py
      python3 ~/.claude/skills/meditate/doctor.py --json
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
HOOK_PATH = os.path.expanduser("~/.claude/hooks/meditate-hook.sh")
HOOK_SRC = os.path.join(SKILL_DIR, "hooks", "meditate-hook.sh")
RETIRED_HOOKS = ("meditate-checkpoint.sh", "rules-inject.sh")
SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")
STILLNESS_PATH = os.path.expanduser("~/.claude/meditation/STILLNESS.md")
MEDITATION_DIR = os.path.expanduser("~/.claude/meditation")
SESSIONS_DIR = os.path.expanduser("~/.claude/meditation/sessions")
MAX_AGE_DAYS = 3

VERSION = open(os.path.join(SKILL_DIR, "VERSION")).read().strip()

# AUTO-DISCOVERED, never hand-maintained. A hand-written list silently lost
# four suites when two sessions edited this file, and dispatch tests sat RED
# for four commits because doctor never ran them. Every test_*.py on disk is
# part of the health check, by construction.
# test_doctor.py calls doctor.run() — including it here makes doctor run a
# suite that runs doctor that runs the suite. Auto-discovery introduced that
# recursion and it showed up as a >120s "red" that was really an infinite
# regress. Self-referential suites are excluded by name, not by luck.
SELF_REFERENTIAL = {"test_doctor.py"}

TEST_FILES = sorted(
    f for f in os.listdir(SKILL_DIR)
    if f.startswith("test_") and f.endswith(".py") and f not in SELF_REFERENTIAL
)


def _envelope(success, data, metadata, errors):
    return {"success": success, "data": data, "metadata": metadata, "errors": errors}


def _check_prereqs() -> List[Dict[str, Any]]:
    checks = []
    checks.append({
        "name": "python3",
        "ok": sys.version_info >= (3, 9),
        "detail": f"Python {sys.version.split()[0]}",
    })
    claude_ok = shutil.which("claude") is not None
    checks.append({
        "name": "claude_code",
        "ok": claude_ok,
        "detail": "found on PATH" if claude_ok else "not found — install Claude Code",
    })
    return checks


def _run_one(tf: str) -> Dict[str, Any]:
    path = os.path.join(SKILL_DIR, tf)
    if not os.path.exists(path):
        return {"file": tf, "ok": False, "detail": "file missing"}
    try:
        r = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, timeout=90, cwd=SKILL_DIR)
        return {"file": tf, "ok": r.returncode == 0,
                "detail": "green" if r.returncode == 0
                else (r.stderr.strip()[-200:] or r.stdout.strip()[-200:])}
    except subprocess.TimeoutExpired:
        return {"file": tf, "ok": False, "detail": "timed out (90s)"}
    except Exception as e:
        return {"file": tf, "ok": False, "detail": str(e)[:200]}


def _check_tests() -> Dict[str, Any]:
    """Run the suites in PARALLEL — they are independent processes on isolated
    temp dirs. Sequentially this took 71s across 27 suites and once timed out a
    commit; wall time is now bounded by the slowest single suite, not the sum.
    """
    from concurrent.futures import ThreadPoolExecutor
    workers = min(8, (os.cpu_count() or 4))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_run_one, TEST_FILES))
    # keep declaration order so the report reads the same every time
    order = {tf: i for i, tf in enumerate(TEST_FILES)}
    results.sort(key=lambda r: order.get(r["file"], 999))
    return {"all_pass": all(r["ok"] for r in results), "files": results}


def _check_hook() -> Dict[str, Any]:
    hook_exists = os.path.isfile(HOOK_PATH)
    hook_executable = os.access(HOOK_PATH, os.X_OK) if hook_exists else False

    # The installed copy must match the repo copy, or the hook has drifted from
    # its source of truth and the next install silently reverts it.
    in_sync = None
    if hook_exists and os.path.isfile(HOOK_SRC):
        with open(HOOK_PATH, "rb") as a, open(HOOK_SRC, "rb") as b:
            in_sync = a.read() == b.read()

    # Check each registration by matcher. Checking only "is a meditate hook
    # present?" reported all_wired=True while a whole matcher was missing.
    registered = {
        "SessionStart": False,
        "PreToolUse:Bash": False,
        "PreToolUse:Write|Edit|MultiEdit": False,
    }
    stale = []
    if os.path.isfile(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH) as fh:
                settings = json.load(fh)
            hooks = settings.get("hooks", {})
            for event, entries in hooks.items():
                for entry in entries:
                    for h in entry.get("hooks", []):
                        cmd = h.get("command", "")
                        for old in RETIRED_HOOKS:
                            if old in cmd:
                                stale.append(f"{event}: {old}")
                        if "meditate-hook.sh" not in cmd:
                            continue
                        matcher = entry.get("matcher")
                        key = event if not matcher else f"{event}:{matcher}"
                        if key in registered:
                            registered[key] = True
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    return {
        "hook_exists": hook_exists,
        "hook_executable": hook_executable,
        "in_sync_with_repo": in_sync,
        "registered": registered,
        "stale_registrations": stale,
        "all_wired": all(registered.values()) and not stale and in_sync is not False,
    }


def _check_heartbeat() -> Dict[str, Any]:
    """The metabolism: grade must run WITHOUT a human. launchd every 6h."""
    plist = os.path.expanduser("~/Library/LaunchAgents/com.meditate.grade.plist")
    exists = os.path.isfile(plist)
    loaded = False
    if exists:
        try:
            r = subprocess.run(["launchctl", "list"], capture_output=True,
                               text=True, timeout=10)
            loaded = "com.meditate.grade" in r.stdout
        except Exception:
            pass
    log = os.path.expanduser("~/.claude/meditation/heartbeat.log")
    last_beat = None
    if os.path.exists(log):
        last_beat = time.strftime("%Y-%m-%d %H:%M",
                                  time.localtime(os.path.getmtime(log)))
    return {"plist_exists": exists, "loaded": loaded, "last_beat": last_beat}


def _check_stillness() -> Dict[str, Any]:
    if not os.path.isfile(STILLNESS_PATH):
        # A new install has never run a stilling pass. "Overdue" says the
        # user is behind on something they have not started, and a fresh
        # install that reports a problem teaches people to ignore the report.
        import freshcheck as _fresh
        never_used = _fresh.is_fresh()
        return {"exists": False, "age_days": None,
                "overdue": not never_used,
                "never_run": never_used}
    mtime = os.path.getmtime(STILLNESS_PATH)
    age_days = (time.time() - mtime) / 86400
    return {
        "exists": True,
        "age_days": round(age_days, 1),
        "overdue": age_days > MAX_AGE_DAYS,
        "last_modified": time.strftime("%Y-%m-%d", time.localtime(mtime)),
    }


def _check_output() -> Dict[str, Any]:
    med_exists = os.path.isdir(MEDITATION_DIR)
    sess_exists = os.path.isdir(SESSIONS_DIR)
    session_dirs = 0
    continuation_chats = 0
    if sess_exists:
        for entry in os.listdir(SESSIONS_DIR):
            p = os.path.join(SESSIONS_DIR, entry)
            if os.path.isdir(p):
                session_dirs += 1
                for f in os.listdir(p):
                    if f.endswith(".md") and f != "INDEX.md":
                        continuation_chats += 1
    return {
        "meditation_dir": med_exists,
        "sessions_dir": sess_exists,
        "session_dirs": session_dirs,
        "continuation_chats": continuation_chats,
    }


NIDRA_STORE_DIR = os.path.expanduser("~/.claude/meditation/nidra_store")


def _check_nidra() -> Dict[str, Any]:
    mem_path = os.path.join(NIDRA_STORE_DIR, "memories.jsonl")
    if not os.path.exists(mem_path):
        return {"connected": False, "total": 0, "active": 0, "by_status": {}}
    active = 0
    statuses: Dict[str, int] = {}
    total = 0
    try:
        with open(mem_path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                total += 1
                m = json.loads(line)
                if not m.get("active"):
                    continue
                active += 1
                s = m.get("epistemic", {}).get("evidence_status", "unverified")
                statuses[s] = statuses.get(s, 0) + 1
    except Exception:
        return {"connected": False, "total": 0, "active": 0, "by_status": {}}
    return {"connected": True, "total": total, "active": active, "by_status": statuses}


def run(run_tests: bool = True) -> Dict[str, Any]:
    """run_tests=False returns the same envelope without executing 26 suites.
    test_doctor.py calls run() three times to check STRUCTURE; making it
    actually run every suite each time turned one test file into a
    multi-minute job (and, with auto-discovery, a recursion)."""
    prereqs = _check_prereqs()
    tests = _check_tests() if run_tests else {
        "all_pass": True, "files": [], "skipped": "run_tests=False"}
    hook = _check_hook()
    heartbeat = _check_heartbeat()
    stillness = _check_stillness()
    output = _check_output()
    nidra = _check_nidra()

    issues = []
    if not all(p["ok"] for p in prereqs):
        issues.append("prerequisites")
    if not tests["all_pass"]:
        issues.append("tests")
    if not hook["all_wired"]:
        issues.append("hook_registration")
    if not heartbeat["loaded"]:
        issues.append("heartbeat_not_loaded")
    if stillness["overdue"]:
        issues.append("stillness_overdue")
    elif stillness.get("never_run"):
        pass          # new install, nothing owed yet

    healthy = len(issues) == 0
    data = {
        "version": VERSION,
        "healthy": healthy,
        "issues": issues,
        "prereqs": prereqs,
        "tests": tests,
        "hook": hook,
        "heartbeat": heartbeat,
        "stillness": stillness,
        "output": output,
        "nidra": nidra,
    }
    return _envelope(True, data, {"skill_dir": SKILL_DIR}, [])


def main(argv: List[str]) -> int:
    env = run()
    d = env["data"]

    if "--json" in argv:
        print(json.dumps(env, indent=2))
        return 0 if d["healthy"] else 1

    print(f"meditate doctor v{d['version']}")
    print("=" * 40)

    print("\nPrerequisites:")
    for p in d["prereqs"]:
        mark = "ok" if p["ok"] else "MISSING"
        print(f"  [{mark:>7}]  {p['name']:15} {p['detail']}")

    print(f"\nTests: {'all green' if d['tests']['all_pass'] else 'FAILURES'}")
    for t in d["tests"]["files"]:
        mark = "ok" if t["ok"] else "FAIL"
        print(f"  [{mark:>7}]  {t['file']:25} {t['detail']}")

    print(f"\nHook:")
    h = d["hook"]
    print(f"  [{'ok' if h['hook_exists'] else 'MISSING':>7}]  hook file exists")
    print(f"  [{'ok' if h['hook_executable'] else 'MISSING':>7}]  hook executable")
    if h["in_sync_with_repo"] is not None:
        print(f"  [{'ok' if h['in_sync_with_repo'] else 'DRIFTED':>7}]  matches repo source")
    for ev, wired in h["registered"].items():
        print(f"  [{'ok' if wired else 'MISSING':>7}]  {ev}")
    for s in h.get("stale_registrations", []):
        print(f"  [ STALE ]  retired hook still wired — {s}")

    hb = d.get("heartbeat", {})
    print(f"\nHeartbeat:")
    print(f"  [{'ok' if hb.get('loaded') else 'MISSING':>7}]  launchd com.meditate.grade "
          f"(last beat: {hb.get('last_beat') or 'never'})")

    print(f"\nStillness:")
    if d["stillness"]["exists"]:
        age = d["stillness"]["age_days"]
        status = "OVERDUE" if d["stillness"]["overdue"] else "ok"
        print(f"  [{status:>7}]  STILLNESS.md — {age:.1f} days old (threshold: {MAX_AGE_DAYS}d)")
    else:
        print(f"  [MISSING]  STILLNESS.md — run /meditate to create")

    print(f"\nOutput:")
    o = d["output"]
    print(f"  meditation dir: {'exists' if o['meditation_dir'] else 'missing'}")
    print(f"  session dirs: {o['session_dirs']}")
    print(f"  continuation chats: {o['continuation_chats']}")

    n = d.get("nidra", {})
    if n.get("connected"):
        print(f"\nNidra store:")
        print(f"  total: {n['total']}  active: {n['active']}")
        for s, c in sorted(n.get("by_status", {}).items()):
            print(f"    {s}: {c}")
    else:
        print(f"\nNidra store: not connected (run nidra_bridge.py --sleep)")

    if d["healthy"]:
        print(f"\n{'=' * 40}")
        print("Healthy. All systems nominal.")
    else:
        print(f"\n{'=' * 40}")
        print(f"Issues: {', '.join(d['issues'])}")

    return 0 if d["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
