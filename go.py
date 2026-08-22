"""go — move everything forward. One verb, no menu.

    meditate go          # launch what the world needs: a repair agent if the
                         # queue is open, plus one agent per dispatchable goal
    meditate go 2        # same, capped at 2 launches total
    meditate go 0        # dry-run: show what WOULD launch

The fleet size is not a setting — the world sets it. Open repair queue = one
repair agent. N goals with open milestones and no agent already on them
(4h cooldown) = N goal agents. A number only restrains, never pads.

Priority is fixed and matches status: knowledge integrity before new work —
a fleet building on drifted facts builds wrong.
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

MEDITATION_DIR = os.path.expanduser("~/.claude/meditation")
STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")


def _repair_kickoff(meditation_dir: str) -> Optional[Dict[str, str]]:
    qp = os.path.join(meditation_dir, "repair-queue.md")
    if not os.path.exists(qp):
        return None
    prompt = (
        "The knowledge repair queue is open: %s\n"
        "Each item is a graded memory whose evidence failed verification. For "
        "each: read the failing claim, check the world, then either fix the "
        "source .md memory file (if the memory is right but stale) or "
        "supersede/correct it (if the world moved on). When done run "
        "`meditate grade` — a clean re-check clears the queue and counts as a "
        "REPAIR. Do not push anything; commit local if you touch a repo."
        % qp)
    return {"cwd": os.path.expanduser("~"), "prompt": prompt, "name": "repair-queue"}


def run(n: Optional[int] = None, repair_only: bool = False,
        meditation_dir: str = MEDITATION_DIR, store_dir: str = STORE_DIR,
        goals_dir: Optional[str] = None, history_path: Optional[str] = None,
        ledger_path: Optional[str] = None,
        launcher: Optional[Callable[[str, str, str], bool]] = None) -> Dict[str, Any]:
    import drive as dv
    import goals as gl

    lp = ledger_path or dv.LEDGER_PATH
    cands = dv.dispatchable(goals_dir, lp, history_path)
    repair = _repair_kickoff(meditation_dir)

    would: List[str] = []
    if repair:
        would.append("repair: " + os.path.join(meditation_dir, "repair-queue.md"))
    would += ["goal: %s -> %s" % (g["name"], g["next"]) for g in cands]

    result: Dict[str, Any] = {"would": would, "repair_launched": False,
                              "goals_launched": 0, "sent": [],
                              "cooling": getattr(dv.dispatchable, "cooling", 0)}
    if n == 0:
        return result

    if launcher is None:
        from launch import launch_claude as launcher  # type: ignore
    budget = n if n is not None else len(would)

    if repair and budget > 0:
        try:
            if launcher(repair["cwd"], repair["prompt"], "repair-queue"):
                result["repair_launched"] = True
                result["sent"].append("repair-queue")
                budget -= 1
        except Exception:
            pass

    if repair_only:
        budget = 0
    if budget > 0:
        kw = {}
        if goals_dir:
            kw["goals_dir"] = goals_dir
        if history_path:
            kw["history_path"] = history_path
        for g in cands[:budget]:
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
            result["goals_launched"] += 1
            result["sent"].append("goal-" + g["name"])
            try:
                os.makedirs(os.path.dirname(lp), exist_ok=True)
                with open(lp, "a") as f:
                    f.write(json.dumps({"goal": g["name"], "milestone": g["next"],
                                        "ts_epoch": time.time(),
                                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                            time.gmtime())}) + "\n")
            except OSError:
                pass
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Move everything forward")
    ap.add_argument("n", nargs="?", type=int, default=None,
                    help="optional cap on launches (0 = dry-run)")
    ap.add_argument("--repair-only", action="store_true",
                    help="only the repair agent (this is `meditate fix`)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    data = run(n=args.n, repair_only=args.repair_only)
    env = {"tool_name": "meditate_go", "success": True, "data": data,
           "metadata": {"dry_run": args.n == 0}, "errors": []}
    if args.json:
        print(json.dumps(env, indent=2))
        return 0
    if args.n == 0:
        print("Would launch (dry-run):")
        for w in data["would"]:
            print("  " + w)
        if not data["would"]:
            print("  nothing — world is clean and goals are covered")
        return 0
    if not data["sent"]:
        print("Nothing to move: no repair queue, no dispatchable goal"
              + (" (%d cooling)" % data["cooling"] if data["cooling"] else ""))
        return 0
    print("Launched %d agent(s):" % len(data["sent"]))
    for s in data["sent"]:
        print("  " + s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
