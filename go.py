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


def repair_items(store_dir: str = STORE_DIR):
    """Selectable repair items: only ACTIONABLE drift (has failing evidence or
    the drifted flag) — evidence-free session stubs are noise, not work."""
    from coordination import drift_report
    rep = drift_report(store_dir)
    return [m for m in rep["memories"]
            if m.get("failing") or "drifted" in (m.get("flags") or [])]


def _repair_kickoff(meditation_dir: str, store_dir: str = STORE_DIR,
                    select: Optional[str] = None) -> Optional[Dict[str, str]]:
    items = repair_items(store_dir)
    if select is not None:
        picked = [m for i, m in enumerate(items, 1)
                  if str(i) == select or m.get("id", "").startswith(select)]
        if not picked:
            return None
        items = picked
    if not items:
        qp = os.path.join(meditation_dir, "repair-queue.md")
        if not os.path.exists(qp):
            return None
    detail = "\n".join(
        "- %s: %s%s" % (m["id"], m["statement"][:140],
                        "".join("\n    FAILS " + f["claim"] for f in m.get("failing", [])))
        for m in items) or "(see the queue file)"
    prompt = (
        "Repair these graded memories — their evidence failed verification:\n"
        "%s\n"
        "For each: read the failing claim, check the world, then either fix the "
        "source .md memory file (memory right but stale) or supersede/correct it "
        "(world moved on). When done run `meditate grade` — a clean re-check "
        "clears the queue and counts as a REPAIR. Do not push; commit local if "
        "you touch a repo." % detail)
    name = "repair-" + (items[0]["id"][-6:] if select else "queue")
    return {"cwd": os.path.expanduser("~"), "prompt": prompt, "name": name}


def run(n: Optional[int] = None, repair_only: bool = False,
        only_goal: Optional[str] = None, repair_select: Optional[str] = None,
        meditation_dir: str = MEDITATION_DIR, store_dir: str = STORE_DIR,
        goals_dir: Optional[str] = None, history_path: Optional[str] = None,
        ledger_path: Optional[str] = None,
        launcher: Optional[Callable[[str, str, str], bool]] = None) -> Dict[str, Any]:
    import drive as dv
    import goals as gl

    lp = ledger_path or dv.LEDGER_PATH
    cands = dv.dispatchable(goals_dir, lp, history_path)
    if only_goal:
        cands = [c for c in cands if c["name"] == only_goal]
        repair = None
    else:
        repair = _repair_kickoff(meditation_dir, store_dir, select=repair_select)

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
                ok = bool(launcher(k["cwd"], k["prompt"], "goal-" + g["name"][:20],
                                   k.get("model", "")))
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
    ap.add_argument("sel", nargs="?", default=None,
                    help="cap (int), goal name, or repair item # / mem_id")
    ap.add_argument("--repair-only", action="store_true",
                    help="only the repair agent (this is `meditate fix`)")
    ap.add_argument("--list", action="store_true",
                    help="with --repair-only: numbered repair items")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.repair_only and args.list:
        items = repair_items()
        if not items:
            print("Repair queue is clean.")
            return 0
        print("Repairable items (meditate fix <n> to launch one):")
        for i, m in enumerate(items, 1):
            print("  %d. %s  %s" % (i, m["id"], m["statement"][:110]))
            for fl in m.get("failing", []):
                print("       FAILS %s" % fl["claim"])
        return 0

    n = None
    only_goal = None
    repair_select = None
    if args.sel is not None:
        try:
            n = int(args.sel)
        except ValueError:
            if args.repair_only:
                repair_select = args.sel
            else:
                only_goal = args.sel
    if args.repair_only and n is not None and n > 0 and str(n) == args.sel:
        repair_select = args.sel        # `meditate fix 2` = item 2, not a cap
        n = None
    data = run(n=n, repair_only=args.repair_only,
               only_goal=only_goal, repair_select=repair_select)
    env = {"tool_name": "meditate_go", "success": True, "data": data,
           "metadata": {"dry_run": n == 0}, "errors": []}
    if args.json:
        print(json.dumps(env, indent=2))
        return 0
    if n == 0:
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
