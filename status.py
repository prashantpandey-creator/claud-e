"""status — where am I, and the ONE next action. Bare `meditate` runs this.

One screen: store health, goals, queues, fleet, heartbeat — ending with a
single decided `next:` line. Never a menu. Priority of the decision:

  1. repair queue open      -> fix knowledge first (a fleet on drifted
                               facts builds wrong)
  2. dispatchable goals     -> meditate go
  3. stilling overdue       -> /meditate
  4. otherwise              -> still; nothing owed

Plumbing (grade, drift, ask, archive, distill, goals, report, who, drive,
dashboard, sessions, launch, doctor) still exists under `meditate help`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

MEDITATION_DIR = os.path.expanduser("~/.claude/meditation")
STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")
STILL_MAX_DAYS = 3


def gather(meditation_dir: str = MEDITATION_DIR, store_dir: str = STORE_DIR,
           goals_dir: Optional[str] = None, history_path: Optional[str] = None,
           ledger_path: Optional[str] = None) -> Dict[str, Any]:
    import drive as dv
    import goals as gl
    from ask import _load

    d: Dict[str, Any] = {}
    mems = [m for m in _load(store_dir) if m.get("active")]
    d["store"] = {
        "active": len(mems),
        "verified": sum(1 for m in mems
                        if m["epistemic"]["evidence_status"] == "machine_checked"),
        "formed": sum(1 for m in mems if "commit-fact" in m.get("tags", [])),
    }
    kw = {}
    if goals_dir:
        kw["goals_dir"] = goals_dir
    if history_path:
        kw["history_path"] = history_path
    d["goals"] = gl.scan(**kw)
    d["dispatchable"] = dv.dispatchable(goals_dir, ledger_path or dv.LEDGER_PATH,
                                        history_path)
    d["cooling"] = getattr(dv.dispatchable, "cooling", 0)
    d["repair_open"] = os.path.exists(os.path.join(meditation_dir, "repair-queue.md"))
    hb = os.path.join(meditation_dir, "heartbeat.log")
    d["heartbeat_h"] = round((time.time() - os.path.getmtime(hb)) / 3600, 1) \
        if os.path.exists(hb) else None
    still = os.path.join(meditation_dir, "STILLNESS.md")
    d["still_days"] = round((time.time() - os.path.getmtime(still)) / 86400, 1) \
        if os.path.exists(still) else None

    # the ONE decision
    if d["repair_open"]:
        d["next"] = "meditate go  (repair queue open — fix knowledge before new work)"
    elif d["dispatchable"]:
        d["next"] = "meditate go  (%d goal agent(s) ready to launch)" % len(d["dispatchable"])
    elif d["still_days"] is None or d["still_days"] > STILL_MAX_DAYS:
        d["next"] = "/meditate  (stilling pass overdue)"
    else:
        d["next"] = "nothing owed — the world is graded and the fleet is out"
    return d


def status_text(**kw) -> str:
    d = gather(**kw)
    s = d["store"]
    lines: List[str] = []
    vr = 100.0 * s["verified"] / s["active"] if s["active"] else 0.0
    lines.append("meditate — %d graded memories, %.1f%% verified, %d self-formed"
                 % (s["active"], vr, s["formed"]))
    if d["heartbeat_h"] is not None:
        try:
            from cadence import current_interval_s
            cyc = (current_interval_s() or 0) / 3600
        except Exception:
            cyc = 0
        # never hardcode the cycle — it is derived and changes (was "6 h" while
        # the real interval was 1 h: the tool lying about its own rhythm)
        lines.append("heartbeat %.1f h ago%s" % (
            d["heartbeat_h"], (" (%.0f h cycle)" % cyc) if cyc else ""))
    for g in d["goals"]:
        widen = "  scope +%d" % g["scope_delta"] if g.get("scope_delta", 0) > 0 else ""
        lines.append("  %-26s %5.1f%%  %d/%d%s  -> %s"
                     % (g["name"][:26], g["pct"], g["done"], g["total"], widen,
                        (g["next"] or "—")[:60]))
    if d["repair_open"]:
        lines.append("repair queue OPEN — knowledge failed verification")
    if d["cooling"]:
        lines.append("%d goal(s) cooling — agents presumed on them" % d["cooling"])
    lines.append("")
    lines.append("next: " + d["next"])
    if "nothing owed" not in d["next"]:
        lines.append("face: ~/.claude/meditation/dashboard.html  (fresh every heartbeat)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Status + the one next action")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.json:
        d = gather()
        d["goals"] = [{"name": g["name"], "pct": g["pct"], "done": g["done"],
                       "total": g["total"], "next": g["next"]} for g in d["goals"]]
        d["dispatchable"] = [g["name"] for g in d["dispatchable"]]
        print(json.dumps({"tool_name": "meditate_status", "success": True,
                          "data": d, "metadata": {"store_dir": STORE_DIR},
                          "errors": []}, indent=2))
        return 0
    print(status_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
