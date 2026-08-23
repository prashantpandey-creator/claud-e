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
