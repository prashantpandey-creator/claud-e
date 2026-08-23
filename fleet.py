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


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="meditate fleet", description="Act on dispatched agents")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("clear")
    c.add_argument("goal", nargs="?")
    c.add_argument("--dead", action="store_true",
                   help="only rows with no milestone, which can never finish")
    c.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
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
