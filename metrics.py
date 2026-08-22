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


def compute_metrics() -> Dict[str, Any]:
    journal = read_journal()
    memories = read_memories()

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
    upgrades = sum(1 for g in grade_changes if "-> machine_checked" in g.get("detail", ""))
    downgrades = sum(1 for g in grade_changes if "machine_checked ->" in g.get("detail", ""))
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
    MEMORY_ROOT = os.path.expanduser("~/claude-sync/memory")
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
    ap = argparse.ArgumentParser(description="meditate metrics — how well is the memory running")
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
