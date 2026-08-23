"""vigil — keep momentum while the owner is away, without burning the plan.

The pieces for overnight autonomy already existed, scattered:

    attention.py   is the person here      (exact, HIDIdleTime)
    milestones.py  is this already true    (readiness)
    go.py          dispatch a fleet
    fleet.py       act on what came back
    brief.py       catch me up

Nothing connected them. `go` dispatched on a timer whether the owner was
mid-sentence or asleep, and nothing recorded what happened while they were
gone. This is that wire, and only that wire — it owns no dispatch logic and
no presence logic of its own.

THE THREE CONSTRAINTS, ALL MEASURED, NONE ASSUMED
-------------------------------------------------
1. The Mac sleeps. `pmset -g log` shows repeated "Entering Sleep state", and
   the graded store's journal has a NINE HOUR hole (06:00 -> 15:00 local)
   with zero events. An overnight fleet on this laptop does not run; it waits
   for a keypress. Fix is one root command the owner must run themselves:

       sudo pmset repeat wakeorpoweron MTWRFSU 02:00:00

   Until that exists, vigil is honest about it and says so.

2. Claude Code's auth lives in the macOS Keychain — there is no portable
   credential file. So "run it on the SSD node instead" means an API key,
   i.e. per-token billing outside the plan: the exact credit burn this is
   supposed to avoid. The server is right for the deterministic parts
   (grading, dashboards) and those already take 5 seconds an hour. It is
   wrong for the agents.

3. Cost is real. A dispatched agent boots at ~35k tokens before it reads its
   first instruction (measured with `claude -p --output-format json`). Six
   agents overnight is ~210k of pure boot. So vigil never dispatches on a
   schedule alone — it dispatches only what readiness says is ACTUALLY ready,
   and it stops at a hard nightly cap.

WHAT IT REFUSES TO DO
---------------------
No work while the owner is present. Not because it would be wrong, but
because the whole point is momentum in the gap, and an agent editing files
under someone's hands is the collision the sangama layer spends its life
preventing.
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
NIGHT_LEDGER = os.path.join(MEDITATION_DIR, "vigil.jsonl")

# Away = no key or mouse for this long. 20 minutes is not a guess: below it
# you catch someone reading, and a dispatched agent editing files under a
# reader's hands is exactly the collision this tool exists to prevent.
AWAY_AFTER_S = 20 * 60

# Hard nightly ceiling. At ~35k boot tokens per agent this is ~280k of boot
# before any work — already a real bite of a plan window. A cap that is
# reached is reported, never silently truncated.
MAX_AGENTS_PER_NIGHT = 8


def _envelope(data: Dict[str, Any], errors: Optional[List] = None) -> Dict[str, Any]:
    return {"tool_name": "vigil", "success": not errors, "data": data,
            "metadata": {"ledger": NIGHT_LEDGER, "away_after_s": AWAY_AFTER_S},
            "errors": errors or []}


def wake_scheduled() -> bool:
    """Is there a repeating wake, so a fleet can actually run overnight?

    Without one the machine sleeps through the night and the fleet is a plan
    nobody executes. Unknown counts as False — an unverifiable promise of
    overnight work is worse than an honest "I cannot".
    """
    import subprocess
    try:
        out = subprocess.run(["pmset", "-g", "sched"], capture_output=True,
                             text=True, timeout=5).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False
    # A user-set repeating wake, not the OS's own alarm entries.
    return "repeating" in out and ("wakeorpoweron" in out or "wake at" in out)


def presence(now: Optional[float] = None) -> Dict[str, Any]:
    """Delegate to attention.py — vigil never re-implements presence."""
    try:
        import attention
        sig = attention.signals()
    except Exception as exc:                       # fail CLOSED: assume present
        return {"idle_s": 0.0, "away": False, "why": "presence unreadable: %s" % exc}
    idle = sig.get("idle_s")
    if idle is None:
        return {"idle_s": None, "away": False, "why": "idle time unreadable"}
    return {"idle_s": idle, "away": idle >= AWAY_AFTER_S,
            "frontmost": sig.get("frontmost"),
            "why": "idle %.0f min" % (idle / 60.0)}


def spent_tonight(ledger_path: str = NIGHT_LEDGER, window_s: int = 12 * 3600,
                  now: Optional[float] = None) -> int:
    """Agents dispatched by vigil inside the trailing window."""
    now = time.time() if now is None else now
    n = 0
    try:
        with open(ledger_path) as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("event") == "dispatched" and now - row.get("ts", 0) <= window_s:
                    n += int(row.get("count", 1))
    except OSError:
        return 0
    return n


def record(event: str, ledger_path: str = NIGHT_LEDGER, **fields) -> None:
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    row = {"event": event, "ts": time.time()}
    row.update(fields)
    try:
        with open(ledger_path, "a") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass                                        # fail-open: never block


def decide(ledger_path: str = NIGHT_LEDGER, now: Optional[float] = None,
           presence_fn=presence, cap: int = MAX_AGENTS_PER_NIGHT) -> Dict[str, Any]:
    """Should vigil dispatch right now, and how many agents may it use?

    Deterministic and side-effect free so it can be tested without a fleet,
    a Mac, or a night.
    """
    p = presence_fn()
    if not p["away"]:
        return {"run": False, "reason": "someone is here (%s)" % p["why"],
                "budget": 0, "presence": p}
    used = spent_tonight(ledger_path, now=now)
    budget = max(0, cap - used)
    if budget == 0:
        return {"run": False,
                "reason": "nightly cap reached — %d agents already dispatched" % used,
                "budget": 0, "presence": p}
    return {"run": True, "reason": "away %s, %d of %d agents left" % (p["why"], budget, cap),
            "budget": budget, "presence": p}


def since_last_present(ledger_path: str = NIGHT_LEDGER) -> List[Dict[str, Any]]:
    """What vigil did since the owner was last at the machine.

    This is the whole payoff: coming back to a sentence instead of a log.
    """
    rows = []
    try:
        with open(ledger_path) as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    out = []
    for row in reversed(rows):
        if row.get("event") == "returned":
            break
        out.append(row)
    return list(reversed(out))


def run(dry: bool = True, ledger_path: str = NIGHT_LEDGER,
        presence_fn=presence, launcher=None) -> Dict[str, Any]:
    d = decide(ledger_path, presence_fn=presence_fn)
    d["wake_scheduled"] = wake_scheduled()
    if not d["run"] or dry:
        return _envelope(d)
    try:
        import go
        result = go.run(n=d["budget"], launcher=launcher)
    except Exception as exc:
        return _envelope(d, [{"code": "dispatch", "message": str(exc)}])
    launched = int(result.get("goals_launched", 0)) + (
        1 if result.get("repair_launched") else 0)
    if launched:
        record("dispatched", ledger_path, count=launched,
               what=result.get("sent") or result.get("would"))
    d["launched"] = launched
    d["result"] = result
    return _envelope(d)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Keep momentum while you are away")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true", help="actually dispatch")
    ap.add_argument("--digest", action="store_true",
                    help="what happened while you were away")
    args = ap.parse_args(argv)

    if args.digest:
        rows = since_last_present()
        if args.json:
            print(json.dumps(_envelope({"since_last_present": rows}), indent=2))
            return 0
        if not rows:
            print("  Nothing ran while you were away.")
            return 0
        n = sum(int(r.get("count", 1)) for r in rows if r.get("event") == "dispatched")
        print("  While you were away: %d agent(s) dispatched." % n)
        for r in rows:
            if r.get("what"):
                for w in (r["what"] if isinstance(r["what"], list) else [r["what"]]):
                    print("    - %s" % w)
        record("returned")
        return 0

    env = run(dry=not args.apply)
    if args.json:
        print(json.dumps(env, indent=2))
        return 0
    d = env["data"]
    print("  %s" % ("WOULD DISPATCH" if d["run"] else "holding"))
    print("  %s" % d["reason"])
    if not d.get("wake_scheduled"):
        print("  ⚠️  No repeating wake is set, so this Mac sleeps through the night")
        print("     and nothing runs. Measured: a 9-hour hole in the journal.")
        print("     One command, and it needs your password (I cannot run it):")
        print("       sudo pmset repeat wakeorpoweron MTWRFSU 02:00:00")
    return 0


if __name__ == "__main__":
    sys.exit(main())
