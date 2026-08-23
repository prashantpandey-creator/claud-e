"""beacon — dispatched agents report progress back to where they launched.

The gap: Pulse's fleet view only knew an agent existed if it EDITED a file.
An agent reading Apple's rejection, thinking, or running commands was
invisible ("no live session seen"). Now every dispatched agent is told to
call `meditate progress <goal> "<what I'm doing>"` at start, at each step,
and at finish — one durable JSONL line per report — and Pulse shows the
latest line beside its goal in the FLEET section.

Deterministic, append-only, stdlib. The agent narrates; Pulse displays.

    meditate progress <goal> "reading the rejection reasons"     # agent calls this
    meditate progress <goal> --done "milestone ticked"           # final line
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

BEACON_PATH = os.environ.get("MEDITATE_BEACON") or os.path.expanduser(
    "~/.claude/coordination/fleet-beacons.jsonl")


def report(goal: str, message: str, done: bool = False,
           beacon_path: str = BEACON_PATH) -> None:
    """Append one progress line. Fail-open: a broken beacon must never stop
    the agent's real work."""
    try:
        os.makedirs(os.path.dirname(beacon_path), exist_ok=True)
        with open(beacon_path, "a") as f:
            f.write(json.dumps({
                "goal": goal,
                "message": message[:200],
                "done": bool(done),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            }) + "\n")
    except OSError:
        pass
    if done:
        notify_done(goal, message)


def notify_done(goal: str, message: str) -> bool:
    """Tell the owner, once, that a job they started has finished.

    Until now the ONLY way to learn an agent had finished was to be watching
    Pulse, or to have the mascot running with its voice on and be sitting near
    the speakers. 12 completions had been reported this way and none of them
    reached anyone who was not already looking.

    A native notification costs nothing, survives being missed, and works with
    the companion closed. Fail-open like the rest of this file: a notification
    that cannot be posted must never break an agent's real work.
    """
    if os.environ.get("MEDITATE_NO_NOTIFY"):
        return False
    if _casper_running():
        # Casper posts a notice you can CLICK — its action opens the dashboard.
        # An osascript notice cannot carry an action: clicking it activates
        # whichever process posted it, which is how "show" landed you in a raw
        # transcript instead of the place you decide what is next. So when the
        # companion is up, it owns this and we stay quiet rather than posting
        # a second, worse copy of the same news.
        return False
    title = _pretty(goal) + " is done"
    body = (message or "finished its milestone")[:160]
    try:
        import subprocess
        r = subprocess.run(
            ["osascript", "-e",
             'display notification %s with title %s sound name "Glass"'
             % (_as(body), _as(title))],
            capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            # "posted" must mean posted. Reporting success on a non-zero exit
            # is how a silent failure becomes a feature nobody knows is dead.
            notify_done.last_error = (r.stderr or "").strip()[:160]
            return False
        return True
    except Exception:
        return False


def _casper_running() -> bool:
    try:
        import subprocess
        r = subprocess.run(["pgrep", "-f", "Casper.app/Contents/MacOS/casper"],
                           capture_output=True, timeout=5)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def _as(s: str) -> str:
    """An AppleScript string literal, quotes and backslashes survived."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _pretty(goal: str) -> str:
    """'purangpt-mobile-live' is a key; 'Purangpt mobile live' is a thing you
    said. The notification is read by a person, not a parser."""
    g = goal
    for p in ("goal-", "goal_"):
        if g.startswith(p):
            g = g[len(p):]
    g = g.replace("-", " ").replace("_", " ").strip()
    return (g[:1].upper() + g[1:]) if g else "A job"


def latest(beacon_path: str = BEACON_PATH) -> Dict[str, Dict[str, Any]]:
    """Most recent beacon line per goal — what Pulse shows in FLEET."""
    out: Dict[str, Dict[str, Any]] = {}
    if not os.path.exists(beacon_path):
        return out
    try:
        with open(beacon_path, errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("goal"):
                    out[r["goal"]] = r
    except OSError:
        pass
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate progress", description="Report fleet-agent progress")
    ap.add_argument("goal", help="goal name this agent is working")
    ap.add_argument("message", nargs="*", help="what the agent is doing now")
    ap.add_argument("--done", action="store_true", help="final report for this goal")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    msg = " ".join(args.message) or ("finished" if args.done else "working")
    report(args.goal, msg, done=args.done)
    if args.json:
        print(json.dumps({"tool_name": "meditate_progress", "success": True,
                          "data": {"goal": args.goal, "message": msg,
                                   "done": args.done},
                          "metadata": {"beacon": BEACON_PATH}, "errors": []}))
    else:
        print("reported: %s — %s%s" % (args.goal, msg, " [done]" if args.done else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
