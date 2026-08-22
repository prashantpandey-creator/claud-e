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
import re
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
    """One screen a stranger can read. No internal vocabulary, and the thing
    that needs you is the thing that stands out."""
    d = gather(**kw)
    s = d["store"]
    # ANSI: bold for what needs you, dim for context. Plain text if piped.
    tty = sys.stdout.isatty()
    B = "\033[1;33m" if tty else ""      # attention
    G = "\033[0;32m" if tty else ""      # all good
    D = "\033[2m" if tty else ""         # quiet detail
    R = "\033[0m" if tty else ""

    lines: List[str] = []
    pct = 100.0 * s["verified"] / s["active"] if s["active"] else 0.0
    lines.append("I know %d things about your work. %.0f%% still check out. "
                 "%d I picked up on my own."
                 % (s["active"], pct, s["formed"]))
    if d["heartbeat_h"] is not None:
        lines.append("%sLast self-check %.0f h ago.%s" % (D, d["heartbeat_h"], R))
    lines.append("")

    if d["goals"]:
        lines.append("%sWhat you're working on%s" % (D, R))
        for g in d["goals"]:
            left = g["total"] - g["done"]
            grown = "  (grew by %d)" % g["scope_delta"] if g.get("scope_delta", 0) > 0 else ""
            lines.append("  %-26s %d of %d done%s" %
                         (g["title"][:26], g["done"], g["total"], grown))
            if g["next"]:
                lines.append("      %snext:%s %s" % (D, R, g["next"][:66]))
        lines.append("")

    if d["repair_open"]:
        lines.append("%s! Some of what I know stopped matching reality.%s" % (B, R))
    if d["cooling"]:
        lines.append("%s%d goal(s) already have someone working on them.%s"
                     % (D, d["cooling"], R))

    nxt = d["next"]
    if "nothing owed" in nxt:
        lines.append("%sNothing needs you. Everything checks out and the work "
                     "is moving.%s" % (G, R))
    else:
        # strip the internal parenthetical, keep the human reason
        cmd = nxt.split("(")[0].strip()
        why = nxt.split("(")[1].rstrip(")").strip() if "(" in nxt else ""
        why = (why.replace("repair queue open — fix knowledge before new work",
                           "some of what I know needs checking first")
                  .replace("stilling pass overdue",
                           "it's been a while since we cleared the decks"))
        why = re.sub(r"(\d+) goal agent\(s\) ready to launch",
                     r"\1 piece(s) of work ready to start", why)
        lines.append("%s→ %s%s%s" % (B, cmd, R, ("   " + D + why + R) if why else ""))
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
