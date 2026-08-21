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


def _envelope(success, data, errors=None, store_dir=None):
    return {
        "tool_name": "nidra_bridge",
        "success": success,
        "data": data,
        "metadata": {"store_dir": store_dir or STORE_DIR, "nidra_root": NIDRA_ROOT},
        "errors": errors or [],
    }


# All curated .md memory stores, not just the vedic-puran one. Every cwd-slug
# directory under claude-sync/memory is real knowledge; grading only the first
# one made coverage read 100% while other stores went ungraded.
MEMORY_ROOT = os.path.expanduser("~/claude-sync/memory")
MEMORY_DIR = os.path.join(MEMORY_ROOT, "-Users-badenath-projects-vedic-puran")


def _memory_dirs(root=None):
    """Every directory under claude-sync/memory that holds .md memories."""
    root = root or MEMORY_ROOT
    if not os.path.isdir(root):
        return []
    found = []
    for entry in sorted(os.listdir(root)):
        d = os.path.join(root, entry)
        if not os.path.isdir(d):
            continue
        if any(f.endswith(".md") for f in os.listdir(d)):
            found.append(d)
    return found


def run(do_sleep=False, store_dir=None, memory_root=None):
    """Scan sessions + .md memories into the graded store.

    store_dir/memory_root are injectable so tests never touch the live store —
    the graded store is shared state, not a scratchpad.
    """
    store_dir = store_dir or STORE_DIR
    try:
        from nidra.store import Store
        from nidra.adapters.meditate import import_sessions
    except ImportError as e:
        return _envelope(False, {}, [{"code": "import", "message": str(e)}], store_dir)

    try:
        from sessions import scan_all_projects
    except ImportError as e:
        return _envelope(False, {}, [{"code": "import", "message": str(e)}], store_dir)

    scan = scan_all_projects(cap=20)
    if not scan["success"]:
        return _envelope(False, {}, scan["errors"], store_dir)

    sessions = scan["data"]["sessions"]
    store = Store(store_dir)
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

    # Import .md memory files (the real knowledge) from EVERY memory store
    mem_files = {"scanned": 0, "imported": 0, "already_exists": 0, "dirs": []}
    try:
        from nidra.adapters.memory_files import import_memory_files
        for d in _memory_dirs(memory_root):
            mf = import_memory_files(store, d)
            mem_files["scanned"] += mf["scanned"]
            mem_files["imported"] += mf["imported"]
            mem_files["already_exists"] += mf["already_exists"]
            mem_files["dirs"].append({"dir": d, "scanned": mf["scanned"]})
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

    return _envelope(True, result, None, store_dir)


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
