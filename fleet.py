"""fleet — act on the agents you dispatched, instead of only watching them.

The fleet section listed what had been sent out and gave you nothing to do
about any of it. Six finished rows sat there reporting "sent 68m ago" with no
way to dismiss them, and three probe rows with no milestone at all reported
"88 minutes, worth a look" forever — a milestone that does not exist can never
tick, so the row could never resolve on its own.

    python3 fleet.py clear                  drop everything already finished
    python3 fleet.py clear <goal>           drop one goal's rows
    python3 fleet.py clear --dead           drop rows that can never finish

Clearing touches the dispatch ledger only. It does not stop a running agent —
an agent lives in its own Terminal window, and closing that window is yours to
do. This just stops the console reporting work that is over.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

LEDGER_PATH = os.path.expanduser("~/.claude/meditation/dispatch.jsonl")


def _rows(path: str) -> List[Dict[str, Any]]:
    out = []
    try:
        with open(path, errors="ignore") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
    except OSError:
        pass
    return out


def _ticked_map(ledger_path: str, goals_dir: Optional[str] = None
                ) -> Dict[str, bool]:
    """Which dispatched milestones are done, per the fleet's OWN verdict.

    Reimplementing "is this checked off" would be a second opinion that can
    disagree with the one on screen. There is one source of truth for that and
    it is drive.fleet_status().
    """
    out: Dict[str, bool] = {}
    try:
        import drive as dv
        kw = {"ledger_path": ledger_path}
        if goals_dir:
            kw["goals_dir"] = goals_dir
        for r in dv.fleet_status(**kw).get("dispatched", []):
            out[r.get("goal", "")] = bool(r.get("milestone_ticked"))
    except Exception:
        pass
    return out


def _known_goals(goals_dir: Optional[str] = None) -> set:
    """Goal names that actually exist as goal files."""
    try:
        import goals as gl
        kw = {"goals_dir": goals_dir} if goals_dir else {}
        return {g.get("name", "") for g in gl.scan(**kw)}
    except Exception:
        return set()


def clear(goal: Optional[str] = None, dead_only: bool = False,
          ledger_path: str = LEDGER_PATH,
          goals_dir: Optional[str] = None) -> Dict[str, Any]:
    """Drop rows from the ledger. Returns what went and what stayed."""
    rows = _rows(ledger_path)
    ticked = {} if goal else _ticked_map(ledger_path, goals_dir)
    known = _known_goals(goals_dir)
    keep, gone = [], []
    for r in rows:
        ms = (r.get("milestone") or "").strip()
        g = r.get("goal", "")
        # A row is DEAD when nothing could ever finish it: no milestone at
        # all, or a milestone on a goal that does not exist. Both showed up
        # here from tests writing into the live ledger — "prove red" against a
        # goal called probe-fleet-button, reporting 96 minutes and climbing,
        # with no way for it to ever resolve.
        orphan = bool(known) and g not in known
        dead = (not ms) or orphan
        if dead_only:
            drop = dead
        elif goal:
            drop = (g == goal)
        else:
            drop = dead or ticked.get(g, False)
        (gone if drop else keep).append(r)

    if gone:
        try:
            os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
            tmp = ledger_path + ".tmp"
            with open(tmp, "w") as f:
                for r in keep:
                    f.write(json.dumps(r) + "\n")
            os.replace(tmp, ledger_path)
        except OSError as e:
            return {"ok": False, "error": str(e), "cleared": 0,
                    "remaining": len(rows)}
    return {"ok": True, "cleared": len(gone), "remaining": len(keep),
            "goals": sorted({r.get("goal", "") for r in gone})}


# ---- closing up after an agent ---------------------------------------------

AGENT_LOGS = os.path.expanduser("~/.claude/meditation/agent-logs")


def _finished_goals(beacon_path: Optional[str] = None) -> Dict[str, str]:
    """Goals whose LATEST report says done, mapped to what they said."""
    try:
        import beacon as bc
        kw = {"beacon_path": beacon_path} if beacon_path else {}
        return {g: r.get("message", "")
                for g, r in bc.latest(**kw).items() if r.get("done")}
    except Exception:
        return {}


def close_finished(ledger_path: str = LEDGER_PATH,
                   beacon_path: Optional[str] = None,
                   goals_dir: Optional[str] = None,
                   force: bool = False) -> Dict[str, Any]:
    """Close the Terminal windows of agents that reported done.

    Three rules, because closing a window someone is reading is worse than
    leaving one open:

      1. Only windows THIS tool opened. The id is recorded at dispatch; a
         window with no recorded id is never touched.
      2. Only goals whose latest beacon says done. Still working means still
         open, however long it has taken.
      3. Never the window you are looking at, unless --force. A window that
         vanishes mid-read costs more than the clutter it saves.

    The window's text is saved before it closes, so closing costs you nothing.
    """
    finished = _finished_goals(beacon_path)
    if not finished:
        return {"ok": True, "closed": 0, "saved": [], "skipped": [],
                "why": "no agent has reported done"}

    wanted: Dict[str, str] = {}          # window id -> goal
    for r in _rows(ledger_path):
        wid = str(r.get("window_id") or "").strip()
        g = r.get("goal", "")
        if wid.isdigit() and g in finished:
            wanted[wid] = g
    if not wanted:
        return {"ok": True, "closed": 0, "saved": [], "skipped": [],
                "why": "finished agents, but no window ids were recorded for them"}

    closed, saved, skipped = [], [], []
    os.makedirs(AGENT_LOGS, exist_ok=True)
    for wid, g in wanted.items():
        text = _window_text(wid)
        if text is None:
            skipped.append({"goal": g, "why": "window already gone"})
            continue
        if not force and _is_frontmost(wid):
            skipped.append({"goal": g, "why": "you are looking at it"})
            continue
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(AGENT_LOGS, "%s-%s.txt" % (g, stamp))
        try:
            with open(path, "w") as f:
                f.write(text)
            saved.append(path)
        except OSError:
            pass
        if _close_window(wid):
            closed.append(g)
        else:
            skipped.append({"goal": g, "why": "Terminal kept the window open — "
                            "set Settings > Profiles > Shell > When the shell "
                            "exits: Close the window"})
    return {"ok": True, "closed": len(closed), "goals": closed,
            "saved": saved, "skipped": skipped}


def _osa(script: str) -> Optional[str]:
    try:
        import subprocess
        r = subprocess.run(["osascript", "-e", script], capture_output=True,
                           text=True, timeout=15)
        return r.stdout.rstrip("\n") if r.returncode == 0 else None
    except Exception:
        return None


def _window_text(wid: str) -> Optional[str]:
    return _osa('tell application "Terminal" to return contents of '
                'selected tab of window id %s' % wid)


def _is_frontmost(wid: str) -> bool:
    front = _osa('tell application "System Events" to return name of first '
                 'application process whose frontmost is true')
    if front != "Terminal":
        return False
    return _osa('tell application "Terminal" to return id of front window') == str(wid)


def _close_window(wid: str) -> bool:
    """Close it, then CHECK it is gone.

    An osascript exit code of 0 means the command was accepted, not that the
    window went away — Terminal can accept `close` and still leave the window
    up (a running process, a confirmation sheet). Reporting "closed: 1" while
    the window count stayed at 2 is exactly that, and it is the kind of lie
    that makes a cleanup feature worse than none.
    """
    # Exit the shell first, then ask the window to go. Measured on this Mac:
    # `close`, `close saving no`, and close-after-the-shell-exits were all
    # ACCEPTED by Terminal and all left the window standing, with no
    # confirmation sheet anywhere. So the last step depends on a Terminal
    # profile setting only the owner can change:
    #     Terminal > Settings > Profiles > Shell > When the shell exits:
    #         "Close the window"
    # With that set, the exit below closes it. Without it, this returns False
    # and the caller says so rather than pretending.
    _osa('tell application "Terminal" to do script "exit" in '
         '(selected tab of window id %s)' % wid)
    _osa('tell application "Terminal" to close (window id %s) saving no' % wid)
    still = _osa('tell application "Terminal" to return (count of '
                 '(every window whose id is %s))' % wid)
    return still == "0"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="meditate fleet", description="Act on dispatched agents")
    sub = ap.add_subparsers(dest="cmd")
    cl = sub.add_parser("close", help="close windows of agents that finished")
    cl.add_argument("--force", action="store_true",
                    help="close even the window you are looking at")
    cl.add_argument("--json", action="store_true")
    c = sub.add_parser("clear")
    c.add_argument("goal", nargs="?")
    c.add_argument("--dead", action="store_true",
                   help="only rows with no milestone, which can never finish")
    c.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "close":
        res = close_finished(force=a.force)
        if getattr(a, "json", False):
            print(json.dumps({"tool_name": "meditate_fleet", "success": res["ok"],
                              "data": res, "metadata": {}, "errors": []}, indent=2))
        elif res["closed"]:
            print("closed %d finished agent window(s): %s"
                  % (res["closed"], ", ".join(res.get("goals", []))))
            for p in res.get("saved", []):
                print("  saved  %s" % p)
        else:
            print(res.get("why") or "nothing to close")
        for sk in res.get("skipped", []):
            print("  left open: %s — %s" % (sk["goal"], sk["why"]))
        return 0
    if a.cmd != "clear":
        ap.print_help()
        return 1
    res = clear(goal=a.goal, dead_only=a.dead)
    if getattr(a, "json", False):
        print(json.dumps({"tool_name": "meditate_fleet", "success": res["ok"],
                          "data": res, "metadata": {"ledger": LEDGER_PATH},
                          "errors": []}, indent=2))
    elif res["cleared"]:
        who = ", ".join(x for x in res["goals"] if x) or "unnamed rows"
        print("cleared %d finished or dead row(s): %s  (%d still running)"
              % (res["cleared"], who, res["remaining"]))
    else:
        print("nothing to clear — %d row(s), all still open" % res["remaining"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
