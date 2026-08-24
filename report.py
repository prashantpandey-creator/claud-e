"""report — the wins/efficacy loop: what drift-correct caught, what got
repaired, and what the stilling practice has actually bought.

The drift-correct loop this measures:
  1. CATCH    sleep pass re-checks every receipt; a changed world demotes the
              memory (journaled) or flags it `drifted`
  2. SURFACE  SessionStart names fresh downgrades; `meditate drift` prints the
              exact failing claim + line
  3. REPAIR   an agent (or /meditate) fixes the .md — judgment work, by design
  4. VERIFY   next grade pass re-checks; the upgrade is journaled
  5. MEASURE  this report: caught, repaired, time-to-repair, open — plus
              stilling (archives, continuation chats) and sangama (facts
              served, collisions warned) from durable logs

Honest zeros: an empty log reports 0 — no invented numbers.

CLI: python3 report.py [--json]     (meditate report)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")
ARCHIVE_ROOT = os.path.expanduser("~/.claude/meditation/archive")
COORD_ROOT = os.path.expanduser("~/.claude/coordination")
MEDITATION_DIR = os.path.expanduser("~/.claude/meditation")


def _jsonl(path: str):
    if not os.path.exists(path):
        return
    with open(path, errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def _ts(s: str) -> Optional[float]:
    try:
        return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def _journals(store_dir: str) -> List[str]:
    """All journal files, oldest first — rotated (journal-<stamp>) then current.
    Alphabetical gives chronology: 'journal-' sorts before 'journal.'."""
    try:
        names = sorted(f for f in os.listdir(store_dir)
                       if f.startswith("journal") and f.endswith(".jsonl"))
    except OSError:
        return []
    return [os.path.join(store_dir, f) for f in names]


def _drift(store_dir: str) -> Dict[str, Any]:
    """Caught / repaired / time-to-repair from ALL journals; open from the store."""
    down_at: Dict[str, float] = {}
    caught = repaired = 0
    repair_hours: List[float] = []
    for e in (row for jp in _journals(store_dir) for row in _jsonl(jp)):
        event = e.get("event")
        d = e.get("detail", "")
        t = _ts(e.get("ts", ""))
        mid = e.get("id", "?")
        # sleep.py logs a real drift catch as `sleep.demoted` — a plain
        # `sleep.regraded ... -> unverified` only fires for the corruption
        # path (sha256 mismatch). Both are catches; both must seed down_at
        # or a drift repair can never pair with its catch and `repaired`
        # stays 0 forever no matter how many drifts get fixed.
        if event == "sleep.demoted":
            caught += 1
            if t:
                down_at[mid] = t
        elif event == "sleep.regraded":
            if "-> unverified" in d:
                caught += 1
                if t:
                    down_at[mid] = t
            elif "-> machine_checked" in d and mid in down_at:
                repaired += 1
                if t:
                    repair_hours.append((t - down_at.pop(mid)) / 3600)
                else:
                    down_at.pop(mid, None)

    open_real = open_ungradeable = 0
    for m in _jsonl(os.path.join(store_dir, "memories.jsonl")):
        if not m.get("active"):
            continue
        if "drifted" in (m.get("flags") or []) and m.get("id") not in down_at:
            caught += 1                      # flagged, no journal catch event
        if m.get("epistemic", {}).get("evidence_status") != "unverified":
            continue
        if m.get("evidence"):
            open_real += 1                   # has receipts, world disagrees
        else:
            open_ungradeable += 1            # nothing checkable, never served

    med = sorted(repair_hours)[len(repair_hours) // 2] if repair_hours else None
    return {"caught": caught, "repaired": repaired,
            "median_repair_hours": round(med, 1) if med is not None else None,
            "open_real": open_real, "open_ungradeable": open_ungradeable}


def _stilling(archive_root: str, meditation_dir: str) -> Dict[str, Any]:
    n = b = 0
    for r in _jsonl(os.path.join(archive_root, "ARCHIVE-INDEX.jsonl")):
        n += 1
        b += int(r.get("bytes") or 0)
    chats = dirs = 0
    sess = os.path.join(meditation_dir, "sessions")
    if os.path.isdir(sess):
        for entry in os.listdir(sess):
            p = os.path.join(sess, entry)
            if not os.path.isdir(p):
                continue
            dirs += 1
            chats += sum(1 for f in os.listdir(p)
                         if f.endswith(".md") and f != "INDEX.md")
    still = os.path.join(meditation_dir, "STILLNESS.md")
    age = round((time.time() - os.path.getmtime(still)) / 86400, 1) \
        if os.path.exists(still) else None
    return {"sessions_archived": n, "bytes_archived": b,
            "split_session_dirs": dirs, "continuation_chats": chats,
            "stillness_age_days": age}


def _sangama(coord_root: str) -> Dict[str, Any]:
    counts = {"fact_served": 0, "collision_warned": 0}
    earliest = None
    for e in _jsonl(os.path.join(coord_root, "events.jsonl")):
        t = e.get("type")
        if t not in counts:
            continue
        counts[t] += 1
        ts = _ts(e.get("ts", ""))
        if ts and (earliest is None or ts < earliest):
            earliest = ts
    span_days = round((time.time() - earliest) / 86400, 1) if earliest else None
    return {"facts_served": counts["fact_served"],
            "collisions_warned": counts["collision_warned"],
            "span_days": span_days}


def compute(store_dir: str = STORE_DIR, archive_root: str = ARCHIVE_ROOT,
            coord_root: str = COORD_ROOT,
            meditation_dir: str = MEDITATION_DIR) -> Dict[str, Any]:
    return {"drift": _drift(store_dir),
            "stilling": _stilling(archive_root, meditation_dir),
            "sangama": _sangama(coord_root)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate report", description="Wins + efficacy report")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    data = compute()
    env = {"tool_name": "meditate_report", "success": True, "data": data,
           "metadata": {"store_dir": STORE_DIR, "archive_root": ARCHIVE_ROOT,
                        "coord_root": COORD_ROOT},
           "errors": []}
    if args.json:
        print(json.dumps(env, indent=2))
        return 0
    d, s, g = data["drift"], data["stilling"], data["sangama"]
    print("meditate report — wins and efficacy")
    print("=" * 40)
    print("\n  Drift-correct loop")
    print("    caught:    %4d  (world changed under a receipt)" % d["caught"])
    print("    repaired:  %4d  (re-verified after a fix)" % d["repaired"])
    if d["median_repair_hours"] is not None:
        print("    median time-to-repair: %.1f h" % d["median_repair_hours"])
    print("    open:      %4d real drift + %d ungradeable stubs"
          % (d["open_real"], d["open_ungradeable"]))
    print("\n  Stilling")
    print("    sessions archived:   %4d  (%.1f KB reclaimed from the pickers)"
          % (s["sessions_archived"], s["bytes_archived"] / 1000))
    print("    sessions split:      %4d  -> %d continuation chats"
          % (s["split_session_dirs"], s["continuation_chats"]))
    if s["stillness_age_days"] is not None:
        print("    last stilling pass:  %.1f days ago" % s["stillness_age_days"])
    print("\n  Sangama (since 0.4.3 — logged from now on)")
    if g["span_days"] is not None:
        print("    counters span:                    %.1f days" % g["span_days"])
    print("    graded facts served at edit time: %d" % g["facts_served"])
    print("    collision warnings issued:        %d" % g["collisions_warned"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
