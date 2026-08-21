"""nidra_bridge — connect meditate's session mining to nidra's graded store.

One command: scan all sessions, import into nidra, run the sleep pass.
The bridge is the pipe between meditate (mining) and nidra (grading).

Run:  python3 ~/.claude/skills/meditate/nidra_bridge.py
      python3 ~/.claude/skills/meditate/nidra_bridge.py --json
      python3 ~/.claude/skills/meditate/nidra_bridge.py --sleep   # also consolidate
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
NIDRA_ROOT = os.path.expanduser("~/projects/nidra")
STORE_DIR = os.path.expanduser("~/.claude/meditation/nidra_store")

sys.path.insert(0, SKILL_DIR)
sys.path.insert(0, NIDRA_ROOT)


def _envelope(success, data, errors=None):
    return {
        "tool_name": "nidra_bridge",
        "success": success,
        "data": data,
        "metadata": {"store_dir": STORE_DIR, "nidra_root": NIDRA_ROOT},
        "errors": errors or [],
    }


MEMORY_DIR = os.path.expanduser("~/claude-sync/memory/-Users-badenath-projects-vedic-puran")


def run(do_sleep=False):
    try:
        from nidra.store import Store
        from nidra.adapters.meditate import import_sessions
    except ImportError as e:
        return _envelope(False, {}, [{"code": "import", "message": str(e)}])

    try:
        from sessions import scan_all_projects
    except ImportError as e:
        return _envelope(False, {}, [{"code": "import", "message": str(e)}])

    scan = scan_all_projects(cap=20)
    if not scan["success"]:
        return _envelope(False, {}, scan["errors"])

    sessions = scan["data"]["sessions"]
    store = Store(STORE_DIR)
    if not store.exists():
        store.init()

    by_dir: Dict[str, List[Dict]] = {}
    for s in sessions:
        pd = s.get("_project_dir", "")
        by_dir.setdefault(pd, []).append(s)

    totals = {"scanned": 0, "imported": 0, "already_exists": 0, "no_anchor": 0}
    for pd, group in by_dir.items():
        r = import_sessions(store, group, project_dir=pd or None)
        for k in totals:
            totals[k] += r[k]

    # Import .md memory files (the real knowledge)
    mem_files = {"scanned": 0, "imported": 0, "already_exists": 0}
    try:
        from nidra.adapters.memory_files import import_memory_files
        if os.path.isdir(MEMORY_DIR):
            mf = import_memory_files(store, MEMORY_DIR)
            mem_files["scanned"] = mf["scanned"]
            mem_files["imported"] = mf["imported"]
            mem_files["already_exists"] = mf["already_exists"]
    except ImportError:
        pass

    result = {
        **totals,
        "memory_files": mem_files,
        "store_total": len(store.load()),
    }

    if do_sleep:
        try:
            from nidra.sleep import run_sleep, census
            report = run_sleep(store)
            result["sleep"] = {
                "actions": len(report["actions"]),
                "contested": len(report["contested"]),
                "after": report["after"],
            }
        except Exception as e:
            result["sleep_error"] = str(e)

    return _envelope(True, result)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bridge meditate sessions into nidra")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sleep", action="store_true", help="Also run nidra sleep pass")
    args = ap.parse_args(argv)

    env = run(do_sleep=args.sleep)

    if args.json:
        print(json.dumps(env, indent=2))
        return 0 if env["success"] else 1

    if not env["success"]:
        for e in env["errors"]:
            print(f"  ERROR: {e['message']}")
        return 1

    d = env["data"]
    print(f"  Sessions:")
    print(f"    scanned:  {d['scanned']}")
    print(f"    imported: {d['imported']}")
    print(f"    existed:  {d['already_exists']}")
    print(f"    no anchor: {d['no_anchor']}")
    mf = d.get("memory_files", {})
    if mf.get("scanned", 0) > 0:
        print(f"  Memory files (.md):")
        print(f"    scanned:  {mf['scanned']}")
        print(f"    imported: {mf['imported']}")
        print(f"    existed:  {mf['already_exists']}")
    print(f"  Store:    {d['store_total']} total memories")
    if "sleep" in d:
        s = d["sleep"]
        print(f"  sleep:    {s['actions']} actions, {s['contested']} contested")
        print(f"  after:    {s['after']}")
    if "sleep_error" in d:
        print(f"  sleep err: {d['sleep_error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
