"""drive — dispatch a fleet of goal agents with one command.

The autonomous-driving layer, with the intention gate kept intact:

  meditate drive          # dry-run: which goals WOULD get an agent
  meditate drive --go 3   # launch up to 3 Terminal agents, one per goal,
                          # each on its goal's FIRST open milestone

Each agent gets the goals.kickoff prompt: drive the first open milestone to
verifiably done, tick the checkbox in the goal file, stop. Ship discipline
rides in via the SessionStart hook (commit local, push on explicit go), and
sangama keeps the fleet from stomping each other.

A dispatched goal enters COOLDOWN (4h): while an agent is presumed working
it, `drive` will not send another. The ledger (dispatch.jsonl) records every
send, so `report`-style tooling can audit the fleet later.

What this deliberately is NOT: a cron. A human runs `drive`. Scheduling it
is one launchd plist away — but an unattended goal loop is an unverified
intention, and this system does not serve unverified things. The owner
triggers; the fleet executes; the checkboxes come back ticked or they don't.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import signal
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

LEDGER_PATH = os.path.expanduser("~/.claude/meditation/dispatch.jsonl")
COOLDOWN_S = 4 * 3600


def _recent_dispatches(ledger_path: str) -> Dict[str, float]:
    """goal-name -> most recent dispatch epoch."""
    out: Dict[str, float] = {}
    if os.path.exists(ledger_path):
        with open(ledger_path, errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    out[r["goal"]] = max(out.get(r["goal"], 0), r.get("ts_epoch", 0))
                except Exception:
                    continue
    return out


def dispatchable(goals_dir: Optional[str] = None,
                 ledger_path: str = LEDGER_PATH,
                 history_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Open goals not inside a dispatch cooldown, least-done first."""
    import goals as gl
    kw = {}
    if goals_dir:
        kw["goals_dir"] = goals_dir
    if history_path:
        kw["history_path"] = history_path
    recent = _recent_dispatches(ledger_path)
    now = time.time()
    out, cooling = [], 0
    for g in gl.scan(**kw):
        if g["status"] in ("done", "paused") or g["done"] >= g["total"]:
            continue
        if now - recent.get(g["name"], 0) < COOLDOWN_S:
            cooling += 1
            continue
        out.append(g)
    out.sort(key=lambda g: g["pct"])          # furthest-behind goal first
    dispatchable.cooling = cooling            # side-channel for run()
    return out


def run(go: int = 0, goals_dir: Optional[str] = None,
        ledger_path: str = LEDGER_PATH, history_path: Optional[str] = None,
        launcher: Optional[Callable[[str, str, str], bool]] = None) -> Dict[str, Any]:
    import goals as gl
    cands = dispatchable(goals_dir, ledger_path, history_path)
    cooling = getattr(dispatchable, "cooling", 0)
    launched = 0
    sent = []
    if go > 0:
        if launcher is None:
            from launch import launch_claude as launcher  # type: ignore
        kw = {}
        if goals_dir:
            kw["goals_dir"] = goals_dir
        if history_path:
            kw["history_path"] = history_path
        for g in cands[:go]:
            k = gl.kickoff(g["name"], **kw)
            if not k:
                continue
            ok = False
            try:
                ok = bool(launcher(k["cwd"], k["prompt"], "goal-" + g["name"][:20]))
            except Exception:
                ok = False
            if not ok:
                continue
            launched += 1
            sent.append({"goal": g["name"], "milestone": g["next"]})
            try:
                os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
                with open(ledger_path, "a") as f:
                    f.write(json.dumps({
                        "goal": g["name"], "milestone": g["next"],
                        "ts_epoch": time.time(),
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                        # which Terminal window this agent got. Without it,
                        # "stop the fleet" can only mean killing every claude
                        # on the machine, including the one you are typing in.
                        "window_id": getattr(launcher, "last_window_id", ""),
                    }) + "\n")
            except OSError:
                pass
    return {"candidates": [{"goal": g["name"], "pct": g["pct"], "next": g["next"]}
                           for g in cands],
            "cooling": cooling, "launched": launched, "sent": sent}


from coordination import last_file as _last_file      # one definition, shared


def fleet_status(goals_dir=None, ledger_path=None, history_path=None):
    """Live progress of dispatched agents, best-effort but honest about it.

    Joins three durable sources: the dispatch ledger (what was sent, when),
    sangama presence (which sessions are LIVE and what files they touch),
    and the goal files (has the dispatched milestone been ticked?). Linking
    dispatch->session is by cwd match — labeled presumed, never certain.
    """
    import goals as gl
    from coordination import live_sessions
    lp = ledger_path or LEDGER_PATH
    kw = {}
    if goals_dir:
        kw["goals_dir"] = goals_dir
    if history_path:
        kw["history_path"] = history_path
    gmap = {g["name"]: g for g in gl.scan(**kw)}
    live = live_sessions()
    rows = []
    seen_sids = set()
    if os.path.exists(lp):
        last = {}
        for line in open(lp, errors="replace"):
            try:
                r = json.loads(line)
                last[r["goal"]] = r
            except Exception:
                continue
        now = time.time()
        for goal, r in sorted(last.items(), key=lambda kv: -kv[1].get("ts_epoch", 0)):
            g = gmap.get(goal)
            mins = int((now - r.get("ts_epoch", now)) / 60)
            ticked = bool(g) and g.get("next") != r.get("milestone")
            agent = None
            for s in live:
                gc = (g or {}).get("cwd", "")
                if gc and (s.get("cwd") == gc or s.get("cwd", "").startswith(gc.rstrip("/") + "/")):
                    agent = s
                    seen_sids.add(s.get("sid"))
                    break
            rows.append({"goal": goal, "milestone": r.get("milestone", "")[:70],
                         "dispatched_min": mins, "milestone_ticked": ticked,
                         "live_session": (agent or {}).get("sid", "")[:12] or None,
                         # Which Terminal window this agent got, so a click can
                         # take you TO it. Knowing a thing is running and having
                         # no way to reach it is half a feature.
                         "window_id": str(r.get("window_id") or ""),
                         "last_file": _last_file(agent) if agent else None})
    # A row with no milestone is not a job — it can never tick, so it reports
    # "still going, worth a look" for as long as the ledger keeps it.
    rows = [r for r in rows if (r.get("milestone") or "").strip()]
    others = [{"sid": s.get("sid", "")[:12], "cwd": s.get("cwd", ""),
               "age_s": s.get("_age_s"), "last_file": _last_file(s)}
              for s in live if s.get("sid") not in seen_sids]
    return {"dispatched": rows, "other_live_sessions": others}


def main(argv: Optional[List[str]] = None) -> int:
    if argv and argv[0] == "fleet":
        f = fleet_status()
        if "--json" in argv:
            print(json.dumps({"tool_name": "meditate_fleet", "success": True,
                              "data": f, "metadata": {}, "errors": []}, indent=2))
            return 0
        if not f["dispatched"]:
            print("No goal agents dispatched (ledger empty).")
        for r in f["dispatched"]:
            state = "milestone TICKED ✓" if r["milestone_ticked"] else "open"
            who = ("agent %s on %s (presumed by cwd)" % (r["live_session"], r["last_file"])
                   if r["live_session"] else "no live session seen")
            print("  %-26s sent %dm ago  %s — %s" % (r["goal"][:26], r["dispatched_min"], state, who))
            print("      milestone: %s" % r["milestone"])
        if f["other_live_sessions"]:
            print("Live sessions not tied to a dispatch:")
            for s in f["other_live_sessions"]:
                print("  %s  %s  (%ss ago)  %s" % (s["sid"], s["cwd"], s["age_s"], s["last_file"]))
        return 0

    ap = argparse.ArgumentParser(prog="meditate drive", description="Dispatch goal agents")
    ap.add_argument("--go", type=int, default=0, metavar="N",
                    help="launch up to N agents (default: dry-run)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    data = run(go=args.go)
    env = {"tool_name": "meditate_drive", "success": True, "data": data,
           "metadata": {"ledger": LEDGER_PATH, "cooldown_h": COOLDOWN_S / 3600,
                        "dry_run": args.go == 0},
           "errors": []}
    if args.json:
        print(json.dumps(env, indent=2))
        return 0
    if args.go == 0:
        print("Drive — dry run (add --go N to launch agents)")
        if not data["candidates"]:
            print("  nothing dispatchable"
                  + (" (%d goal(s) cooling down)" % data["cooling"] if data["cooling"] else ""))
        for c in data["candidates"]:
            print("  %-26s %5.1f%%  next: %s" % (c["goal"], c["pct"], c["next"]))
        if data["cooling"]:
            print("  (+%d in cooldown — an agent is presumed on them)" % data["cooling"])
    else:
        print("Launched %d agent(s):" % data["launched"])
        for s in data["sent"]:
            print("  %s -> %s" % (s["goal"], s["milestone"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

def _open_window_ids() -> set:
    """Terminal window ids that currently exist."""
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "Terminal" to return id of every window'],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return set()
    return {w.strip() for w in (r.stdout or "").split(",") if w.strip()}


def _alive(pid: int) -> bool:
    """Is this pid still running? The only honest test of a kill."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _tty_of(window_id: str) -> str:
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "Terminal" to return tty of selected tab of '
             'window id %s' % window_id],
            capture_output=True, text=True, timeout=8)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _agent_pids(tty: str) -> List[int]:
    """claude processes running on a terminal, by pid.

    Not the login shell and not zsh — those ignore TERM and survive on
    purpose. The agent is the thing we started and the only thing we kill.
    """
    dev = (tty or "").replace("/dev/", "")
    if not dev:
        return []
    try:
        r = subprocess.run(["ps", "-t", dev, "-o", "pid=,command="],
                           capture_output=True, text=True, timeout=8)
    except Exception:
        return []
    out = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, cmd = line.partition(" ")
        if "claude" not in cmd:
            continue
        try:
            out.append(int(pid))
        except ValueError:
            continue
    return out


def running_agents(ledger_path: str = None, max_age_h: float = 12.0):
    """Agents THIS tool dispatched that are still actually working.

    Keyed on the window id recorded at dispatch, then confirmed by finding a
    live claude process on that window's tty. Window-open alone was the wrong
    test: Terminal keeps a window after its shell exits (a preference we do
    not control), so a finished agent would have counted as running forever.
    """
    lp = ledger_path or LEDGER_PATH
    now = time.time()
    latest = {}
    try:
        with open(lp) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                wid = str(r.get("window_id") or "").strip()
                if not wid:
                    continue
                if r.get("event") == "stopped":
                    # only a HINT. Whether it is running is decided below by
                    # looking for the process, not by trusting our own note —
                    # a false "stopped" row once hid a live agent completely.
                    latest.pop(wid, None)
                    continue
                if now - r.get("ts_epoch", 0) > max_age_h * 3600:
                    continue
                latest[wid] = r
    except OSError:
        return []
    alive = _open_window_ids()
    out = []
    for w, r in latest.items():
        if w not in alive:
            continue
        pids = _agent_pids(_tty_of(w))
        if not pids:
            continue
        out.append({"window_id": w, "goal": r.get("goal", ""),
                    "milestone": r.get("milestone", ""),
                    "pids": pids,
                    "age_min": int((now - r.get("ts_epoch", now)) / 60)})
    return out


def stop_fleet(ledger_path: str = None):
    """End the agents we started, and nothing else.

    Success is THE AGENT IS GONE, not "the window closed". `close window id N`
    returns success and leaves a busy window open — measured: it reported
    "stopped 1 agent(s)" with the window still on screen — and even an idle
    window stays if Terminal is set to keep it. So: kill the claude processes
    on that window's tty, verify they are gone, then ask the window to close
    as a courtesy and do not claim it either way.
    """
    agents = running_agents(ledger_path)
    stopped, failed = [], []
    for a in agents:
        # Verify by PID, never by re-reading the tty. Once the window is
        # gone _tty_of returns "" and _agent_pids returns [] — absence of
        # evidence read as success, and it claimed "stopped 2 agents" with the
        # process still alive.
        for pid in a["pids"]:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception:
                pass
        deadline = time.time() + 5
        while time.time() < deadline and any(_alive(p) for p in a["pids"]):
            time.sleep(0.25)
        # a agent that ignores TERM still has to go
        for pid in a["pids"]:
            if _alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
        deadline = time.time() + 3
        while time.time() < deadline and any(_alive(p) for p in a["pids"]):
            time.sleep(0.2)
        gone = not any(_alive(p) for p in a["pids"])
        if gone:
            try:
                subprocess.run(
                    ["osascript", "-e",
                     'tell application "Terminal" to close window id %s'
                     % a["window_id"]], capture_output=True, timeout=8)
            except Exception:
                pass
        (stopped if gone else failed).append(a)
    if stopped:
        try:
            with open(ledger_path or LEDGER_PATH, "a") as f:
                for a in stopped:
                    f.write(json.dumps({
                        "goal": a["goal"], "event": "stopped",
                        "window_id": a["window_id"],
                        "ts_epoch": time.time(),
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                            time.gmtime()),
                    }) + "\n")
        except OSError:
            pass
    return {"closed": stopped, "failed": failed,
            "count": len(stopped), "was_running": len(agents)}
