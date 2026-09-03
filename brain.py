"""pulse — the whole organism, live, in the browser.

    meditate pulse            # serve http://127.0.0.1:7711 and open it
    meditate brain --port N

One page, auto-refreshing every 4 s: live sessions (who is working, on what,
right now), the goal fleet, the repair queue, memory census, wins, and what
ran silently. Everything the hooks know, made visible.

Stdlib only. Binds 127.0.0.1 ONLY and refuses anything else — this page IS
the owner's memory and sessions; a brain never faces a network by default.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import threading
import time
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

import paths
from coordination import last_file as _last_file
from projects import rollup as _pj_rollup

DEFAULT_PORT = 7711

ACTIONS = {
    "go":    lambda arg: ["python3", os.path.join(SKILL_DIR, "go.py")] + ([arg] if arg else []),
    "fix":   lambda arg: ["python3", os.path.join(SKILL_DIR, "go.py"), "--repair-only"] + ([arg] if arg else []),
    "grade": lambda arg: ["python3", os.path.join(SKILL_DIR, "nidra_bridge.py"), "--sleep"],
    # Reach a live session by id: `tell <sid> <message>`. The console listed
    # sessions but could not touch one, which is what "not connected enough"
    # meant — a roster is not a console.
    # Drop finished / un-finishable rows from the dispatch ledger. The fleet
    # listed work you could not act on: six done rows with no way to dismiss
    # them, and three with no milestone at all reporting "88 minutes, worth a
    # look" forever, because a milestone that does not exist can never tick.
    "clear": lambda arg: ["python3", os.path.join(SKILL_DIR, "fleet.py"), "clear"]
                         + ([arg] if arg else []),
    # Pick a dormant project back up. Without this verb the mascot could
    # surface eight abandoned repos and do nothing about any of them —
    # "yes" had no meaning, which is exactly the dead end the agenda
    # docstring warns about a few files over.
    "revive": lambda arg: ["python3", os.path.join(SKILL_DIR, "projects.py"),
                           "--revive-open", arg or ""],
    "tell":  lambda arg: ["python3", os.path.join(SKILL_DIR, "inbox.py"), "send"]
                         + (arg.split(" ", 1) if " " in (arg or "") else [arg or "", ""]),
    # The meditate acts. The console could dispatch agents and grade memory
    # but not do the thing the tool is named after — so stilling a session
    # still meant leaving here and typing a command, which is the one thing
    # the owner said he wanted to stop doing.
    "still":   lambda arg: ["python3", os.path.join(SKILL_DIR, "sessions.py")]
                           + (["--session", arg] if arg else []),
    "threads": lambda arg: ["python3", os.path.join(SKILL_DIR, "launch.py")],
    # --open spawns Terminal windows. Deliberately a separate verb from
    # `threads`: seeing the plan and executing it are different acts, and
    # one click should not open twenty windows.
    "open":    lambda arg: ["python3", os.path.join(SKILL_DIR, "launch.py"),
                            "--open"],
    # Dry-run only. Archiving is reversible but it is still the owner's
    # call per session, and a button that silently sets down finished work
    # is not a button, it is a surprise. `--apply` is not reachable here.
    "settle":  lambda arg: ["python3", os.path.join(SKILL_DIR, "archive.py")],
}

# Verbs whose OUTPUT is the answer, not a status line. They get a real
# character budget and a longer leash; everything else keeps the short one.
READING = {"still", "threads", "settle"}


def _note(step: str) -> None:
    try:
        import thinking
        thinking.note(step)
    except Exception:
        pass


def _default_runner(action: str, arg: str) -> Dict[str, Any]:
    """Run the same code the CLI runs and RETURN ITS REAL OUTPUT — a click
    that hides what it did is the opposite of intuitive. go/fix finish in a
    couple seconds (they open Terminal agents and report); grade is slow, so
    it detaches and says so. Never push/deploy: those gates stay with the
    owner in the terminal."""
    _note({"go": "starting the fleet", "fix": "repairing what broke",
           "grade": "re-checking every memory",
           "revive": "opening the project you left",
           "still": "reading every session",
           "threads": "finding the live threads",
           "open": "opening a window per live thread",
           "settle": "checking what is finished"}.get(action,
                                                      "running " + action))
    cmd = ACTIONS[action](arg)
    if action == "grade":
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return {"started": True,
                "output": "grading in background — numbers refresh as it lands"}
    if action == "revive":
        # Check the name BEFORE detaching. Popen cannot see a failure, so
        # without this the console answered "opening a window on
        # zzz-not-dormant" for a project that does not exist — started:true
        # over nothing, which is the exact lie didNothing() exists to catch.
        # The lookup is cached, so this costs nothing.
        try:
            import projects as _pj
            if arg and arg not in {c["project"] for c in _pj.revival_cards()}:
                return {"started": False,
                        "output": "%s isn't one of the projects you left — "
                                  "nothing to open" % arg}
        except Exception:
            pass          # cannot check is not "no"; fall through and try
        # launch_claude polls up to 45s for the TUI to come up, so this can
        # never finish inside the 25s cap below. Reporting "still running,
        # check the fleet" would be a wrong instruction, not just a slow one.
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return {"started": True,
                "output": "opening a window on %s — it takes a few seconds to "
                          "come up" % (arg or "it")}
    # A reading verb IS its output: 600 chars turned a 329-session list into
    # four rows and said nothing about the other 325. The cap stays for the
    # status verbs, and when it does bite it now says so.
    cap = 24_000 if action in READING else 600
    limit = 90 if action in READING else 25
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=limit)
        out = (r.stdout or r.stderr or "").strip() or "(no output)"
    except subprocess.TimeoutExpired:
        out = "still running after %ds — check `meditate fleet`" % limit
    _note("")
    if len(out) > cap:
        kept = out[:cap].rsplit("\n", 1)[0]
        out = kept + "\n… %d more characters not shown" % (len(out) - len(kept))
    return {"started": True, "output": out}


ACT_RUNNER = _default_runner   # tests monkeypatch this

NAMES_PATH = os.path.expanduser("~/.claude/coordination/session-names.json")
LABELS_CACHE = os.path.expanduser("~/.claude/coordination/session-labels.json")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

import re as _re
_CHAPTER_RE = _re.compile(r'mark_chapter.{0,400}?\\?"title\\?":\s*\\?"([^"\\]{4,70})')
_ASK_RE = _re.compile(r'"type":\s*"user".{0,2000}?"(?:text|content)":\s*"((?:[^"\\]|\\.){8,160})')


def _derive_label(full_sid: str, cwd: str) -> str:
    """WHAT is this session doing — from its own transcript, precisely.

    Priority: the session's LAST chapter mark (it names its own phase), else
    its last user ask (the owner's words), else the project dir. Reads only
    the transcript tail (300 KB) and recomputes at most once per 60 s per
    session — active transcripts change every few seconds and a full re-read
    per tick would burn the 4 s budget.
    """
    try:
        with open(LABELS_CACHE) as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    ent = cache.get(full_sid)
    now = time.time()
    if ent and now - ent.get("ts", 0) < 60:
        return ent.get("label", "")
    label = ""
    try:
        import glob as _glob
        cands = _glob.glob(os.path.join(PROJECTS_DIR, "*", full_sid + ".jsonl"))
        if cands:
            tp = max(cands, key=os.path.getmtime)
            size = os.path.getsize(tp)
            with open(tp, "rb") as f:
                if size > 300_000:
                    f.seek(size - 300_000)
                tail = f.read().decode("utf-8", errors="replace")
            chapters = _CHAPTER_RE.findall(tail)
            if chapters:
                label = chapters[-1]
            else:
                # transcripts wrap TOOL RESULTS inside "type":"user" rows —
                # trusting the type alone labeled sessions with "Exit code
                # 143..." garbage, live. A human ask is a user row WITHOUT
                # tool_result, and it must read like words.
                for line in tail.splitlines():
                    if '"type":"user"' not in line or "tool_result" in line                             or "toolUseResult" in line:
                        continue
                    m = _ASK_RE.search(line)
                    if not m:
                        continue
                    a = m.group(1).encode().decode("unicode_escape", errors="replace")
                    a = _re.sub(r"\s+", " ", a).strip()
                    if a.startswith(("{", "<", "Exit code", "[")) or " " not in a:
                        continue
                    if sum(c.isalpha() for c in a) < len(a) * 0.5:
                        continue                     # numbers/log spew
                    label = a[:64]                   # last good one wins
    except Exception:
        label = ""
    cache[full_sid] = {"label": label, "ts": now}
    try:
        os.makedirs(os.path.dirname(LABELS_CACHE), exist_ok=True)
        with open(LABELS_CACHE + ".tmp", "w") as f:
            json.dump(cache, f)
        os.replace(LABELS_CACHE + ".tmp", LABELS_CACHE)
    except OSError:
        pass
    return label


def _names() -> Dict[str, str]:
    try:
        with open(NAMES_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def set_name(sid: str, name: str) -> None:
    names = _names()
    names[sid[:12]] = name[:60]
    os.makedirs(os.path.dirname(NAMES_PATH), exist_ok=True)
    with open(NAMES_PATH + ".tmp", "w") as f:
        json.dump(names, f)
    os.replace(NAMES_PATH + ".tmp", NAMES_PATH)


def _pid_is_claude(pid: int) -> bool:
    """Refuse to signal anything that is not verifiably a claude process."""
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=5).stdout
        return "claude" in out.lower()
    except Exception:
        return False


def stop_session(sid: str, kill=os.kill) -> Dict[str, Any]:
    """SIGTERM the session's claude process — the button equivalent of
    closing its window. Guarded: pid must come from presence AND still be a
    claude process, or we refuse."""
    import coordination as co
    # resolve the dir at CALL time — default-arg binding ate a test once already
    for s in co.live_sessions(co.COORD_DIR):
        if s.get("sid", "").startswith(sid[:8]):
            pid = int(s.get("pid") or 0)
            if pid <= 1:
                return {"started": False, "output": "no pid recorded for that session yet (it appears after its next file edit)"}
            if not _pid_is_claude(pid):
                return {"started": False, "output": "refused: pid %d is not a claude process" % pid}
            try:
                kill(pid, 15)
                return {"started": True, "output": "sent stop (SIGTERM) to session %s (pid %d)" % (sid[:8], pid)}
            except ProcessLookupError:
                return {"started": False, "output": "already gone"}
            except PermissionError:
                return {"started": False, "output": "permission denied"}
    return {"started": False, "output": "session not found among the living"}


def _log_brain_action(action: str, arg: str) -> None:
    """Every click leaves a durable record — the page's ACTIVITY section and
    the efficacy report both read this."""
    try:
        from coordination import events_path
        ev = events_path()
        with open(ev, "a") as f:
            f.write(json.dumps({"type": "brain_action", "path": action +
                                ((" " + arg) if arg else ""), "sid": "brain",
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                    time.gmtime())}) + "\n")
    except OSError:
        pass


def _dispatch_label(s, dispatched) -> str:
    for r in dispatched or []:
        if r.get("live_session") and s.get("sid", "").startswith(r["live_session"][:8]):
            return "goal: " + (r.get("milestone") or "")[:56]
    return ""


_STATE_LOCK = threading.Lock()
_STATE_SNAPSHOT: Dict[str, Any] = {"at": 0.0, "data": None}
STATE_TTL_S = 2.0


def state() -> Dict[str, Any]:
    """One computation at a time, shared by every caller.

    The per-component caches below each fixed a slow ORGAN. None of them
    stopped a stampede: state() was computed per request, the page polls every
    4s, and a cold start costs ~7s — so poll N+1 begins before poll N finishes,
    the browser aborts at its own timeout and immediately retries, and every
    abandoned request keeps a thread scanning 100MB+ of transcripts. Measured
    2026-08-29 on a restart under a live dashboard: 87 threads, 64 open
    transcript fds, 120% CPU, and /api/state never answered in 4 minutes,
    while the same build with one client answered in 6.9s cold / 0.7s warm.

    The lock is the whole fix: one scan at a time, and whoever was waiting
    gets the result that scan just produced instead of starting another.
    """
    snap = _STATE_SNAPSHOT["data"]
    if snap and time.time() - _STATE_SNAPSHOT["at"] < STATE_TTL_S:
        return snap
    with _STATE_LOCK:
        snap = _STATE_SNAPSHOT["data"]
        if snap and time.time() - _STATE_SNAPSHOT["at"] < STATE_TTL_S:
            return snap
        d = _state_uncached()
        _STATE_SNAPSHOT.update({"at": time.time(), "data": d})
        return d


def _state_uncached() -> Dict[str, Any]:
    """Every organ, one dict, all from durable stores."""
    import goals as gl
    import report as rp
    from drive import fleet_status
    from go import repair_items
    from status import gather
    from coordination import live_sessions, done_digest
    from beacon import latest as beacon_latest

    st = gather()
    rep = rp.compute()
    fleet = fleet_status()
    beacons = beacon_latest()
    for r in fleet["dispatched"]:
        bd = beacons.get(r.get("goal"))
        if bd:
            r["says"] = bd.get("message", "")
            r["says_done"] = bd.get("done", False)
            r["says_ts"] = bd.get("ts", "")[11:19]
    d = {
        "generated": time.strftime("%H:%M:%S"),
        "store": st["store"],
        "heartbeat_h": st["heartbeat_h"],
        "next": st["next"],
        "goals": [{"name": g["name"], "title": g["title"], "pct": g["pct"],
                   "done": g["done"], "total": g["total"], "cwd": g.get("cwd", ""),
                   "scope_delta": g.get("scope_delta", 0), "next": g["next"]}
                  for g in st["goals"]],
        "live_sessions": [{"sid": s.get("sid", "")[:12], "cwd": s.get("cwd", ""),
                           "age_s": s.get("_age_s"),
                           "pid": s.get("pid"),
                           # coordination.live_sessions already decides
                           # working (touched inside 180s) vs idle, and this
                           # payload dropped it — so every consumer had to
                           # re-guess liveness from last_file and got it
                           # wrong. Measured 2026-08-29: 34 reported "live",
                           # actually 7 working / 27 idle.
                           "state": s.get("_state", ""),
                           # precision ladder: owner's name > goal milestone >
                           # the session's own chapter/ask > project dir
                           "label": _names().get(s.get("sid", "")[:12])
                                    or _dispatch_label(s, fleet["dispatched"])
                                    or _derive_label(s.get("sid", ""), s.get("cwd", ""))
                                    or (os.path.basename(s.get("cwd", "").rstrip("/")) or "~"),
                           "last_file": _last_file(s)}
                          for s in live_sessions()],
        "triage": _triage_cached(),
        "milestones": _milestones_cached(),
        "fleet": fleet["dispatched"],
        "repair": [{"id": m["id"], "statement": m["statement"][:140],
                    "fails": [f["claim"] for f in m.get("failing", [])]}
                   for m in repair_items()],
        "queues": {"repair_open": st["repair_open"],
                   "cooling": st["cooling"],
                   "dispatchable": len(st["dispatchable"])},
        "wins": rep["drift"],
        "stilling": rep["stilling"],
        "sangama": rep["sangama"],
        "digest": done_digest(),
        # Casper watches these two: what to say, and whether now is the moment
        "briefing": _casper_briefing(),
        "timing": _casper_timing(),
        "projects": _projects_rollup(),
        # What was started and left. Git-derived, so it moves on the scale of
        # days — but until it was in this payload the fleet, the page and
        # Casper could none of them see a project the owner had not opened
        # this month, and 8 of them were invisible.
        "dormant": _dormant_cached(),
        "projects_window_days": _window_days_cached(),
        "facts_unattributed": getattr(_pj_rollup, "facts_unattributed", 0),
        "fleet_running": _fleet_running(),
        # True when the code on disk is newer than this process.
        "server_stale": _code_stamp() > _BOOT_STAMP + 1,
        "activity": _recent_events(),
    }
    try:
        from insights import insights as _ins
        d["insights"] = _ins(d)
    except Exception:
        d["insights"] = {"headline": "", "projects": [], "needs_you": [], "moving": []}
    # the hero paragraph — composed from the SAME dict the page gets, so the
    # words and the numbers under them can never disagree
    try:
        from brief import compose
        d["brief"] = compose(d)
    except Exception:
        d["brief"] = []
    return d


_DORMANT_CACHE: Dict[str, Any] = {"at": 0.0, "data": []}


def _dormant_cached(ttl_s: float = 3600.0) -> List[Dict[str, Any]]:
    """Started and left — cached for an hour.

    Costs ~8s cold: it walks 42 repos and shells out per repo for the commit
    count, the author list and the last subject. The window it reports is 30
    days wide, so an hour of staleness cannot change an answer. Uncached in a
    4-second poll it would have been the entire cost of /api/state, the same
    way _casper_briefing was before it was capped.
    """
    if time.time() - _DORMANT_CACHE["at"] < ttl_s and _DORMANT_CACHE["data"]:
        return _DORMANT_CACHE["data"]
    try:
        import projects as _pj
        cards = _pj.revival_cards(limit=8)
    except Exception:
        cards = []
    _DORMANT_CACHE.update({"at": time.time(), "data": cards})
    return cards


_BRIEFING_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}
BRIEFING_TTL_S = 30.0


def _casper_briefing(ttl_s: float = BRIEFING_TTL_S) -> Dict[str, Any]:
    """What to say. Cached — measured at 6.76s uncached, which WAS the entire
    cost of /api/state: warm polls took 7.18s against a 4-second poll, so
    requests piled up and the console felt dead while every number in it was
    current. Thirty seconds is still live for a sentence about your week."""
    if time.time() - _BRIEFING_CACHE["at"] < ttl_s and _BRIEFING_CACHE["data"]:
        return _BRIEFING_CACHE["data"]
    d = _casper_briefing_uncached()
    _BRIEFING_CACHE.update({"at": time.time(), "data": d})
    return d


def _casper_briefing_uncached() -> Dict[str, Any]:
    try:
        import voice as vc
        return vc.briefing()
    except Exception:
        return {"headline": "", "action": "", "kind": "clear"}


def _casper_timing() -> Dict[str, Any]:
    try:
        import voice as vc
        return vc.interruptibility()
    except Exception:
        return {"state": "unknown", "interrupt_ok": False}


_FLEET_CACHE: Dict[str, Any] = {"at": 0.0, "n": 0}


def _code_stamp() -> float:
    """Newest mtime across the modules this server serves from.

    A Pulse server runs for days with whatever code it started with. Nothing
    restarts it, so every change to brain.py or drive.py silently keeps
    serving the old answer — the mascot's fleet dots were grey for an hour
    because `alive` did not exist in THIS process, only on disk. Reporting the
    stamp makes that visible instead of mysterious.
    """
    newest = 0.0
    for f in ("brain.py", "drive.py", "voice.py", "status.py", "projects.py"):
        try:
            newest = max(newest, os.path.getmtime(os.path.join(SKILL_DIR, f)))
        except OSError:
            pass
    return newest


_BOOT_STAMP = _code_stamp()


def _fleet_running(ttl_s: float = 8.0) -> int:
    """How many dispatched agents are still open. Cached — it asks Terminal,
    and /api/state is polled by both the page and the mascot."""
    if time.time() - _FLEET_CACHE["at"] < ttl_s:
        return _FLEET_CACHE["n"]
    try:
        from drive import running_agents
        n = len(running_agents())
    except Exception:
        n = 0
    _FLEET_CACHE.update({"at": time.time(), "n": n})
    return n


_ROLLUP_CACHE: Dict[str, Any] = {"at": 0.0, "rows": []}
ROLLUP_TTL_S = 120.0


_TRIAGE_CACHE: Dict[str, Any] = {"at": 0.0, "data": {}}
TRIAGE_TTL_S = 90.0


def _triage_cached(ttl_s: float = TRIAGE_TTL_S) -> Dict[str, Any]:
    """Which chats are owed a reply. Cached: it reads 150+ transcript tails,
    and the answer does not change between two polls four seconds apart."""
    if time.time() - _TRIAGE_CACHE["at"] < ttl_s and _TRIAGE_CACHE["data"]:
        return _TRIAGE_CACHE["data"]
    try:
        from triage import triage as _t
        d = _t()
        out = {"counts": d.get("counts", {}),
               "action_items": d.get("action_items", [])[:6],
               "skipped": d.get("programmatic_skipped", 0)}
    except Exception:
        out = {"counts": {}, "action_items": [], "skipped": 0}
    _TRIAGE_CACHE.update({"at": time.time(), "data": out})
    return out


_WINDOW_CACHE: Dict[str, Any] = {"at": 0.0, "days": 0}


def _window_days_cached(ttl_s: float = 600.0) -> int:
    if time.time() - _WINDOW_CACHE["at"] < ttl_s and _WINDOW_CACHE["days"]:
        return _WINDOW_CACHE["days"]
    try:
        from projects import window_days
        _WINDOW_CACHE.update({"at": time.time(), "days": window_days()})
    except Exception:
        _WINDOW_CACHE["at"] = time.time()
    return _WINDOW_CACHE["days"] or 0


_MILE_CACHE: Dict[str, Any] = {"at": 0.0, "data": {}}


def _milestones_cached(ttl_s: float = 300.0) -> Dict[str, Any]:
    """Which milestones the world says are already done. Runs git per goal,
    so it is cached — but it is the check that stops the console asking for
    work that is finished."""
    if time.time() - _MILE_CACHE["at"] < ttl_s and _MILE_CACHE["data"]:
        return _MILE_CACHE["data"]
    try:
        from milestones import audit
        d = audit()
        out = {"looks_done": d.get("looks_done", [])[:5],
               "stale_wording": d.get("stale_wording", [])[:5],
               "unknown": d.get("unknown", 0)}
    except Exception:
        out = {"looks_done": [], "stale_wording": [], "unknown": 0}
    _MILE_CACHE.update({"at": time.time(), "data": out})
    return out


def _projects_rollup(ttl_s: float = ROLLUP_TTL_S) -> List[Dict[str, Any]]:
    """Which projects have your attention.

    Counting this means walking every session log on disk — ~10s here. The
    mascot polls state every few seconds, so uncached this endpoint was the
    whole reason it felt dead. Attention does not change second to second;
    a 2-minute-old answer is the same answer."""
    if time.time() - _ROLLUP_CACHE["at"] < ttl_s:
        return _ROLLUP_CACHE["rows"]
    _refresh_rollup_async()
    return _ROLLUP_CACHE["rows"]      # never block a poll; fills in shortly


def _refresh_rollup_async() -> None:
    if _ROLLUP_CACHE.get("running"):
        return
    _ROLLUP_CACHE["running"] = True

    def work():
        try:
            from projects import rollup
            rows = [r for r in rollup() if r["messages"] or r["goals"]][:8]
            _ROLLUP_CACHE.update({"at": time.time(), "rows": rows})
        except Exception:
            _ROLLUP_CACHE["at"] = time.time()   # don't hot-loop on a failure
        finally:
            _ROLLUP_CACHE["running"] = False

    threading.Thread(target=work, daemon=True).start()


def _recent_events(n: int = 10) -> List[Dict[str, str]]:
    # Through the shared resolver, like the writer above. Reader and writer
    # disagreeing meant a test wrote to its sandbox and read the owner's real
    # activity trail — the same split that leaked test POSTs into it before.
    from coordination import events_path
    ev = events_path()
    rows: List[Dict[str, str]] = []
    if os.path.exists(ev):
        try:
            with open(ev, errors="replace") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        rows.append({"type": r.get("type", "?"),
                                     "what": os.path.basename(str(r.get("path", ""))),
                                     "ts": str(r.get("ts", ""))[11:19]})
                    except Exception:
                        continue
        except OSError:
            pass
    return rows[-n:][::-1]


REPORT_PAGE = """<!doctype html><meta charset="utf-8">
<title>What is on your plate</title>
<style>
  :root{
    --bg:#0b0a08; --panel:#100e0b; --line:#231f19; --line-soft:#191611;
    --ink:#e6e0d2; --ink-dim:#8a8578; --ink-faint:#6b6557;
    --gold:#E3B140; --gold-soft:#c9973a; --alert:#c96442;
    --r:12px;
  }
  *{box-sizing:border-box}
  body{background:var(--bg);color:var(--ink);margin:0;
       font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;
       padding:40px 44px 80px;max-width:900px;-webkit-font-smoothing:antialiased}
  .eyebrow{letter-spacing:.32em;font-size:10.5px;color:var(--ink-faint);
           text-transform:uppercase}
  h1{font-size:26px;font-weight:600;margin:14px 0 4px;letter-spacing:-.01em}
  .lede{font-size:16px;line-height:1.6;color:var(--ink-dim);max-width:66ch;
        margin:0 0 26px}
  .row{background:var(--panel);border:1px solid var(--line);
       border-radius:var(--r);padding:15px 17px;margin-bottom:11px;
       display:grid;grid-template-columns:22px 1fr;gap:14px;align-items:start;
       transition:border-color .15s,background .15s}
  .row:hover{border-color:#2e2820}
  .row.on{border-color:var(--gold-soft);background:#13100b}
  .row input{appearance:none;width:17px;height:17px;margin-top:2px;
             border:1.5px solid #3a342a;border-radius:5px;cursor:pointer;
             transition:all .15s}
  .row input:checked{background:var(--gold);border-color:var(--gold)}
  .row input:checked::after{content:"";display:block;width:5px;height:9px;
        border:solid #0b0a08;border-width:0 2px 2px 0;transform:rotate(42deg);
        margin:1px 0 0 5px}
  .say{font-size:15px;line-height:1.5}
  .meta{margin-top:6px;font-size:12.5px;color:var(--ink-faint)}
  .tag{display:inline-block;font-size:10.5px;letter-spacing:.14em;
       text-transform:uppercase;color:var(--ink-faint);
       border:1px solid var(--line);border-radius:999px;padding:2px 9px;
       margin-right:8px}
  .tag.repair{color:var(--alert);border-color:#3a251d}
  .tag.goal{color:var(--gold-soft);border-color:#3a3020}
  .detail{margin-top:9px;font-size:13px;color:var(--ink-dim);
          border-left:2px solid var(--line);padding-left:11px;line-height:1.55}
  .bar{position:sticky;bottom:0;background:linear-gradient(transparent,var(--bg) 26%);
       padding:26px 0 4px;margin-top:26px;display:flex;gap:10px;align-items:center}
  .b{cursor:pointer;border:1px solid var(--line);background:transparent;
     color:var(--gold);border-radius:9px;padding:9px 17px;font-size:13.5px;
     transition:background .15s,border-color .15s}
  .b:hover{background:#1b1710;border-color:var(--gold-soft)}
  .b.primary{background:var(--gold);color:#0b0a08;border-color:var(--gold);
             font-weight:600}
  .b.primary:hover{background:#f0bd48}
  .b[disabled]{opacity:.35;cursor:default}
  .count{color:var(--ink-faint);font-size:12.5px;margin-left:auto}
  h2{font-size:12px;letter-spacing:.22em;text-transform:uppercase;
     color:var(--ink-faint);font-weight:500;margin:44px 0 13px}
  .parked{background:transparent;border:1px dashed var(--line);
          border-radius:10px;padding:12px 15px;margin-bottom:9px;
          display:flex;gap:12px;align-items:center;color:var(--ink-dim)}
  .parked .b{padding:5px 11px;font-size:12px;margin-left:auto}
  .empty{color:var(--ink-faint);font-style:italic}
  #flash{position:fixed;left:44px;bottom:22px;background:var(--panel);
         border:1px solid var(--gold-soft);border-radius:9px;padding:10px 16px;
         color:var(--ink);opacity:0;transition:opacity .25s;pointer-events:none}
  #flash.show{opacity:1}
</style>
<div class="eyebrow">Report</div>
<h1>What is on your plate</h1>
<p class="lede">Tick the ones you are actually going to do. Everything you
leave unticked goes to the backlog — not deleted, not finished, just put down
on purpose, and it stops being offered until you bring it back.</p>
<div id="live"></div>
<div class="bar">
  <button class="b primary" id="commit" disabled>Keep ticked, backlog the rest</button>
  <button class="b" id="all">Tick all</button>
  <button class="b" id="none">Tick none</button>
  <span class="count" id="count"></span>
</div>
<h2>Put down</h2>
<div id="parked"></div>
<div id="flash"></div>
<script>
const esc = t => String(t == null ? "" : t).replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let DATA = {live: [], backlog: []};

function flash(msg){
  const f = document.getElementById("flash");
  f.textContent = msg; f.classList.add("show");
  setTimeout(() => f.classList.remove("show"), 2600);
}

function render(){
  const live = document.getElementById("live");
  live.innerHTML = DATA.live.length ? "" :
    '<p class="empty">Nothing is waiting on you.</p>';
  DATA.live.forEach((r, i) => {
    const d = document.createElement("div");
    d.className = "row";
    d.innerHTML = '<input type="checkbox" id="c' + i + '">' +
      '<div><div class="say">' + esc(r.say) + '</div>' +
      '<div class="meta"><span class="tag ' + esc(r.kind) + '">' +
        esc(r.kind) + '</span>' + (r.progress ? esc(r.progress) : "") + '</div>' +
      (r.detail ? '<div class="detail">' + esc(r.detail) + '</div>' : '') +
      '</div>';
    const box = d.querySelector("input");
    box.addEventListener("change", () => {
      d.classList.toggle("on", box.checked); recount();
    });
    live.appendChild(d);
  });

  const parked = document.getElementById("parked");
  parked.innerHTML = DATA.backlog.length ? "" :
    '<p class="empty">Nothing put down yet.</p>';
  DATA.backlog.forEach(b => {
    const d = document.createElement("div");
    d.className = "parked";
    d.innerHTML = '<span>' + esc(b.say || b.key) + '</span>' +
      '<span style="color:var(--ink-faint);font-size:12px">' +
      esc(b.days) + 'd</span>' +
      '<button class="b">Bring back</button>';
    d.querySelector("button").addEventListener("click", async () => {
      await act("unbacklog", b.key, "");
      flash("brought back"); await load();
    });
    parked.appendChild(d);
  });
  recount();
}

function ticked(){
  return DATA.live.filter((_, i) => document.getElementById("c" + i).checked);
}
function recount(){
  const n = ticked().length, total = DATA.live.length;
  document.getElementById("count").textContent =
    total ? n + " of " + total + " ticked — " + (total - n) + " to backlog" : "";
  // Enabled even at zero: backlogging everything is a real answer, and the
  // button says what it will do.
  document.getElementById("commit").disabled = total === 0;
}

async function act(action, arg, value){
  const r = await fetch("/api/act", {method:"POST",
    headers:{"Content-Type":"application/json","X-Meditate":"1"},
    body: JSON.stringify({action, arg, value})});
  return r.json();
}

async function load(){
  const r = await fetch("/api/report", {headers:{"X-Meditate":"1"}});
  DATA = await r.json(); render();
}

document.getElementById("all").addEventListener("click", () => {
  DATA.live.forEach((_, i) => {
    const b = document.getElementById("c" + i);
    b.checked = true; b.dispatchEvent(new Event("change"));
  });
});
document.getElementById("none").addEventListener("click", () => {
  DATA.live.forEach((_, i) => {
    const b = document.getElementById("c" + i);
    b.checked = false; b.dispatchEvent(new Event("change"));
  });
});
document.getElementById("commit").addEventListener("click", async () => {
  const keep = new Set(ticked().map(r => r.key));
  const drop = DATA.live.filter(r => !keep.has(r.key));
  for (const r of drop) await act("backlog", r.key, r.say);
  flash(drop.length ? "backlogged " + drop.length : "kept everything");
  await load();
});
load();
</script>
"""


def report_data() -> Dict[str, Any]:
    """Everything the report shows, in one call.

    The live list, each item with WHY it is there and what acting on it
    means — plus what is already put down. The mascot could only ever read
    four sentences aloud; nothing let him see them together, weigh them
    against each other, and decide which ones he was actually going to do.
    """
    import voice as _v
    import backlog as _b
    live = []
    for it in _v.agenda():
        if not (it.get("action") or ""):
            continue
        row = {"say": it.get("say", ""), "action": it.get("action", ""),
               "kind": it.get("kind", ""), "goal": it.get("goal", ""),
               "milestone": it.get("milestone", ""),
               "key": _b.key_for(it), "detail": "", "progress": ""}
        if row["kind"] == "goal" and row["goal"]:
            try:
                from goals import detail as _detail
                d = _detail(row["goal"]) or {}
                row["progress"] = ("%d of %d done" % (d.get("done", 0),
                                                      d.get("total", 0)))
                nxt = next((m for m in d.get("milestones", [])
                            if not m.get("done")), None)
                if nxt:
                    bits = []
                    if nxt.get("verdict"):
                        bits.append(str(nxt["verdict"]))
                    if nxt.get("evidence"):
                        bits.append(str(nxt["evidence"])[:280])
                    row["detail"] = " — ".join(bits)
                if (d.get("agent") or {}).get("message"):
                    row["detail"] = (row["detail"] + "  Someone is on it: "
                                     + d["agent"]["message"][:200]).strip()
            except Exception:
                pass
        live.append(row)
    return {"live": live, "backlog": _b.items(),
            "generated": time.strftime("%A %-d %B, %H:%M")}


_SWARM_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}


def _swarm_cached(ttl_s: float = 45.0) -> Dict[str, Any]:
    """The dispatch plan, warm. It asks go.py's dry run, which walks goals and
    the cooldown ledger, so it is not free — and the answer only changes when
    the queue does."""
    if time.time() - _SWARM_CACHE["at"] < ttl_s and _SWARM_CACHE["data"]:
        return _SWARM_CACHE["data"]
    try:
        import swarm as _sw
        d = _sw.plan()
    except Exception as e:
        d = {"agents": [], "notes": [], "error": str(e)[:200]}
    _SWARM_CACHE.update({"at": time.time(), "data": d})
    return d


_TWIN_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}


def _twin_cached(ttl_s: float = 60.0) -> Dict[str, Any]:
    """CLAUD-E's sections, warm. Cold cost is ~12s (the project field's git
    walk); in-process the underlying caches hold it near half a second, and a
    minute of staleness cannot change an answer whose windows are 30 days."""
    if time.time() - _TWIN_CACHE["at"] < ttl_s and _TWIN_CACHE["data"]:
        return _TWIN_CACHE["data"]
    try:
        import twin as _tw
        d = {"sections": _tw.build()}
        try:
            d["series"] = _tw.goal_series()
        except Exception:
            d["series"] = None      # a missing chart must not cost the page
    except Exception as e:
        d = {"sections": [], "error": str(e)[:200]}
    _TWIN_CACHE.update({"at": time.time(), "data": d})
    return d


# The twin's own page — managing THROUGH CLAUD-E, not beside him. The main
# Pulse page is the organism's console; this one is the person-shaped view:
# who you are, how you decide, what moved, what you could do better — with
# the manage verbs inline, wired to the same /api/act the mascot uses. One
# server, one act path; the page is a view, never a second brain.
TWIN_PAGE = """<!doctype html><meta charset="utf-8">
<title>CLAUD-E — your digital twin</title>
<body style="background:#0d0c0a;color:#d8d4cc;font:14px/1.6 -apple-system,Helvetica,sans-serif;margin:0;padding:40px 48px;max-width:900px">
<div style="font-family:ui-monospace,monospace;color:#E3B140;white-space:pre;font-size:11px;line-height:1.25" id="mark"></div>
<div style="font-family:ui-monospace,monospace;white-space:pre;color:#8a8578;font-size:13px;margin-top:6px" id="face"></div>
<div id="body" style="margin-top:26px;color:#8a8578">deriving from the record…</div>
<div style="margin-top:34px;color:#6b6557;font-size:12px">
  every line is your own sentence, a counted number, or a live switch — nothing invented ·
  <a href="/" style="color:#E3B140">pulse</a> · <a href="/report" style="color:#E3B140">agenda</a></div>
<script>
const GOLD="#E3B140", DIM="#8a8578", FG="#d8d4cc";
document.getElementById("mark").textContent =
"   \\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2557 \\u2588\\u2588\\u2557      \\u2588\\u2588\\u2588\\u2588\\u2588\\u2557  \\u2588\\u2588\\u2557   \\u2588\\u2588\\u2557 \\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2557        \\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2557\\n" +
"  \\u2588\\u2588\\u2554\\u2550\\u2550\\u2550\\u2550\\u255d \\u2588\\u2588\\u2551     \\u2588\\u2588\\u2554\\u2550\\u2550\\u2588\\u2588\\u2557 \\u2588\\u2588\\u2551   \\u2588\\u2588\\u2551 \\u2588\\u2588\\u2554\\u2550\\u2550\\u2588\\u2588\\u2557       \\u2588\\u2588\\u2554\\u2550\\u2550\\u2550\\u2550\\u255d\\n" +
"  \\u2588\\u2588\\u2551      \\u2588\\u2588\\u2551     \\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2551 \\u2588\\u2588\\u2551   \\u2588\\u2588\\u2551 \\u2588\\u2588\\u2551  \\u2588\\u2588\\u2551 \\u2588\\u2588\\u2588\\u2588\\u2588\\u2557\\u2588\\u2588\\u2588\\u2588\\u2588\\u2557\\n" +
"  \\u2588\\u2588\\u2551      \\u2588\\u2588\\u2551     \\u2588\\u2588\\u2554\\u2550\\u2550\\u2588\\u2588\\u2551 \\u2588\\u2588\\u2551   \\u2588\\u2588\\u2551 \\u2588\\u2588\\u2551  \\u2588\\u2588\\u2551 \\u255a\\u2550\\u2550\\u2550\\u2550\\u255d\\u2588\\u2588\\u2554\\u2550\\u2550\\u255d\\n" +
"  \\u255a\\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2557 \\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2557\\u2588\\u2588\\u2551  \\u2588\\u2588\\u2551 \\u255a\\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2554\\u255d \\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2554\\u255d       \\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2588\\u2557\\n" +
"   \\u255a\\u2550\\u2550\\u2550\\u2550\\u2550\\u255d \\u255a\\u2550\\u2550\\u2550\\u2550\\u2550\\u2550\\u255d\\u255a\\u2550\\u255d  \\u255a\\u2550\\u255d  \\u255a\\u2550\\u2550\\u2550\\u2550\\u2550\\u255d  \\u255a\\u2550\\u2550\\u2550\\u2550\\u2550\\u255d        \\u255a\\u2550\\u2550\\u2550\\u2550\\u2550\\u2550\\u255d";
function esc(x){const d=document.createElement("i");d.textContent=x==null?"":String(x);return d.innerHTML}
async function act(action, arg){
  const r = await fetch("/api/act",{method:"POST",
    headers:{"Content-Type":"application/json","X-Meditate":"1"},
    body: JSON.stringify({action, arg: arg||"", value:""})});
  const d = await r.json();
  const n = document.getElementById("say");
  if(n) n.textContent = (d.output||"").split("\\n")[0];
}
function btn(label, action, arg){
  return `<button onclick="act('${action}','${esc(arg||"")}')" style="background:none;border:1px solid #3a352c;border-radius:6px;color:${GOLD};padding:3px 10px;font-size:12px;cursor:pointer;margin-left:8px">${label}</button>`;
}
async function load(){
  const [t, s] = await Promise.all([
    fetch("/api/twin",{headers:{"X-Meditate":"1"}}).then(r=>r.json()),
    fetch("/api/state",{headers:{"X-Meditate":"1"}}).then(r=>r.json())]);
  const gate = ((s.timing||{}).state)||"";
  const awake = !!s.fleet_running || gate==="pause" || gate==="settled";
  document.getElementById("face").textContent =
    "             \\u250c\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2510\\n" +
    (awake ? "             \\u2502   \\u25c9       \\u25c9   \\u2502   awake \\u2014 acting, or free to"
           : "             \\u2502   \\u2500\\u2500     \\u2500\\u2500   \\u2502   watching \\u2014 holding while you work") + "\\n" +
    "             \\u2514\\u2500\\u2500\\u2500\\u252c\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u252c\\u2500\\u2500\\u2500\\u2518";
  let h = '<div id="say" style="color:'+GOLD+';min-height:18px;font-size:13px"></div>';
  for (const sec of (t.sections||[])){
    h += `<div style="margin-top:22px"><div style="letter-spacing:.18em;font-size:11px;color:#6b6557">${esc(sec.title)}</div>`;
    for (const line of sec.lines){
      let extra = "";
      if (sec.title.startsWith("WHAT YOU COULD DO BETTER")){
        if (line.startsWith("broken")) extra = btn("repair it","fix","");
        if (line.startsWith("started and left")) extra = btn("what did I leave?","say","what did I leave unfinished");
      }
      h += `<div style="margin-top:6px;color:${FG};font-size:13px">${esc(line)}${extra}</div>`;
    }
    h += `<div style="color:#57524a;font-size:11px;margin-top:4px">basis: ${esc(sec.basis)}</div></div>`;
  }
  h += `<div style="margin-top:26px">${btn("start the fleet","go","")}${btn("re-check everything","grade","")}</div>`;
  document.getElementById("body").innerHTML = h;
}
load(); setInterval(load, 60000);
</script>
"""



PAGE = """<!doctype html><meta charset="utf-8">
<title>Pulse — your Claude, live</title>
<style>
  :root{
    --bg:#0b0a08; --panel:#100e0b; --line:#231f19; --line-soft:#191611;
    --ink:#e6e0d2; --ink-dim:#8a8578; --ink-faint:#6b6557;
    --gold:#E3B140; --gold-soft:#c9973a; --alert:#c96442;
    --r:12px; --s2:12px; --s3:20px; --s4:32px;
  }
  *{box-sizing:border-box}
  body{background:var(--bg);color:var(--ink);margin:0;
       font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;
       padding:var(--s4) 44px 64px;max-width:1120px;
       -webkit-font-smoothing:antialiased}
  a{color:var(--gold)}
  .eyebrow{letter-spacing:.32em;font-size:10.5px;color:var(--ink-faint);
           text-transform:uppercase}
  .lede{font-size:17px;line-height:1.6;color:var(--ink);max-width:74ch;
        margin:var(--s3) 0 var(--s2)}
  .card{background:var(--panel);border:1px solid var(--line);
        border-radius:var(--r);padding:16px 18px}
  .grid{display:grid;gap:14px}
  .cols-3{grid-template-columns:repeat(auto-fit,minmax(290px,1fr))}
  .stats{display:grid;gap:10px;
         grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
  .stat{background:var(--panel);border:1px solid var(--line-soft);
        border-radius:10px;padding:12px 14px}
  .muted{color:var(--ink-dim)}
  .faint{color:var(--ink-faint)}
  .sec{margin-top:var(--s4);display:block}
  .b{cursor:pointer;border:1px solid var(--line);background:transparent;
     color:var(--gold);border-radius:9px;padding:7px 14px;font-size:13px;
     transition:background .15s,border-color .15s}
  .b:hover{background:#1b1710;border-color:var(--gold-soft)}
  .goaltitle{width:250px;text-align:left;background:none;border:0;padding:0;
             color:var(--ink);font:inherit;cursor:pointer;
             text-decoration:underline;text-decoration-color:#2a2620;
             text-underline-offset:3px}
  .goaltitle:hover{color:var(--gold);text-decoration-color:var(--gold-soft)}
  .cap{font-size:10.5px;color:var(--ink-faint);margin-top:5px;line-height:1.4}
  details>summary{list-style:none;cursor:pointer;padding:10px 0}
  details>summary::-webkit-details-marker{display:none}
  details>summary::before{content:"\25b8  ";color:var(--ink-faint)}
  details[open]>summary::before{content:"\25be  "}
</style>
<body>
<div class="eyebrow">Meditate \u00b7 Pulse</div>
<div style="font-size:22px;margin:6px 0 2px;color:#E3B140">Pulse <span style="font-size:13px;color:#8a8578">· your sessions, goals, memory and fleet — live. One click runs, and shows what ran.</span></div>
<div id="meta" style="font-size:12px;color:#8a8578"></div>
<div id="brief" class="lede"></div>
<div id="headline" class="muted" style="font-size:13px"></div>
<div id="next" style="margin:4px 0 14px;color:#E3B140"></div>
<div class="grid cols-3" style="margin-top:18px">
  <div class="card"><span class="eyebrow" style="color:var(--alert)">Needs you</span>
    <div id="needs" style="font-size:12.5px;margin-top:8px"></div></div>
  <div class="card"><span class="eyebrow" style="color:var(--alert)">Chats waiting on you</span>
    <div id="owed" style="font-size:12.5px;margin-top:8px"></div>
    <div id="owedrest" class="faint" style="font-size:11px;margin-top:8px"></div></div>
  <div class="card"><span class="eyebrow">Moving by itself</span>
    <div id="moving" style="font-size:12.5px;margin-top:8px"></div></div>
</div>
<div style="display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start">
  <div style="max-width:180px"><button onclick="act('go','')" class="b" title="Opens one Terminal window per goal below, each with an agent working that goal's next milestone. Repair goes first if the queue is open.">launch fleet</button>
    <div class="cap">opens one Terminal agent per goal below — repair first if open</div></div>
  <div style="max-width:180px"><button onclick="act('fix','')" class="b" title="Opens one Terminal agent that re-checks and fixes the failed memories listed under REPAIR QUEUE.">repair all</button>
    <div class="cap">one agent to fix every failed memory in the repair queue</div></div>
  <div style="max-width:180px"><button onclick="act('grade','')" class="b" title="Re-verifies every memory against reality in the background. Nothing opens; the numbers refresh as it finishes.">grade now</button>
    <div class="cap">re-check all memories against reality (background, ~1 min)</div></div>
  <span id="toast" style="color:#8a8578;font-size:12px;align-self:center"></span>
</div>
<pre id="out" style="display:none;background:#12100c;border:1px solid #2a2620;border-radius:8px;padding:10px 14px;font-size:12px;color:#d8d2c4;white-space:pre-wrap;margin:10px 0 0"></pre>
<style>.b{cursor:pointer;border:1px solid #2a2620;background:transparent;color:#E3B140;border-radius:7px;padding:6px 13px;font-size:13px}.b:hover{background:#1d1a14}
.cap{font-size:10.5px;color:#6b6557;margin-top:4px;line-height:1.35}</style>
<details style="margin-top:18px"><summary style="cursor:pointer;letter-spacing:.3em;font-size:11px;color:#6b6557">THE NUMBERS <span style="letter-spacing:0;color:#4a463c">— every table the sentences above were computed from</span></summary>
<div id="stats" class="stats" style="margin:18px 0"></div>
<div style="font-size:11.5px;color:#6b6557;max-width:760px;margin:-4px 0 4px">what "a memory" means here: one fact about your work, saved with a receipt — the exact file and line it came from. Facts are re-checked against reality; a fact that stops matching goes to the repair queue instead of being trusted. Hover any number for its meaning.</div>
<div class="sec"><span class="eyebrow">Live Sessions</span> <span style="letter-spacing:0;color:#4a463c">— each orb beats with its session: fast = working right now, dim ember = gone quiet (prāṇa, the breath)</span></div>
<div id="live" style="display:flex;flex-wrap:wrap;gap:26px;margin-top:14px"></div>
<style>
@keyframes prana {
  0%,100% { transform:scale(1);    box-shadow:0 0 6px 1px rgba(227,177,64,.25); }
  50%     { transform:scale(1.18); box-shadow:0 0 22px 6px rgba(227,177,64,.55); }
}
.orb { width:44px;height:44px;border-radius:50%;
       background:radial-gradient(circle at 35% 35%, #f5d68a, #E3B140 55%, #6b4e12);
       animation:prana 2s ease-in-out infinite; margin:0 auto 8px; }
</style>
<div class="sec"><span class="eyebrow">Projects</span> <span style="letter-spacing:0;color:#4a463c" id="projlabel">— recent attention vs the repo's whole history</span></div>
<div id="projects" style="font-size:13px;margin-top:8px"></div>
</details>
<div class="sec"><span class="eyebrow">Goals</span> <span style="letter-spacing:0;color:#4a463c">— stuck first, then closest to done</span></div>
<div id="mile" style="font-size:12px;margin:6px 0"></div>
<div id="goals"></div>
<div class="sec"><span class="eyebrow">Fleet</span> </div>
<div id="fleet" style="font-size:13px"></div>
<div class="sec"><span class="eyebrow">Repair Queue</span> <span style="letter-spacing:0;color:#4a463c">— facts whose receipts stopped matching reality; not trusted until fixed</span></div>
<div id="repair" style="font-size:13px"></div>
<div class="sec"><span class="eyebrow">Activity</span> </div>
<div id="activity" style="font-size:12px;color:#8a8578"></div>
<div id="digest" style="margin-top:24px;font-size:12px;color:#8a8578"></div>
<div style="margin-top:6px;font-size:11px;color:#6b6557">agents run in Terminal windows on this Mac; they appear in LIVE SESSIONS as they work, and milestones tick only when their work verifies</div>
<script>
const G="#E3B140", DIM="#8a8578";
async function act(action, arg, value){
  const t=document.getElementById("toast"), o=document.getElementById("out");
  t.textContent = "running " + action + " " + (arg||"") + "…";
  try{
    const r=await fetch("/api/act",{method:"POST",
      headers:{"Content-Type":"application/json","X-Meditate":"1"},
      body:JSON.stringify({action,arg,value})});
    const j=await r.json();
    t.textContent = j.started ? "done: "+action+" "+(arg||"") : "refused";
    o.style.display="block"; o.textContent = j.output || "(no output)";
  }catch(e){ t.textContent="failed: "+e }
  setTimeout(tick, 1200);
}
function esc(s){return String(s||"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function bar(p){return `<span style="display:inline-block;width:180px;height:8px;background:#1d1a14;border-radius:4px;vertical-align:middle"><span style="display:block;width:${Math.min(100,p)}%;height:8px;background:${G};border-radius:4px"></span></span>`}

// The organs that move on their own. Driven by tick() AND by the push stream,
// so a dispatched agent's first line of progress does not wait for the next
// 4-second poll to be seen.
function renderLive(s){
  if (s.live_sessions) document.getElementById("live").innerHTML = s.live_sessions.map(x=>{
    // the beat IS the recency: <60s -> ~1.1s fast pulse; slows with age;
    // >30 min -> a still ember (no animation, dim)
    const beat = Math.min(6, Math.max(1.1, x.age_s/45));
    const ember = x.age_s > 1800;
    const glow = ember ? "animation:none;opacity:.35;filter:saturate(.5)"
                       : `animation-duration:${beat.toFixed(1)}s`;
    return `<div style="width:140px;text-align:center">
      <div class="orb" style="${glow}" title="session ${esc(x.sid)} · ${esc(x.cwd)}"></div>
      <div style="font-size:12.5px;color:${G};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.label)}</div>
      <div style="font-size:11px;color:${DIM};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.last_file)}</div>
      <div style="font-size:10px;color:#4a463c">${x.age_s<60?x.age_s+"s":Math.round(x.age_s/60)+"m"} ago
        · <a href="#" class="j-name" data-sid="${esc(x.sid)}" data-label="${esc(x.label)}" style="color:${DIM}" title="Rename this session in your own words.">name</a>
        · <a href="#" class="j-stop" data-sid="${esc(x.sid)}" data-label="${esc(x.label)}" style="color:${DIM}" title="End this session — same as closing its window.">stop</a></div>
    </div>`}).join("") || `<div style="color:${DIM};font-size:13px">no living sessions — the field is still</div>`;
  const ml = s.milestones||{looks_done:[],stale_wording:[]};
  const mparts = (ml.looks_done||[]).map(m=>
      `<div style="color:${G}">✓ looks already done: ${esc(m.milestone)} <span style="color:${DIM}">— ${esc(m.evidence)}</span></div>`)
    .concat((ml.stale_wording||[]).map(m=>
      `<div style="color:${DIM}">status frozen in the text ${esc(m.phrase)} — ${esc(m.milestone)}</div>`));
  document.getElementById("mile").innerHTML = mparts.join("");
  const onGoal = {};
  (s.fleet||[]).forEach(f=>{ if(f.goal) onGoal[f.goal] = f; });
  if (s.goals) document.getElementById("goals").innerHTML = s.goals.map(g=>{
    const f = onGoal[g.name];
    const working = f
      ? `<span style="color:${G}" title="agent live on this goal${f.last_file?` — last touched ${esc(f.last_file)}`:""}">⟳ agent on it · ${Math.round(f.dispatched_min)}m${f.milestone_ticked?` · milestone ✓`:""}</span>`
      : `<button class="b j-go" data-goal="${esc(g.name)}" style="padding:2px 9px;font-size:11px" title="Opens ONE Terminal agent working only this goal's next milestone: ${esc(g.next||'')}">dispatch</button>`;
    return `<div style="margin:8px 0"><div style="display:flex;gap:12px;align-items:center">
      <button class="j-open goaltitle" data-goal="${esc(g.name)}"
              title="open this goal">${esc(g.title.slice(0,42))}</button>${bar(g.pct)}
      <span style="color:${G}">${Math.round(g.pct)}%</span>
      <span style="color:${DIM}">${g.done}/${g.total}</span>
      ${g.stalled?`<span style="color:${G}" title="${esc(g.idle_basis||'')}">stuck ${g.idle_days}d</span>`:""}
      ${g.scope_delta>0?`<span style="color:${G}">scope +${g.scope_delta}</span>`:""}
      ${working}</div>
      <div style="margin-left:262px;font-size:12px;color:${DIM}">next: ${esc(g.next||"—")}</div>
      <div data-goalbox="${esc(g.name)}" style="margin-left:262px"></div></div>`;
  }).join("");
  restoreOpenGoals();
  if (s.fleet) document.getElementById("fleet").innerHTML = s.fleet.map(f=>{
    const status = f.says
      ? `<span style="color:${f.says_done?G:'#d8d2c4'}">${f.says_done?'✓ ':'▸ '}${esc(f.says)}</span> <span style="color:#4a463c">(${esc(f.says_ts||'')})</span>`
      : (f.milestone_ticked?`<span style="color:${G}">milestone done ✓</span>`
         : (f.live_session?`working — ${esc(f.last_file||'')}`:`<span style="color:${DIM}">launched, no report yet</span>`));
    const stale = f.milestone_ticked || f.dispatched_min >= 15;
    const btn = stale
      ? `<button class="b j-clear" data-goal="${esc(f.goal)}" style="padding:1px 8px;font-size:11px"
          title="Removes this row from the dispatch ledger. It does NOT stop a running agent — that window is yours to close.">clear</button>`
      : "";
    return `<div style="margin:5px 0"><span style="color:${G}">${esc(f.goal)}</span>
      <span style="color:#4a463c">· sent ${f.dispatched_min}m ago ·</span> ${status} ${btn}</div>`;
  }).join("") || `<div style="color:${DIM}">nothing dispatched — press a goal's <b>dispatch</b>, or <code style="color:${G}">meditate go</code></div>`;
  if ((s.fleet||[]).some(f=>f.milestone_ticked))
    document.getElementById("fleet").insertAdjacentHTML("beforeend",
      `<div style="margin-top:6px"><button class="b j-clearall" style="padding:1px 8px;font-size:11px"
        title="Drops every finished row at once.">clear finished</button></div>`);
  if (s.repair) document.getElementById("repair").innerHTML = s.repair.map((m,i)=>
    `<div style="margin:4px 0"><span style="color:${G}">${i+1}.</span> ${esc(m.statement)}
     <button class="b j-fix" data-n="${i+1}" style="padding:1px 8px;font-size:11px" title="Opens ONE Terminal agent scoped to only this memory: it checks reality, fixes the .md, and re-grades.">fix this</button>
     ${m.fails.map(f=>`<div style="margin-left:18px;color:${DIM};font-size:12px">FAILS ${esc(f)}</div>`).join("")}</div>`
  ).join("") || `<div style="color:${DIM}">clean — nothing failed verification</div>`;
  const tot = (s.projects||[]).reduce((a,p)=>a+p.messages,0)||1;
  document.getElementById("brief").innerHTML =
    (s.brief||[]).map(x=>esc(x)).join(" ") ||
    `<span style="color:${DIM}">reading the room…</span>`;
  const wd = s.projects_window_days||0;
  if (wd) document.getElementById("projlabel").textContent =
    `\u2014 % is share of the last ${wd} days of chats; commits are the whole history`
    + (s.facts_unattributed ? `; ${s.facts_unattributed} facts name no project` : "");
  document.getElementById("projects").innerHTML = (s.projects||[]).map(p=>{
    const share = 100*p.messages/tot;
    const hist = p.commits ? `${p.commits} commits since ${(p.since||"").slice(0,7)}`
                           + (p.commits_recent?` · ${p.commits_recent} this month`:"")
                           : "no repo";
    return `<div style="margin:7px 0">
      <div style="display:flex;gap:12px;align-items:center">
        <span style="width:140px;color:${G}">${esc(p.project)}</span>
        ${bar(share,140)}
        <span style="color:${DIM};width:150px">${share.toFixed(0)}% · ${p.messages} msgs · ${p.sessions} chats</span>
        <span style="color:${DIM};width:230px">${esc(hist)}</span>
        <span style="color:${DIM}">${p.facts} facts${p.repair_items?` · <span style="color:${G}">${p.repair_items} to fix</span>`:""}</span>
      </div>
      ${(p.open_tasks||[]).map(t=>`<div style="margin-left:152px;font-size:11.5px;color:${DIM}">↳ ${esc(t.task)}</div>`).join("")}
    </div>`}).join("") || `<div style="color:${DIM}">no project data yet</div>`;
  document.getElementById("activity").innerHTML = (s.activity||[]).map(a=>
    `<div>${esc(a.ts)} · ${esc(a.type)} · ${esc(a.what)}</div>`).join("") ||
    "<div>no recorded activity yet</div>";
}

async function tick(){
  let s; try{ s = await (await fetch("/api/state")).json() }catch(e){ return }
  // once the push stream is live it owns this line — otherwise the 4s poll
  // overwrites "live" with "refreshed" and the page understates itself
  if (!STREAMED) document.getElementById("meta").textContent =
    `refreshed ${s.generated} · every number from the graded store, not recall`;
  document.getElementById("next").textContent = "next: " + s.next;
  const ins = s.insights||{headline:"",needs_you:[],moving:[]};
  document.getElementById("headline").textContent = ins.headline||"";
  document.getElementById("needs").innerHTML = (ins.needs_you||[]).map(x=>
    `<div style="margin:2px 0">• ${esc(x)}</div>`).join("") || `<div style="color:${DIM}">nothing — all clear</div>`;
  const tri = s.triage||{counts:{},action_items:[],skipped:0};
  const owed = tri.action_items||[];
  document.getElementById("owed").innerHTML = owed.length ? owed.map(x=>{
    const verb = x.action === "reply" ? "reply" : "resume";
    const age = x.age_h < 48 ? Math.round(x.age_h)+"h" : Math.round(x.age_h/24)+"d";
    return `<div style="margin:4px 0">`
      + `<a href="#" data-open="${esc(x.id)}" style="color:#c96442;text-decoration:none">${verb}</a>`
      + ` <span style="color:#6f6a5f">${age}</span> `
      + `<span style="color:#d8d2c4">${esc(x.last_said||"")}</span></div>`;
  }).join("") : `<div style="color:${DIM}">nothing is waiting on you</div>`;
  const c = tri.counts||{};
  document.getElementById("owedrest").textContent =
    `${(c.resumable||0)} could be picked up · ${(c.finished||0)} finished · `
    + `${(c.stale||0)} gone quiet · ${tri.skipped||0} tool calls ignored`;

  document.getElementById("moving").innerHTML = (ins.moving||[]).map(x=>
    `<div style="margin:2px 0;color:#d8d2c4">▸ ${esc(x)}</div>`).join("") || `<div style="color:${DIM}">no agents reporting</div>`;
  const v = s.store.active? (100*s.store.verified/s.store.active).toFixed(1):"0";
  const stat=(val,lab,tip)=>`<div class="stat" title="${tip||""}">`
    +`<div style="font-size:25px;line-height:1.1;color:${G};font-variant-numeric:tabular-nums">${val}</div>`
    +`<div class="muted" style="font-size:11px;margin-top:4px">${lab}</div></div>`;
  document.getElementById("stats").innerHTML =
    stat(s.store.active,"facts it knows",
      "A memory here = one fact about your work, saved with a receipt: the exact file and line it came from, so it can be re-checked forever.")+
    stat(v+"%","still true when re-checked",
      "Every fact's receipt is re-checked against the real files. This is the share that still matches reality right now.")+
    stat(s.store.formed,"learned by itself",
      "Facts the system wrote on its own from your git commits — your commit messages become memory automatically.")+
    stat(s.wins.caught+" / "+s.wins.repaired,"broken by reality / fixed",
      "When the world changes under a fact (a file moves, a claim goes stale), it is caught and stops being trusted. Fixed = someone repaired it and the re-check passed.")+
    stat(s.sangama.facts_served,"facts handed to sessions",
      "When any session edits a file this system knows facts about, those facts are handed to it at that exact moment.")+
    stat(s.stilling.sessions_archived,"old chats tidied away",
      "Empty or finished sessions moved out of your session list — reversible, nothing is ever deleted.")+
    stat((s.heartbeat_h==null?"—":s.heartbeat_h+" h"),"since last self-check",
      "Every 6 hours the system re-checks all facts, learns from new commits, and tidies up — without being asked.");
  renderLive(s);
  document.getElementById("digest").textContent = s.digest || "";
}
// Clicking a goal opens it. The bar could only say "37%, next: X"; every
// other question meant opening a markdown file by hand.
//
// The opened panel is redrawn after every poll. The goals list is rebuilt
// wholesale every 4 seconds, which silently threw the panel away one tick
// after you opened it — the click looked broken when it had worked.
const OPEN = {};      // name -> last fetched detail

function renderGoal(name){
  const box = document.querySelector('[data-goalbox="' + name + '"]');
  const g = OPEN[name];
  if(!box || !g) return;
  if(g === true){
    box.innerHTML = `<div class="faint" style="font-size:12px;margin:6px 0">opening…</div>`;
    return;
  }
  if(g.error){ box.innerHTML = `<div class="faint">${esc(g.error)}</div>`; return; }

  const rows = (g.milestones||[]).map(m=>{
    const mark = m.done ? `<span style="color:${G}">\u2713</span>`
                        : `<span class="faint">\u25cb</span>`;
    let note = "";
    if(!m.done && m.verdict === true)
      note = `<div style="color:${G};font-size:11.5px;margin-left:18px">looks already done \u2014 ${esc(m.evidence||"")}</div>`;
    else if(!m.done && m.verdict === false)
      note = `<div class="faint" style="font-size:11.5px;margin-left:18px">checked, still open \u2014 ${esc(m.evidence||"")}</div>`;
    if(m.stale_wording)
      note += `<div class="faint" style="font-size:11.5px;margin-left:18px">status frozen in the text: ${esc(m.stale_wording)}</div>`;
    return `<div style="margin:3px 0">${mark} <span style="${m.done?'color:#6b6557':''}">${esc(m.text)}</span>${note}</div>`;
  }).join("");

  const agent = g.agent
    ? `<div style="margin-top:10px;font-size:12px"><span class="eyebrow">Agent</span><br><span style="color:#d8d2c4">${esc(g.agent.message)}</span> <span class="faint">${esc(g.agent.ts||"")}</span></div>`
    : "";
  const moved = (g.idle_days === null || g.idle_days === undefined)
    ? `<span class="faint">no movement recorded yet</span>`
    : `<span class="faint">last moved ${g.idle_days}d ago${g.stalled?" \u2014 stuck":""}</span>`;

  box.innerHTML = `<div class="card" style="margin:8px 0 12px">
      <div class="row" style="justify-content:space-between">
        <span class="eyebrow">${esc(g.project||"")} \u00b7 ${g.done}/${g.total} done</span>${moved}
      </div>
      <div style="margin-top:8px;font-size:12.5px">${rows}</div>
      ${g.note?`<div class="faint" style="font-size:11.5px;margin-top:8px;white-space:pre-wrap">${esc(g.note)}</div>`:""}
      ${agent}
      <div class="faint" style="font-size:11px;margin-top:8px">${esc(g.file||"")}</div>
    </div>`;
}

function restoreOpenGoals(){ Object.keys(OPEN).forEach(renderGoal); }

async function openGoal(name){
  if(OPEN[name]){                       // second click closes it
    delete OPEN[name];
    const box = document.querySelector('[data-goalbox="' + name + '"]');
    if(box) box.innerHTML = "";
    return;
  }
  OPEN[name] = true;
  renderGoal(name);
  try {
    OPEN[name] = await (await fetch("/api/goal?name=" + encodeURIComponent(name))).json();
  } catch(e) {
    OPEN[name] = {error: "could not read that goal"};
  }
  renderGoal(name);
}

document.addEventListener("click", e=>{
  const t = e.target.closest("a,button"); if(!t) return;
  if(t.classList.contains("j-name")){e.preventDefault();
    const v=prompt("Name this session (what is it working on?)", t.dataset.label||"");
    if(v!==null) act("name", t.dataset.sid, v);}
  else if(t.classList.contains("j-stop")){e.preventDefault();
    if(confirm(`Stop session "${t.dataset.label}"? Same as closing its window.`)) act("stop", t.dataset.sid);}
  else if(t.classList.contains("j-go")){act("go", t.dataset.goal);}
  else if(t.classList.contains("j-open")){openGoal(t.dataset.goal);}
  else if(t.classList.contains("j-fix")){act("fix", t.dataset.n);}
  else if(t.classList.contains("j-clear")){act("clear", t.dataset.goal);}
  else if(t.classList.contains("j-clearall")){act("clear", "");}
});
tick(); setInterval(tick, 4000);

// Push, not poll, for the fleet. The server watches the beacon and event
// files and sends only when they actually change; the fingerprint check costs
// nothing, so it can look three times a second.
let LAST = null, ES = null, STREAMED = false;
function connectStream(){
  try { if (ES) ES.close(); } catch(e){}
  ES = new EventSource("/api/stream");
  ES.onmessage = ev => {
    let d; try { d = JSON.parse(ev.data); } catch(e){ return; }
    LAST = Object.assign(LAST || {}, d);
    const meta = document.getElementById("meta");
    try { renderLive(LAST); } catch(e){}
    if (meta && d.at) meta.textContent =
      "live \u00b7 pushed " + d.at + " \u00b7 every number from the graded store, not recall";
    STREAMED = true;
  };
  // a dropped stream is not a dead page: come back
  ES.onerror = () => { try { ES.close(); } catch(e){} setTimeout(connectStream, 3000); };
}
connectStream();
</script></body>"""


def _host_ok(handler) -> bool:
    """Reject any request whose Host isn't loopback — defeats DNS rebinding,
    where attacker.com resolves to 127.0.0.1 and the custom header no longer
    helps because the page is 'same-origin'."""
    host = (handler.headers.get("Host") or "").split(":")[0].strip().lower()
    return host in ("127.0.0.1", "localhost", "[::1]", "::1", "")


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            # CSRF guard: any web page can POST to localhost, but a custom
            # header forces a CORS preflight we never answer. No header = 403.
            if not _host_ok(self):
                self.send_error(403, "bad Host")
                return
            if self.path != "/api/act":
                self.send_error(404)
                return
            if self.headers.get("X-Meditate") != "1":
                self.send_error(403, "missing X-Meditate header")
                return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                req = {}
            action = str(req.get("action") or "")
            arg = str(req.get("arg") or "")
            if action == "say":
                # the mascot's mouth+ear: one conversational turn over the
                # graded data. allow_actions stays FALSE by default — the
                # turn TELLS you the command; the page confirms before running.
                import converse as cv
                res = cv.turn(str(req.get("value") or arg or ""),
                              allow_actions=bool(req.get("allow_actions")))
                _log_brain_action("say", res.get("intent", ""))
                body = json.dumps({"started": True, "action": "say",
                                   "arg": arg, "turn": res,
                                   "output": res.get("speech", "")}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if action == "name":
                set_name(arg, str(req.get("value") or ""))
                res = {"started": True, "output": "named"}
            elif action == "stop":
                res = stop_session(arg)
            elif action == "stopfleet":
                # distinct from "stop", which halts ONE named session. This
                # closes every agent this tool dispatched, and nothing else.
                try:
                    from drive import stop_fleet
                    r = stop_fleet()
                    res = {"started": True,
                           "output": ("nothing was running" if not r["was_running"]
                                      else "stopped %d agent(s)" % r["count"])}
                except Exception as e:
                    res = {"started": False, "output": "could not stop: %s" % e}
            elif action in ("backlog", "unbacklog"):
                # Putting something down is a decision, and the tool has to
                # remember it. Every item used to be re-offered forever and
                # the only way to stop one was to finish it.
                try:
                    import backlog as _b
                    r = (_b.add(arg, str(req.get("value") or ""))
                         if action == "backlog" else _b.remove(arg))
                    res = {"started": r["ok"],
                           "output": ("put down — %d in the backlog" % r["total"])
                           if r["ok"] and action == "backlog" else
                           ("brought back — %d left" % r["total"])
                           if r["ok"] else r["why"]}
                except Exception as e:
                    res = {"started": False, "output": str(e)[:160]}
            elif action == "plan-all":
                # Build the whole-graph plan. Elaboration calls a planner per
                # open milestone (read-only, ~$0.35 each, minutes) so it runs
                # detached; the page polls /api/campaign until a plan exists.
                try:
                    _log_brain_action("plan-all", arg)
                    cmd = ["python3", os.path.join(SKILL_DIR, "campaign.py"), "plan"]
                    if arg == "fast":
                        cmd.append("--no-elaborate")
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
                    res = {"started": True,
                           "output": "planning every goal — a read-only planner per "
                                     "open milestone; the page fills in as it lands"
                                     if arg != "fast" else
                                     "planning from the goal files only — no planner calls"}
                except Exception as e:
                    res = {"started": False, "output": str(e)[:160]}
            elif action == "go-all":
                # THE go. Arms the campaign and sends the first ready wave.
                try:
                    import campaign as _cp
                    _log_brain_action("go-all", arg)
                    r = _cp.go(max_parallel=int(arg) if str(arg).isdigit() else None)
                    m = r.get("metrics") or {}
                    res = {"started": bool(r.get("armed")),
                           "output": ("armed — sent %d now; %d ready, %d steps, up to $%.2f"
                                      % (len(r.get("dispatched", [])), m.get("ready", 0),
                                         m.get("nodes", 0), m.get("est_usd", 0)))
                           if r.get("armed") else r.get("why", "not armed")}
                except Exception as e:
                    res = {"started": False, "output": str(e)[:160]}
            elif action == "pause-all":
                try:
                    import campaign as _cp
                    _log_brain_action("pause-all", arg)
                    r = _cp.pause(why=arg or "paused from the console")
                    res = {"started": True, "output": "paused — " + r.get("why", "")}
                except Exception as e:
                    res = {"started": False, "output": str(e)[:160]}
            elif action in ("discard-proposed", "discard-goal"):
                # The owner says no. Reversible: files move under .discarded,
                # store rows go inactive, the ledger keeps it from resurfacing.
                try:
                    import campaign as _cp
                    _log_brain_action(action, arg)
                    why = str(req.get("value") or "").strip()
                    r = (_cp.discard_proposed(arg, reason=why) if action == "discard-proposed"
                         else _cp.discard_goal(arg, reason=why))
                    if r.get("ok"):
                        moved = r.get("moved") or ", ".join(os.path.basename(x) for x in r.get("memories_moved", []))
                        res = {"started": True,
                               "output": "discarded %s — %s; %d store row%s set inactive; reversible: campaign restore %s"
                                         % (arg, ("memory moved: " + os.path.basename(moved)) if moved else "no memory to move",
                                            r.get("tombstoned", 0), "" if r.get("tombstoned", 0) == 1 else "s", arg)}
                    else:
                        res = {"started": False, "output": r.get("why", "could not discard")}
                except Exception as e:
                    res = {"started": False, "output": str(e)[:160]}
            elif action == "human-done":
                # The owner did a thing only he could do. arg = node id,
                # value = an optional note. The goal file's box ticks when the
                # node is a milestone; whatever waited on it can move.
                try:
                    import campaign as _cp
                    _log_brain_action("human-done", arg)
                    r = _cp.done(arg, note=str(req.get("value") or "").strip())
                    res = {"started": bool(r.get("ok")),
                           "output": ("done — %d step%s can move now" % (len(r.get("unblocked", [])),
                                      "" if len(r.get("unblocked", [])) == 1 else "s"))
                           if r.get("ok") else r.get("why", "could not mark done")}
                except Exception as e:
                    res = {"started": False, "output": str(e)[:160]}
            elif action == "accept-goal":
                # A mined goal becomes a goal file; the next plan brings it in.
                try:
                    import campaign as _cp
                    _log_brain_action("accept-goal", arg)
                    r = _cp.accept_goal(arg)
                    res = {"started": bool(r.get("ok")),
                           "output": ("goal written: %s — re-plan to bring it in" % os.path.basename(r.get("path", "")))
                           if r.get("ok") else r.get("why", "could not accept")}
                except Exception as e:
                    res = {"started": False, "output": str(e)[:160]}
            elif action == "accept-idea":
                # An idea becomes a step only when the owner takes it.
                try:
                    import campaign as _cp
                    _log_brain_action("accept-idea", arg)
                    r = _cp.accept(arg)
                    res = {"started": bool(r.get("ok")),
                           "output": ("accepted — it runs after the goal's last open step")
                           if r.get("ok") else r.get("why", "could not accept")}
                except Exception as e:
                    res = {"started": False, "output": str(e)[:160]}
            elif action == "steer":
                # A correction mid-flight: arg = node id, value = the message.
                try:
                    import campaign as _cp
                    msg = str(req.get("value") or "").strip()
                    if not msg:
                        res = {"started": False, "output": "say what to change"}
                    else:
                        _log_brain_action("steer", arg)
                        r = _cp.steer(arg, msg)
                        res = {"started": bool(r.get("ok")),
                               "output": ("steering %s" % r.get("node")) if r.get("ok")
                               else r.get("why", "could not steer")}
                except Exception as e:
                    res = {"started": False, "output": str(e)[:160]}
            elif action == "tick":
                # Close the milestone you were just told about, from wherever
                # you are. Being told about work you cannot act on is nagging.
                try:
                    import goals as _g
                    r = _g.tick(arg, str(req.get("value") or "") or None)
                    res = {"started": r["ok"],
                           "output": ("closed “%s” on %s — %d left"
                                      % (r["closed"], r["goal"], r["remaining"]))
                           if r["ok"] else r["why"]}
                except Exception as e:
                    res = {"started": False, "output": "could not close: %s" % e}
            elif action == "look":
                # Analyse it here rather than sending them to a file. detail()
                # already runs each open milestone past check_milestone, so
                # this is a wiring job, not a new judgement.
                try:
                    import goals as _g
                    d = _g.detail(arg)
                    if not d:
                        res = {"started": False, "output": "no goal called %s" % arg}
                    else:
                        nxt = next((m for m in d.get("milestones", [])
                                    if not m["done"]), None)
                        bits = ["%s is %d of %d done."
                                % (d["title"], d["done"], d["total"])]
                        if nxt:
                            bits.append("Next: %s."
                                        % (nxt.get("headline") or nxt["text"]))
                            if nxt.get("verdict"):
                                bits.append("Checking it: %s." % nxt["verdict"])
                            if nxt.get("evidence"):
                                bits.append(str(nxt["evidence"])[:200])
                        if d.get("agent", {}).get("message"):
                            bits.append("Someone is on it: %s"
                                        % d["agent"]["message"][:160])
                        res = {"started": True, "output": " ".join(bits)}
                except Exception as e:
                    res = {"started": False, "output": "could not look: %s" % e}
            elif action in ACTIONS:
                res = ACT_RUNNER(action, arg)
            else:
                self.send_error(400, "unknown action")
                return
            if not isinstance(res, dict):
                res = {"started": bool(res), "output": ""}
            _log_brain_action(action, arg)
            body = json.dumps({"started": bool(res.get("started")),
                               "action": action, "arg": arg,
                               "output": res.get("output", "")}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def do_GET(self):
        try:
            if not _host_ok(self):
                self.send_error(403, "bad Host")
                return
            if self.path == "/api/state":
                body = json.dumps(state()).encode()
                ctype = "application/json"
            elif self.path == "/api/stream":
                # Server-sent events: the fleet moves when an AGENT moves, not
                # when a timer says so. Polling every 4s meant a dispatched
                # agent's first line of progress could sit unseen for most of
                # those 4 seconds, which is what "not live enough" looks like.
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                last = ""
                try:
                    for _ in range(4000):        # ~20 min, then the page reconnects
                        fp = _live_fingerprint()
                        if fp != last:
                            last = fp
                            body = json.dumps(_live_payload())
                            self.wfile.write(b"data: " + body.encode() + b"\n\n")
                            self.wfile.flush()
                        else:
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                        time.sleep(0.3)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            elif self.path.startswith("/api/goal"):
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                name = (q.get("name") or [""])[0]
                try:
                    from goals import detail
                    d = detail(name)
                except Exception as e:
                    d = {"error": str(e)[:200]}
                if d is None:
                    d = {"error": "no such goal: %s" % name}
                body = json.dumps(d).encode()
                ctype = "application/json"
            elif self.path == "/api/spend":
                try:
                    import models as _md
                    _md.reconcile()          # fold in anything that just finished
                    body = json.dumps(_md.spend()).encode()
                except Exception as e:
                    body = json.dumps({"runs": 0, "rows": [], "per_model": [],
                                       "error": str(e)[:160]}).encode()
                ctype = "application/json"
            elif self.path == "/api/swarm":
                body = json.dumps(_swarm_cached()).encode()
                ctype = "application/json"
            elif self.path == "/api/campaign":
                # The all-goals run: the graph, its numbers, and whether it
                # is armed. Read every time — it changes every tick.
                try:
                    import campaign as _cp
                    body = json.dumps(_cp.status()).encode()
                except Exception as e:
                    body = json.dumps({"id": "", "armed": False, "metrics": {},
                                       "nodes": [], "why": str(e)[:160]}).encode()
                ctype = "application/json"
            elif self.path == "/api/twin":
                body = json.dumps(_twin_cached()).encode()
                ctype = "application/json"
            elif self.path == "/twin":
                # From disk, so the console is the twin's own file rather
                # than a string living inside the server — the same
                # separation the layer ratchet protects. The inline page
                # stays as the fallback for a machine without it.
                try:
                    with open(os.path.join(SKILL_DIR, "twin_console.html")) as _f:
                        body = _f.read().encode()
                except OSError:
                    body = TWIN_PAGE.encode()
                ctype = "text/html; charset=utf-8"
            elif self.path == "/api/report":
                body = json.dumps(report_data()).encode()
                ctype = "application/json"
            elif self.path == "/report":
                body = REPORT_PAGE.encode()
                ctype = "text/html; charset=utf-8"
            elif self.path == "/":
                body = PAGE.encode()
                ctype = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                err = json.dumps({"error": str(e)[:200]}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err)
            except Exception:
                pass

    def log_message(self, *a):                       # quiet server
        pass


LIVE_FILES = [
    os.path.expanduser("~/.claude/coordination/fleet-beacons.jsonl"),
    os.path.expanduser("~/.claude/coordination/events.jsonl"),
    os.path.expanduser("~/.claude/coordination/sessions"),
    # Resolved, not hardcoded: this owner's goals happen to live in
    # ~/claude-sync/goals, nobody else's do. test_packaging catches it.
    paths.goals_dir(),
]


def _live_fingerprint() -> str:
    """Cheap stamp of everything that changes minute to minute."""
    parts = []
    for p in LIVE_FILES:
        try:
            st = os.stat(p)
            parts.append("%s:%d:%d" % (os.path.basename(p), st.st_mtime_ns,
                                       getattr(st, "st_size", 0)))
        except OSError:
            parts.append(os.path.basename(p) + ":-")
    return "|".join(parts)


def _live_payload() -> Dict[str, Any]:
    """Only the fast-moving organs. Rebuilding all of state() 3x a second
    would cost more than it shows."""
    out: Dict[str, Any] = {}
    try:
        from drive import fleet_status
        from beacon import latest as _beacons
        f = fleet_status()
        bs = _beacons()
        for r in f["dispatched"]:
            bd = bs.get(r.get("goal"))
            if bd:
                r["says"] = bd.get("message", "")
                r["says_done"] = bd.get("done", False)
                r["says_ts"] = bd.get("ts", "")[11:19]
        out["fleet"] = f["dispatched"]
    except Exception:
        out["fleet"] = []
    try:
        out["activity"] = _recent_events()
    except Exception:
        out["activity"] = []
    try:
        from coordination import live_sessions
        out["live_sessions"] = live_sessions()
    except Exception:
        out["live_sessions"] = []
    out["at"] = time.strftime("%H:%M:%S")
    return out


def make_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    # Fill the caches before anyone asks. The first poll costs ~5.7s cold, so
    # without this the console opens to an empty screen and fills in later —
    # which is exactly what "not connected enough" looks like.
    threading.Thread(target=_warm, daemon=True).start()
    return srv


def _warm() -> None:
    try:
        state()
    except Exception:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate pulse", description="Live brain server (localhost only)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args(argv)
    try:
        srv = make_server(args.port)
    except OSError:
        url = "http://127.0.0.1:%d" % args.port
        print("already running at %s — opening it" % url)
        if not args.no_open:
            os.system("open '%s' 2>/dev/null" % url)
        return 0
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    print("brain live at %s  (localhost only — Ctrl-C to stop)" % url)
    if not args.no_open:
        os.system("open '%s' 2>/dev/null" % url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstilled.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
