"""meditate metrics — measuring how well the memory system is running.

Reads nidra's journal.jsonl (every action ever taken) and memories.jsonl
to compute health metrics over time. No new logging needed — the journal
IS the event stream.

Run:  meditate metrics
      meditate metrics --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

STORE_DIR = os.path.expanduser("~/.claude/meditation/nidra_store")
JOURNAL_PATH = os.path.join(STORE_DIR, "memories.jsonl")
EVENTS_PATH = os.path.join(STORE_DIR, "journal.jsonl")
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION = open(os.path.join(SKILL_DIR, "VERSION")).read().strip()


def _parse_ts(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        # events written by different components differ in offset-awareness;
        # normalize to aware-UTC so no comparison can ever crash the dashboard
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _day_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def read_journal() -> List[Dict]:
    rows = []
    if not os.path.exists(EVENTS_PATH):
        return rows
    with open(EVENTS_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def read_memories() -> List[Dict]:
    rows = []
    if not os.path.exists(JOURNAL_PATH):
        return rows
    with open(JOURNAL_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def _within(rows: List[Dict], days_ago_from: float, days_ago_to: float,
            now: Optional[datetime] = None) -> List[Dict]:
    """Rows whose ts falls in [now-days_ago_from, now-days_ago_to)."""
    now = now or datetime.now(timezone.utc)
    lo = now.timestamp() - days_ago_from * 86400
    hi = now.timestamp() - days_ago_to * 86400
    out = []
    for r in rows:
        dt = _parse_ts(r.get("ts", ""))
        if dt and lo <= dt.timestamp() < hi:
            out.append(r)
    return out


TRACKED_EVENTS = ("sleep.completed", "sleep.demoted", "sleep.regraded",
                  "sleep.merged", "import.meditate", "import.memory_files")


def _trend(journal: List[Dict], now: Optional[datetime] = None,
           window_days: float = 7.0) -> Dict[str, Any]:
    """Last window vs the one before it, per event type.

    This is the only part of this module that can go DOWN, which is the only
    part that can tell you something broke. `delta` is this-window minus
    last-window; `dead` names events that fired last window and have fired
    ZERO times in this one — the shape of a silently-broken lane.
    """
    cur = _within(journal, window_days, 0, now)
    prev = _within(journal, window_days * 2, window_days, now)

    def count(rows):
        out = {e: 0 for e in TRACKED_EVENTS}
        for r in rows:
            e = r.get("event")
            if e in out:
                out[e] += 1
        return out

    c, p = count(cur), count(prev)

    # Is there enough history for the comparison to MEAN anything?
    #
    # With 4.5 days of journal, the prior 7-day window predates the first
    # event, so every count in it is 0 and every delta reads as spectacular
    # growth. That is not a measurement, it is an artefact of the window
    # hanging off the end of the data — the same "reported a number where
    # there was no answer" defect this module was just fixed for. Say
    # "not enough history yet" instead of printing a triumphant +5130.
    stamps = [_parse_ts(r.get("ts", "")) for r in journal]
    first = min((s for s in stamps if s), default=None)
    ref = now or datetime.now(timezone.utc)
    needed = ref.timestamp() - window_days * 2 * 86400
    comparable = bool(first) and first.timestamp() <= needed

    return {
        "window_days": window_days,
        "current": c,
        "previous": p,
        "comparable": comparable,
        "history_days": round((ref.timestamp() - first.timestamp()) / 86400, 1) if first else 0.0,
        # None, not a number, when the baseline window predates the data. A
        # missing value is honest; a fake one gets quoted back at you later.
        "delta": {e: c[e] - p[e] for e in TRACKED_EVENTS} if comparable else None,
        # A lane that WAS running and now is not. Nothing else in this file
        # can surface that: every cumulative counter keeps its old total and
        # looks healthy forever. Only meaningful once there is a baseline.
        "dead": sorted(e for e in TRACKED_EVENTS if p[e] > 0 and c[e] == 0) if comparable else [],
    }


def compute_metrics(journal: Optional[List[Dict]] = None,
                    memories: Optional[List[Dict]] = None,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    """journal/memories are injectable so the NUMBERS can be tested.

    Before this they were read from fixed paths, so every test could do was
    assert a key existed and that a rate was >= 0 — which 0.00% satisfies
    forever. The suite would have passed identically if every metric returned
    zero, and for one metric it did: drift.downgrades read 0 for the tool's
    whole life while 41 real demotions sat in the same journal.
    """
    journal = read_journal() if journal is None else journal
    memories = read_memories() if memories is None else memories

    # --- Memory health snapshot ---
    total = len(memories)
    active = sum(1 for m in memories if m.get("active"))
    tombstoned = total - active
    by_status: Dict[str, int] = defaultdict(int)
    confidence_sum = 0.0
    confidence_count = 0
    for m in memories:
        if not m.get("active"):
            continue
        s = m.get("epistemic", {}).get("evidence_status", "unverified")
        by_status[s] += 1
        c = m.get("epistemic", {}).get("confidence")
        if c is not None:
            confidence_sum += c
            confidence_count += 1

    verified_rate = by_status.get("machine_checked", 0) / active if active else 0
    avg_confidence = confidence_sum / confidence_count if confidence_count else 0

    # --- Journal event timeline ---
    events_by_type: Dict[str, int] = defaultdict(int)
    events_by_day: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    imports_by_project: Dict[str, int] = defaultdict(int)
    sleep_runs = []
    grade_changes = []
    merges = 0
    first_ts = None
    last_ts = None

    for row in journal:
        event = row.get("event", "unknown")
        events_by_type[event] += 1

        ts = _parse_ts(row.get("ts", ""))
        if ts:
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts
            day = _day_key(ts)
            events_by_day[day][event] += 1

        if event == "import.meditate":
            proj = row.get("project_dir", "unknown")
            proj_name = os.path.basename(proj) if proj else "unknown"
            imports_by_project[proj_name] += row.get("imported", 0)

        elif event == "sleep.completed":
            sleep_runs.append({
                "ts": row.get("ts"),
                "actions": row.get("actions", 0),
                "contested": row.get("contested", 0),
            })

        elif event == "sleep.regraded":
            detail = row.get("detail", "")
            grade_changes.append({"id": row.get("id"), "detail": detail, "ts": row.get("ts")})

        elif event == "sleep.merged":
            merges += 1

    # --- Drift metrics ---
    #
    # downgrades USED to count only sleep.regraded rows whose detail began
    # "machine_checked ->". sleep.py emits `regraded` ONLY when "drifted" is
    # NOT in states — a drift demotion emits `sleep.demoted` instead. So the
    # counter could never see the one event it exists to count, and reported
    # drift_rate 0.00% for the tool's entire life while 41 real demotions sat
    # in the same journal. Proven 2026-08-25 by running a real demotion
    # through run_sleep: events were [regraded, completed, DEMOTED, completed]
    # and no regraded row carried "machine_checked ->".
    #
    # The correct count is BOTH paths: demotions caused by drift, plus
    # non-drift regrades that lost machine_checked.
    demoted = sum(1 for r in journal if r.get("event") == "sleep.demoted")
    regrade_down = sum(1 for g in grade_changes if "machine_checked ->" in g.get("detail", ""))
    upgrades = sum(1 for g in grade_changes if "-> machine_checked" in g.get("detail", ""))
    downgrades = demoted + regrade_down
    drift_rate = downgrades / active if active else 0

    # --- Consolidation efficiency ---
    total_sleep_actions = sum(s["actions"] for s in sleep_runs)
    sleep_run_count = len(sleep_runs)

    # --- Coverage ---
    try:
        sys.path.insert(0, SKILL_DIR)
        from sessions import scan_all_projects
        scan = scan_all_projects(cap=20)
        total_sessions = scan["data"]["total_sessions"] if scan["success"] else 0
    except Exception:
        total_sessions = 0

    session_mems = sum(1 for m in memories if m.get("active") and "meditate-session" in m.get("tags", []))
    memfile_mems = sum(1 for m in memories if m.get("active") and "memory-file" in m.get("tags", []))

    # Count EVERY memory store, not just the vedic-puran one. Hardcoding a
    # single store made coverage read a flattering 100% while 28 files in the
    # other stores were never graded at all — the metric hid the gap it exists
    # to expose.
    MEMORY_ROOT = paths.memory_root()
    total_md_files = 0
    if os.path.isdir(MEMORY_ROOT):
        for entry in os.listdir(MEMORY_ROOT):
            d = os.path.join(MEMORY_ROOT, entry)
            if not os.path.isdir(d):
                continue
            total_md_files += sum(
                1 for f in os.listdir(d) if f.endswith(".md") and f != "MEMORY.md"
            )

    session_coverage = session_mems / total_sessions if total_sessions else 0
    memfile_coverage = memfile_mems / total_md_files if total_md_files else 0

    # --- Uptime (days since first event) ---
    now = datetime.now(timezone.utc)
    uptime_days = (now - first_ts).total_seconds() / 86400 if first_ts else 0

    # --- Compose ---
    return {
        "version": VERSION,
        "snapshot": {
            "total_memories": total,
            "active": active,
            "tombstoned": tombstoned,
            "by_status": dict(by_status),
            "verified_rate": round(verified_rate, 3),
            "avg_confidence": round(avg_confidence, 3),
        },
        "drift": {
            "upgrades": upgrades,
            "downgrades": downgrades,
            "drift_rate": round(drift_rate, 4),
            "grade_changes_total": len(grade_changes),
        },
        # WINDOWED, and this is the point of the section.
        #
        # Every other number here is a lifetime cumulative total or a
        # this-instant snapshot. A cumulative counter is monotonic: it cannot
        # go down, so it CANNOT report a regression. If the grader silently
        # broke tomorrow, upgrades/sleep_runs/journal_entries would keep
        # climbing and verified_rate would stay flattering, because nothing
        # was being checked. That is not a hypothetical — it is precisely how
        # Tutor recorded "fires every session" through 132 sessions in which
        # it fired zero times, and how drift_rate read 0.00% through 41 real
        # demotions.
        #
        # A rate over a WINDOW can fall. That is the whole property that makes
        # a change measurable after the day it shipped: this week vs last
        # week, not since-the-beginning-of-time.
        "trend": _trend(journal, now),
        "consolidation": {
            "sleep_runs": sleep_run_count,
            "total_actions": total_sleep_actions,
            "merges": merges,
            "avg_actions_per_run": round(total_sleep_actions / sleep_run_count, 1) if sleep_run_count else 0,
        },
        "coverage": {
            "sessions_known": total_sessions,
            "session_memories": session_mems,
            "session_coverage": round(session_coverage, 3),
            "md_files_known": total_md_files,
            "md_file_memories": memfile_mems,
            "md_file_coverage": round(memfile_coverage, 3),
            "total_active": active,
        },
        "activity": {
            "journal_entries": len(journal),
            "events_by_type": dict(events_by_type),
            "imports_by_project": dict(imports_by_project),
            "uptime_days": round(uptime_days, 1),
            "first_event": first_ts.isoformat() if first_ts else None,
            "last_event": last_ts.isoformat() if last_ts else None,
        },
        "timeline": {k: dict(v) for k, v in sorted(events_by_day.items())},
    }


def _envelope(data):
    return {
        "tool_name": "meditate_metrics",
        "success": True,
        "data": data,
        "metadata": {"store_dir": STORE_DIR},
        "errors": [],
    }


def _bar(value: float, width: int = 20) -> str:
    filled = int(value * width)
    return "█" * filled + "░" * (width - filled)


def print_human(data: Dict):
    s = data["snapshot"]
    d = data["drift"]
    c = data["consolidation"]
    cov = data["coverage"]
    a = data["activity"]

    print(f"meditate metrics v{data['version']}")
    print("=" * 50)

    print(f"\n  Memory Health")
    print(f"    active:     {s['active']:>4}  (of {s['total_memories']} total, {s['tombstoned']} tombstoned)")
    for status, count in sorted(s["by_status"].items(), key=lambda x: -x[1]):
        print(f"      {status}: {count}")
    print(f"    verified:   {_bar(s['verified_rate'])} {s['verified_rate']*100:.1f}%")
    print(f"    confidence: {_bar(s['avg_confidence'])} {s['avg_confidence']*100:.1f}%")

    print(f"\n  Drift Detection")
    print(f"    upgrades:    {d['upgrades']:>4}  (unverified -> machine_checked)")
    print(f"    downgrades:  {d['downgrades']:>4}  (machine_checked -> unverified)")
    print(f"    drift rate:  {d['drift_rate']*100:.2f}%")
    print(f"    total grade changes: {d['grade_changes_total']}")

    # The only block here that can go DOWN, and therefore the only one that
    # can tell you something broke. Everything else is a lifetime total or a
    # snapshot; both look healthy forever after a lane dies.
    t = data.get("trend") or {}
    if t:
        w = t.get("window_days", 7)
        print(f"\n  Trend (last {w:.0f}d vs prior {w:.0f}d)")
        if not t.get("comparable"):
            print(f"    not enough history yet — {t.get('history_days', 0)}d of journal, "
                  f"need {w*2:.0f}d for a baseline. No delta reported rather than a fake one.")
        else:
            for ev, cur in t["current"].items():
                prev = t["previous"][ev]
                if not cur and not prev:
                    continue
                dl = t["delta"][ev]
                print(f"    {ev:<22} {prev:>6} -> {cur:<6} {dl:+d}")
            if t.get("dead"):
                print(f"    ⚠️  STOPPED (ran last window, zero this one): {', '.join(t['dead'])}")

    print(f"\n  Consolidation (sleep pass)")
    print(f"    runs:        {c['sleep_runs']:>4}")
    print(f"    actions:     {c['total_actions']:>4}  (avg {c['avg_actions_per_run']:.1f}/run)")
    print(f"    merges:      {c['merges']:>4}  (duplicates fused)")

    print(f"\n  Coverage")
    print(f"    Sessions:    {cov['session_memories']:>4} / {cov['sessions_known']}  {_bar(cov['session_coverage'])} {cov['session_coverage']*100:.1f}%")
    print(f"    .md files:   {cov['md_file_memories']:>4} / {cov['md_files_known']}  {_bar(cov['md_file_coverage'])} {cov['md_file_coverage']*100:.1f}%")
    print(f"    total active: {cov['total_active']}")

    print(f"\n  Activity")
    print(f"    uptime:      {a['uptime_days']:.1f} days")
    print(f"    journal:     {a['journal_entries']} entries")
    print(f"    imports by project:")
    for proj, count in sorted(a["imports_by_project"].items(), key=lambda x: -x[1]):
        print(f"      {proj}: {count}")

    timeline = data.get("timeline", {})
    if timeline:
        print(f"\n  Timeline (last 7 days)")
        days = sorted(timeline.keys())[-7:]
        for day in days:
            evts = timeline[day]
            total_day = sum(evts.values())
            imports = evts.get("import.meditate", 0)
            sleeps = evts.get("sleep.completed", 0)
            regrades = evts.get("sleep.regraded", 0)
            print(f"    {day}  {total_day:>4} events  ({imports} imports, {regrades} regrades, {sleeps} sleeps)")

    print(f"\n{'=' * 50}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="meditate metrics", description="meditate metrics — how well is the memory running")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    data = compute_metrics()

    if args.json:
        print(json.dumps(_envelope(data), indent=2))
        return 0

    print_human(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
