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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
NIDRA_ROOT = paths.nidra_root() or ""
STORE_DIR = os.path.expanduser("~/.claude/meditation/nidra_store")

sys.path.insert(0, SKILL_DIR)
if NIDRA_ROOT:
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
MEMORY_ROOT = paths.memory_root()
# (MEMORY_DIR was here: MEMORY_ROOT + a hardcoded "-Users-badenath-projects-
# vedic-puran". It had no readers — every grading path walks _all_memory_dirs()
# instead — so it was the author's home directory sitting in the package for
# nothing. Removed rather than made cwd-dependent.)


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


JOURNAL_MAX_BYTES = 25_000_000  # measured: 3,314 rows/day ≈ 0.7 MB/day at peak;
                                # 25 MB ≈ a month of heavy use per rotated file.
                                # Keeps every full-journal read <20 ms forever.


def _rotate_journal(store_dir, max_bytes=JOURNAL_MAX_BYTES):
    """Rotate journal.jsonl when it crosses the threshold.

    The journal is append-only with no other bound — the one measured
    unbounded-growth surface in the pipeline. Rotated files stay in the store
    dir as journal-<stamp>.jsonl; report.py reads them all (repair pairs span
    rotations), while the SessionStart drift scan reads only the current file
    (its window is 48h — a 25 MB rotation can't cut inside that at any
    plausible rate).
    """
    import time as _t
    jp = os.path.join(store_dir, "journal.jsonl")
    try:
        if os.path.exists(jp) and os.path.getsize(jp) > max_bytes:
            stamp = _t.strftime("%Y%m%d-%H%M%S")
            os.replace(jp, os.path.join(store_dir, "journal-%s.jsonl" % stamp))
            return True
    except OSError:
        pass
    return False


def run(do_sleep=False, store_dir=None, memory_root=None, form_days=None,
        sessions=None):
    """Scan sessions + .md memories into the graded store.

    store_dir/memory_root are injectable so tests never touch the live store —
    the graded store is shared state, not a scratchpad.
    """
    store_dir = store_dir or STORE_DIR

    # Three writers now exist (heartbeat, Pulse's grade button, goal agents
    # running `meditate grade`). Unlocked, two passes interleave load->save
    # and the later save silently DROPS the earlier one's memories. Per-store
    # flock: second runner skips with an honest envelope instead of corrupting.
    import fcntl
    os.makedirs(store_dir, exist_ok=True)
    lock_f = open(os.path.join(store_dir, ".grade.lock"), "w")
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_f.close()
        return _envelope(True, {"skipped": "another grade pass is already running on this store"},
                         None, store_dir)

    _rotate_journal(store_dir)
    try:
        from nidra.store import Store
        from nidra.adapters.meditate import import_sessions
    except ImportError as e:
        return _envelope(False, {}, [{"code": "import", "message": str(e),
             "fix": "run: bash %s/install.sh  (fetches the grading engine), or set MEDITATE_NIDRA_ROOT to a nidra checkout" % SKILL_DIR}], store_dir)

    try:
        from sessions import scan_all_projects
    except ImportError as e:
        return _envelope(False, {}, [{"code": "import", "message": str(e),
             "fix": "run: bash %s/install.sh  (fetches the grading engine), or set MEDITATE_NIDRA_ROOT to a nidra checkout" % SKILL_DIR}], store_dir)

    # sessions injectable: the suite called this 11x and each call rescanned
    # the owner's 126 real transcripts (2.6s each = 26s, the slowest suite and
    # the bound on the whole parallel doctor run). Tests pass a fixture; they
    # also stop depending on how many sessions the owner happens to have.
    if sessions is None:
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

    # LANE 1 formation: the day becomes memory. Commits made in sessions are
    # already-distilled knowledge (the owner wrote the message); each becomes
    # a memory with evidence born attached, verified next pass like any other.
    # form_days=0 skips formation (tests own that cost in test_formation.py);
    # None means the module default window.
    formed = 0
    if form_days != 0:
        try:
            from formation import form_commit_facts
            kw = {} if form_days is None else {"since_days": form_days}
            formed = form_commit_facts(store_dir, sessions, **kw)
        except Exception:
            pass

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
        "formed_commit_facts": formed,
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

    # Rebuild the path index: absolute path -> machine-checked statements.
    # coordination.py serves these at edit time, so a wrong belief about a file
    # gets corrected at the exact moment the agent is about to act on it.
    try:
        index = {}
        for m in store.load():
            if not m.get("active"):
                continue
            status = m.get("epistemic", {}).get("evidence_status", "unverified")
            for ev in m.get("evidence", []):
                loc = str(ev.get("locator", ""))
                if loc.startswith("path:"):
                    p = os.path.expanduser(loc[5:])
                    index.setdefault(p, []).append(
                        {"id": m.get("id"), "statement": m.get("statement", "")[:200],
                         "status": status})
        idx_path = os.path.join(store_dir, "path_index.json")
        with open(idx_path + ".tmp", "w") as fh:
            json.dump(index, fh)
        os.replace(idx_path + ".tmp", idx_path)
        result["path_index"] = {"paths": len(index),
                                "claims": sum(len(v) for v in index.values())}
    except Exception as e:
        result["path_index_error"] = str(e)

    # Close the correction loop: materialize caught drift as WORK. The queue
    # file appears when evidence fails and disappears when the world is clean
    # again — `meditate report` counts the round trip as a repair.
    try:
        from coordination import drift_report
        from ask import write_repair_queue
        # Queue lives beside ITS OWN store (parent dir) — a test run against a
        # temp store must never touch the live queue.
        med_dir = os.path.dirname(store_dir.rstrip("/"))
        qp = write_repair_queue(drift_report(store_dir), meditation_dir=med_dir)
        result["repair_queue"] = qp or "clean"
    except Exception as e:
        result["repair_queue_error"] = str(e)

    lock_f.close()
    return _envelope(True, result, None, store_dir)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="meditate grade", description="Bridge meditate sessions into nidra")
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
