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
                    }) + "\n")
            except OSError:
                pass
    return {"candidates": [{"goal": g["name"], "pct": g["pct"], "next": g["next"]}
                           for g in cands],
            "cooling": cooling, "launched": launched, "sent": sent}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Dispatch goal agents")
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
    sys.exit(main())
