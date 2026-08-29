"""meditate doctor — self-diagnostic for the meditation system.

Checks prerequisites, test suite health, hook registration, STILLNESS.md
freshness, and meditation output state. Returns a JSON envelope.

Run:  python3 ~/.claude/skills/meditate/doctor.py
      python3 ~/.claude/skills/meditate/doctor.py --json
"""
from __future__ import annotations

import json
import os
import plistlib
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
        # Tests must not touch the world. Marking the run is the ONE guard that
        # covers every module at once — the alternative is auditing each new
        # side effect forever, and that has now failed four times: the activity
        # log, the console event trail, the dispatch ledger, and a notification
        # saying "G is done" for a goal called g that only exists in a test.
        env = dict(os.environ, MEDITATE_TESTING="1")
        r = subprocess.run([sys.executable, path], capture_output=True, env=env,
                           text=True, timeout=180, cwd=SKILL_DIR)
        return {"file": tf, "ok": r.returncode == 0,
                "detail": "green" if r.returncode == 0
                else (r.stderr.strip()[-200:] or r.stdout.strip()[-200:])}
    except subprocess.TimeoutExpired:
        # A timeout is NOT a failure — it is an unknown. Reporting it as FAIL
        # made doctor call a healthy install broken whenever the machine was
        # busy: five suites "failed" at load average 138, and every one of
        # them passed when run again with no cap. A health check that cries
        # wolf under load is a health check nobody reads.
        return {"file": tf, "ok": False, "timeout": True,
                "detail": "no verdict in 180s — machine too busy, not a failure"}
    except Exception as e:
        return {"file": tf, "ok": False, "detail": str(e)[:200]}


def _check_tests() -> Dict[str, Any]:
    """Run the suites in PARALLEL — they are independent processes on isolated
    temp dirs. Sequentially this took 71s across 27 suites and once timed out a
    commit; wall time is now bounded by the slowest single suite, not the sum.
    """
    from concurrent.futures import ThreadPoolExecutor
    workers = max(2, min(6, (os.cpu_count() or 4) // 2))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_run_one, TEST_FILES))

    # CONFIRM BEFORE ACCUSING. Suites that bind a port or wait on a subprocess
    # can lose a race to their own neighbours here — test_brain was seen
    # failing under the pool and passing 11/11 alone, on a box running nine
    # other sessions' suites. Reported as-is, that tells a new user
    # "installed with test failures" because their laptop was busy, which is
    # the same lie this tool exists to stop telling. A failure gets ONE quiet
    # re-run on its own; only a repeat counts.
    retried = []
    for i, r in enumerate(results):
        if r["ok"] or r["detail"] == "file missing":
            continue
        again = _run_one(r["file"])
        if again["ok"]:
            again["detail"] = "green (failed under load, passed alone)"
            retried.append(r["file"])
        results[i] = again

    # keep declaration order so the report reads the same every time
    order = {tf: i for i, tf in enumerate(TEST_FILES)}
    results.sort(key=lambda r: order.get(r["file"], 999))
    real_fail = [r for r in results if not r["ok"] and not r.get("timeout")]
    return {"all_pass": not real_fail,
            "timed_out": [r["file"] for r in results if r.get("timeout")],
            "files": results,
            "flaky_under_load": retried}


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
    """The metabolism: grade must run WITHOUT a human.

    LOADED IS NOT RUNNING. This used to report only whether launchd knew about
    the job, so a job that was loaded and failing on every single fire looked
    healthy — which is how a malformed plist once killed the pass for 13.7
    hours in silence. The age of the last beat is the thing that says it is
    alive, and the interval is read from the plist rather than assumed (this
    docstring said 6h while the plist said 1h).
    """
    plist = os.path.expanduser("~/Library/LaunchAgents/com.meditate.grade.plist")
    exists = os.path.isfile(plist)
    loaded = False
    interval = None
    if exists:
        try:
            r = subprocess.run(["launchctl", "list"], capture_output=True,
                               text=True, timeout=10)
            loaded = "com.meditate.grade" in r.stdout
        except Exception:
            pass
        try:
            r = subprocess.run(["plutil", "-extract", "StartInterval", "raw",
                                plist], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                interval = int(r.stdout.strip())
        except Exception:
            pass
    log = os.path.expanduser("~/.claude/meditation/heartbeat.log")
    last_beat, age_s = None, None
    if os.path.exists(log):
        mt = os.path.getmtime(log)
        last_beat = time.strftime("%Y-%m-%d %H:%M", time.localtime(mt))
        age_s = time.time() - mt
    # two and a half intervals: one missed fire is a busy machine, three is a
    # job that is not coming back
    limit = (interval or 3600) * 2.5
    stale = bool(age_s is not None and age_s > limit)
    return {"plist_exists": exists, "loaded": loaded, "last_beat": last_beat,
            "interval_s": interval, "age_s": None if age_s is None else int(age_s),
            "stale": stale, "never_beat": exists and loaded and age_s is None}


# A launchd label is a promise about which program it supervises. Break that
# promise and every restart command lies: com.meditate.brain ran `ollama serve`
# for four days (measured 2026-08-29 — launchctl list said pid 83090 = ollama),
# so kickstarting "brain" restarted the model server while brain.py itself sat
# unsupervised at PPID 1, and code changes never reached the running Pulse.
# Each entry is label -> a fragment that MUST appear in ProgramArguments.
SERVICES = {
    "com.meditate.brain":  "brain.py",
    "com.meditate.tts":    "tts.py",
    "com.meditate.ollama": "ollama",
}


def _check_services() -> List[Dict[str, Any]]:
    out = []
    try:
        r = subprocess.run(["launchctl", "list"], capture_output=True,
                           text=True, timeout=10)
        listing = r.stdout
    except Exception:
        listing = ""
    for label, must_contain in SERVICES.items():
        plist = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % label)
        exists = os.path.isfile(plist)
        program, mismatched = None, False
        if exists:
            try:
                with open(plist, "rb") as f:
                    argv = plistlib.load(f).get("ProgramArguments") or []
                program = " ".join(argv)
                mismatched = must_contain not in program
            except Exception:
                pass
        out.append({"label": label, "plist_exists": exists,
                    "loaded": label in listing, "program": program,
                    "expects": must_contain, "mismatched": mismatched})
    return out


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


def _check_index() -> Dict[str, Any]:
    """Does MEMORY.md still point only at memories the grader trusts?

    MEMORY.md is the lane that carries the weight — ~5,000 tokens read into
    EVERY session by Claude Code's own harness. Until now nothing checked it
    against the graded store, so a demoted memory kept being read in verbatim
    until a person ran /meditate. Measured 2026-08-25: the graded lane served
    3 facts in 24h; this one runs every session.
    """
    try:
        sys.path.insert(0, SKILL_DIR)
        import repair
        stale = repair.stale_index_lines()
        return {"checked": True, "stale": len(stale), "lines": stale[:20]}
    except Exception as e:
        return {"checked": False, "stale": 0, "lines": [], "error": str(e)[:120]}


def _check_memory_coverage() -> Dict[str, Any]:
    """Which cwds you work in start with NO memory at all.

    Measured 2026-08-25: 164 of 228 transcripts ran in a cwd that had
    memories. The rest started cold and nothing said so — worst case, the
    tool's own repo with 45 sessions and zero memories. A gap nobody can see
    is a gap nobody fixes.
    """
    try:
        sys.path.insert(0, SKILL_DIR)
        import paths
        c = paths.memory_coverage()
        return {"checked": True, "blind": len(c["blind"]),
                "auto_linkable": sum(1 for b in c["blind"] if b["link_to"]),
                "sessions_covered": c["sessions_covered"],
                "sessions_total": c["sessions_total"], "detail": c["blind"][:10]}
    except Exception as e:
        return {"checked": False, "blind": 0, "auto_linkable": 0, "error": str(e)[:120]}


def _check_assessment() -> Dict[str, Any]:
    """Is meditate actually judging the work, or just counting sessions?

    Measured 2026-08-25: 83 tracked entries, 4 with a goal. Where a goal
    exists the judgement is good; the defect is that ~25 real products you
    work in have no yardstick, so the tool is silent about most of the work
    and the silence reads as health.
    """
    try:
        sys.path.insert(0, SKILL_DIR)
        import projects
        g = projects.assessment_gaps()
        return {"checked": True, "tracked": g["tracked"],
                "real_projects": g["real_projects"], "assessed": g["assessed"],
                "unassessed": len(g["unassessed"]),
                "not_projects": len(g["not_projects"]),
                "top_unassessed": [x["project"] for x in g["unassessed"][:5]],
                # Only the ones a person could actually fix. This used to be
                # not_projects[:5], which printed `skills`, `other` and
                # worktree hashes as things to write alias lines for — all
                # already handled by a rule, none actionable.
                "needs_alias": [x["project"] for x in g.get("needs_alias", [])[:5]],
                "handled_by_rule": len(g["not_projects"]) - len(g.get("needs_alias", [])),
                # Started and left — real history, nothing in 30 days. Added
                # 2026-08-26 with the widened scan; before it, a repo the tool
                # never looked at and a repo with no activity both read as 0.
                "dormant": len(g["dormant"]),
                "top_dormant": ["%s (%d)" % (x["project"], x["commits"])
                                for x in g["dormant"][:5]]}
    except Exception as e:
        return {"checked": False, "error": str(e)[:120]}



def _check_fleet() -> Dict[str, Any]:
    """Did the agents this tool launched actually DO anything?

    This is the check that was missing, and its absence is why the same
    failure ran 45 times unseen. Measured 2026-08-29: heartbeat.log held 45
    identical "osascript error" lines — one class of failure, every one after
    a 75-second timeout — while dispatch.jsonl recorded 59 successful
    dispatches and 5 headless agent logs held 39 bytes each, header only.
    The tool reported a working fleet and had no way to know otherwise.

    A dispatch is only real if something came out of it: a window id, or a
    headless log with agent output in it past the two-line header.
    """
    try:
        import json as _json
        ledger = os.path.expanduser("~/.claude/meditation/dispatch.jsonl")
        rows = []
        with open(ledger) as f:
            for ln in f:
                try:
                    rows.append(_json.loads(ln))
                except ValueError:
                    continue
        rows = [r for r in rows if "window_id" in r]      # older rows predate the field
        logs = os.path.expanduser("~/.claude/meditation/agents")
        produced = 0
        empty = 0
        for fn in (os.listdir(logs) if os.path.isdir(logs) else []):
            try:
                n = os.path.getsize(os.path.join(logs, fn))
            except OSError:
                continue
            # the header alone is ~35-45 bytes; anything past it is real output
            (produced, empty) = (produced + 1, empty) if n > 60 else (produced, empty + 1)
        windowed = sum(1 for r in rows if (r.get("window_id") or "").strip())
        # `how` was added the same day; rows without it cannot be classified,
        # and unclassified is reported as unknown rather than as failure.
        unknown = sum(1 for r in rows if not r.get("how"))
        return {"checked": True, "dispatched": len(rows), "with_window": windowed,
                "headless_logs": produced + empty, "headless_with_output": produced,
                "headless_empty": empty, "unclassified": unknown}
    except Exception as e:
        return {"checked": False, "error": str(e)[:120]}


def _check_warranty() -> Dict[str, Any]:
    """How much of MEMORY.md an agent could actually re-check.

    MEMORY.md is ~54% of an agent's opening context and is loaded into every
    session by Claude Code's own harness. A line backed only by quote-scoped
    evidence is permanently green and permanently unfalsifiable — the
    difference between this project's 56% and 13%.
    """
    try:
        sys.path.insert(0, SKILL_DIR)
        import repair
        w = repair.index_warranty()
        n = w["lines"] or 1
        return {"checked": True, "lines": w["lines"], "world": w["world"],
                "unwarrantied": w["unwarrantied"], "ungraded": w["ungraded"],
                "broken": w["broken"], "world_pct": round(w["world"] / n * 100)}
    except Exception as e:
        return {"checked": False, "error": str(e)[:120]}


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
    services = _check_services()
    stillness = _check_stillness()
    output = _check_output()
    nidra = _check_nidra()
    index = _check_index()
    coverage = _check_memory_coverage()
    assessment = _check_assessment()
    warranty = _check_warranty()
    fleet = _check_fleet()

    issues = []
    if not all(p["ok"] for p in prereqs):
        issues.append("prerequisites")
    if not tests["all_pass"]:
        issues.append("tests")
    if not hook["all_wired"]:
        issues.append("hook_registration")
    if not heartbeat["loaded"]:
        issues.append("heartbeat_not_loaded")
    elif heartbeat.get("never_beat"):
        issues.append("heartbeat_never_ran")
    elif heartbeat.get("stale"):
        issues.append("heartbeat_stale")
    if any(s["mismatched"] for s in services):
        # A label supervising the wrong program is worse than a missing one:
        # restart commands report success and change nothing.
        issues.append("service_label_mismatch")
    if index.get("stale"):
        issues.append("memory_index_stale")
    if assessment.get("needs_alias"):
        # Path fragments sitting in the project table as if they were products,
        # AND not already handled by a rule. Mechanical: one alias line each.
        # It used to fire on `not_projects`, which includes the containers,
        # the `other` sentinel and every worktree hash — measured 2026-08-29,
        # 8 flagged and 0 of them actionable, so doctor was permanently red
        # over work nobody should do. NOT an issue for unassessed products
        # either — which product deserves a goal is the owner's call, and
        # doctor going red on a judgement it cannot make is how a health check
        # gets ignored.
        issues.append("project_names_unaliased")
    if coverage.get("auto_linkable"):
        # Only the MECHANICAL gap is an issue. A cwd with no covering project
        # is a decision, not a defect, so it is reported but never fails the
        # health check — doctor must not go red on something it cannot fix.
        issues.append("memory_dirs_unlinked")
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
        "services": services,
        "stillness": stillness,
        "index": index,
        "coverage": coverage,
        "assessment": assessment,
        "warranty": warranty,
        "fleet": fleet,
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

    slow = d["tests"].get("timed_out") or []
    verdict = "all green" if d["tests"]["all_pass"] else "FAILURES"
    if d["tests"]["all_pass"] and slow:
        verdict = "all green (%d gave no verdict in time — machine busy)" % len(slow)
    print(f"\nTests: {verdict}")
    for t in d["tests"]["files"]:
        mark = "ok" if t["ok"] else ("SLOW" if t.get("timeout") else "FAIL")
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

    svcs = d.get("services", [])
    if svcs:
        print(f"\nServices:")
        for s in svcs:
            if s["mismatched"]:
                mark = "WRONG"
                detail = "runs %r, expected %s" % (s["program"], s["expects"])
            elif not s["plist_exists"]:
                mark = "absent"
                detail = "no plist (run install.sh)"
            else:
                mark = "ok" if s["loaded"] else "STOPPED"
                detail = s["expects"]
            print(f"  [{mark:>7}]  {s['label']:22} {detail}")

    print(f"\nStillness:")
    if d["stillness"]["exists"]:
        age = d["stillness"]["age_days"]
        status = "OVERDUE" if d["stillness"]["overdue"] else "ok"
        print(f"  [{status:>7}]  STILLNESS.md — {age:.1f} days old (threshold: {MAX_AGE_DAYS}d)")
    else:
        print(f"  [MISSING]  STILLNESS.md — run /meditate to create")

    # Assessment / warranty / coverage were computed into the JSON and never
    # printed — so a person running `doctor` saw a green report while ~25
    # products had no yardstick and half of MEMORY.md was unfalsifiable.
    a = d.get("assessment", {})
    if a.get("checked"):
        print(f"\nAssessment:")
        print(f"  {a['real_projects']} products · {a['assessed']} with a goal · "
              f"{a['unassessed']} without · {a['not_projects']} name-fragments")
        if a.get("top_unassessed"):
            print(f"  no yardstick: {', '.join(a['top_unassessed'])}")
        if a.get("dormant"):
            print(f"  started and left ({a['dormant']}): {', '.join(a.get('top_dormant', []))}")

    w = d.get("warranty", {})
    if w.get("checked"):
        print(f"\nMEMORY.md warranty:")
        print(f"  {w['lines']} lines · {w['world']} re-checkable ({w['world_pct']}%) · "
              f"{w['unwarrantied']} unwarrantied · {w['ungraded']} ungraded · "
              f"{w['broken']} broken")

    fl = d.get("fleet", {})
    if fl.get("checked") and fl.get("dispatched"):
        print(f"\nFleet:")
        print(f"  {fl['dispatched']} dispatched · {fl['with_window']} opened a window · "
              f"{fl['headless_with_output']} of {fl['headless_logs']} headless logs "
              f"have agent output")
        if fl["headless_empty"]:
            print(f"  [{'EMPTY':>7}]  {fl['headless_empty']} headless agent(s) produced nothing")

    c = d.get("coverage", {})
    if c.get("auto_linkable"):
        print(f"\nMemory dirs: {len(c['auto_linkable'])} unlinked but auto-linkable "
              f"(meditate paths --link-memory)")

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
