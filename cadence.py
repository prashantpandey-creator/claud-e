"""cadence — how often should the self-check run? Derived, not guessed.

The 6-hour interval was a guess. This computes it from the only thing that
actually matters: how fast the watched world changes underneath the facts.

The parameter is churn — source files edited per hour. The rule:

    interval = TARGET_CHANGES / churn_per_hour

i.e. wake when roughly TARGET_CHANGES edits have piled up, because a pass
that finds nothing was wasted and a pass that finds fifty let the store lie
for too long. Bounded [MIN_H, MAX_H] so a burst of activity can't spin the
machine and a quiet week can't let knowledge rot unchecked.

Staleness is the honest cost: with interval H, a fact whose file you delete
now can be served as true for up to H more hours. `meditate cadence` prints
the number, why, and what it would change.

    meditate cadence            # recommendation + reasoning
    meditate cadence --apply    # rewrite the schedule (launchd, or cron where
                                 # launchd is absent) and reload
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import paths
import time
from typing import Any, Dict, List, Optional

MEMORY_ROOT = paths.memory_root()
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
PLIST = os.path.expanduser("~/Library/LaunchAgents/com.meditate.grade.plist")
CRONTAB_TAG = "meditate-heartbeat"

TARGET_CHANGES = 5      # edits worth waking for — below this a pass finds nothing
MIN_H, MAX_H = 1, 24    # never thrash; never let a week of rot go unchecked
WINDOW_H = 72           # measure churn over 3 days: long enough to survive one
                        # quiet evening, short enough to track a real shift


def churn(window_h: int = WINDOW_H) -> Dict[str, Any]:
    """Watched-file edits per hour, measured — memories and transcripts."""
    now = time.time()
    cutoff = now - window_h * 3600
    mem_edits = trans_edits = 0
    for d in glob.glob(os.path.join(MEMORY_ROOT, "*")):
        if not os.path.isdir(d):
            continue
        for f in glob.glob(os.path.join(d, "*.md")):
            try:
                if os.path.getmtime(f) > cutoff:
                    mem_edits += 1
            except OSError:
                continue
    for f in glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl")):
        try:
            if os.path.getmtime(f) > cutoff:
                trans_edits += 1
        except OSError:
            continue
    total = mem_edits + trans_edits
    return {"window_h": window_h, "memory_edits": mem_edits,
            "transcript_edits": trans_edits, "total_edits": total,
            "per_hour": round(total / window_h, 2)}


def recommend(c: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = c or churn()
    rate = c["per_hour"]
    if rate <= 0:
        hours, why = MAX_H, "nothing changed in the window — check rarely"
    else:
        raw = TARGET_CHANGES / rate
        hours = max(MIN_H, min(MAX_H, round(raw)))
        why = ("%.2f edits/hour → ~%d edits pile up every %d h"
               % (rate, TARGET_CHANGES, hours))
        if raw < MIN_H:
            why += " (floored at %dh — never thrash)" % MIN_H
        elif raw > MAX_H:
            why += " (capped at %dh — never let knowledge rot)" % MAX_H
    return {"hours": int(hours), "seconds": int(hours) * 3600,
            "why": why, "churn": c,
            "worst_case_staleness_h": int(hours)}


def _cron_line() -> Optional[str]:
    """The heartbeat's crontab line, if install.sh used the cron fallback."""
    if not shutil.which("crontab"):
        return None
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True,
                           text=True, timeout=10)
    except Exception:
        return None
    for line in r.stdout.splitlines():
        if CRONTAB_TAG in line:
            return line
    return None


def _cron_interval_h(line: str) -> Optional[int]:
    """Hour field of `0 */N * * *` (or `0 * * * *` for every hour)."""
    fields = line.split()
    if len(fields) < 2:
        return None
    hour_field = fields[1]
    if hour_field == "*":
        return 1
    m = re.match(r"\*/(\d+)$", hour_field)
    return int(m.group(1)) if m else None


def current_interval_s() -> Optional[int]:
    try:
        import plistlib
        with open(PLIST, "rb") as f:
            v = int(plistlib.load(f).get("StartInterval") or 0)
            if v:
                return v
    except Exception:
        pass
    line = _cron_line()
    if line:
        h = _cron_interval_h(line)
        if h:
            return h * 3600
    return None


def apply(seconds: int) -> Dict[str, Any]:
    """Rewrite the interval in place and reload — nothing else touched.
    launchd's StartInterval if a plist is installed, else the cron schedule."""
    if os.path.exists(PLIST):
        try:
            import plistlib
            with open(PLIST, "rb") as f:
                d = plistlib.load(f)
            d["StartInterval"] = int(seconds)
            with open(PLIST, "wb") as f:
                plistlib.dump(d, f)
            subprocess.run(["launchctl", "unload", PLIST],
                           capture_output=True, timeout=15)
            r = subprocess.run(["launchctl", "load", "-w", PLIST],
                               capture_output=True, timeout=15)
            return {"applied": r.returncode == 0, "seconds": int(seconds),
                     "mechanism": "launchd"}
        except Exception as e:
            return {"applied": False, "error": str(e), "mechanism": "launchd"}

    line = _cron_line()
    if line and shutil.which("crontab"):
        try:
            hours = max(1, round(seconds / 3600))
            hour_field = "*" if hours == 1 else "*/%d" % hours
            rest = line.split(None, 5)[5]
            new_line = "0 %s * * * %s" % (hour_field, rest)
            cur = subprocess.run(["crontab", "-l"], capture_output=True,
                                 text=True, timeout=10).stdout
            kept = [l for l in cur.splitlines() if CRONTAB_TAG not in l]
            kept.append(new_line)
            r = subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n",
                               capture_output=True, text=True, timeout=10)
            return {"applied": r.returncode == 0, "seconds": hours * 3600,
                     "mechanism": "cron"}
        except Exception as e:
            return {"applied": False, "error": str(e), "mechanism": "cron"}

    return {"applied": False, "error": "no launchd plist or cron heartbeat found"}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate cadence", description="Derive the self-check interval")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rec = recommend()
    cur = current_interval_s()
    data = {**rec, "current_seconds": cur,
            "current_hours": round(cur / 3600, 1) if cur else None,
            "change_needed": bool(cur and abs(cur - rec["seconds"]) >= 3600)}
    if args.apply and data["change_needed"]:
        data["apply"] = apply(rec["seconds"])
    if args.json:
        print(json.dumps({"tool_name": "meditate_cadence", "success": True,
                          "data": data, "metadata": {"plist": PLIST},
                          "errors": []}, indent=2))
        return 0
    c = rec["churn"]
    print("Self-check cadence — derived from how fast your world changes")
    print("  measured : %d edits in %dh (%d memories, %d transcripts) = %.2f/hour"
          % (c["total_edits"], c["window_h"], c["memory_edits"],
             c["transcript_edits"], c["per_hour"]))
    print("  rule     : wake when ~%d edits have piled up" % TARGET_CHANGES)
    print("  →  every %d h   (%s)" % (rec["hours"], rec["why"]))
    print("  worst case: a fact can be served %d h after its file changed"
          % rec["worst_case_staleness_h"])
    if cur:
        print("  currently : every %.1f h" % (cur / 3600))
    if data.get("apply", {}).get("applied"):
        print("  APPLIED — launchd reloaded at %d h" % rec["hours"])
    elif data["change_needed"]:
        print("  run `meditate cadence --apply` to move it")
    else:
        print("  already correct — nothing to change")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
