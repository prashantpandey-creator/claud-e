"""repair — fix dead path claims in memories, without a model.

A graded memory fails when it names a file that is not there. Most of those
failures are not interesting: a hook got retired, a script moved, a directory
was renamed. The successor is sitting on disk, findable by three rules and no
judgement at all — measured on the real case that started this (a memory
pointing at ~/.claude/hooks/meditate-checkpoint.sh), rule 1 alone found it.

    rule 1  <path>.retired / .bak / .old / .disabled exists next to it
    rule 2  the same filename exists somewhere in the memory or tool trees
    rule 3  (nothing else) — leave it, and say so

What this deliberately does NOT do is decide whether a CLAIM is still true.
"The file moved" is a fact on disk. "This feature was deleted so the memory is
now wrong" is a judgement, and it is left for a person or a session.

The repaired line points at the successor and the dead path is NOT written
back. Writing it back in backticks would recreate the exact claim that was
failing — the repair would pass once and fail on the next grade. The old value
goes into the log instead.

    python3 repair.py                 what it would fix, changing nothing
    python3 repair.py --apply         fix them, and log every edit
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")
LOG_PATH = os.path.expanduser("~/.claude/meditation/repairs.jsonl")
SUFFIXES = (".retired", ".bak", ".old", ".disabled")
SEARCH_ROOTS = ("~/claude-sync/memory", "~/.claude/skills/meditate",
                "~/.claude/hooks")
MAX_WALK = 20000          # files; a runaway search is worse than no search


def _exists(p: str) -> bool:
    return os.path.exists(os.path.expanduser(p))


def successor(dead: str, roots: Optional[List[str]] = None) -> Optional[Dict[str, str]]:
    """Where did this file go? None when nothing on disk answers that."""
    p = os.path.expanduser(dead)
    for s in SUFFIXES:
        if os.path.exists(p + s):
            return {"path": dead + s, "rule": "retired twin (%s)" % s}
    base = os.path.basename(p)
    if not base:
        return None
    seen = 0
    for root in (roots or [os.path.expanduser(r) for r in SEARCH_ROOTS]):
        root = os.path.expanduser(root)
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            seen += len(files)
            if seen > MAX_WALK:
                return None
            if base in files:
                found = os.path.join(dirpath, base)
                if os.path.realpath(found) != os.path.realpath(p):
                    return {"path": found, "rule": "same filename, moved"}
    return None


def repair_file(md_path: str, dead_paths: List[str], apply: bool = False,
                roots: Optional[List[str]] = None) -> Dict[str, Any]:
    """Repoint every findable dead path in one memory file."""
    try:
        text = open(md_path).read()
    except OSError as e:
        return {"file": md_path, "fixed": [], "left": [], "error": str(e)}
    original = text
    fixed, left = [], []
    for dead in dict.fromkeys(dead_paths):        # de-dupe, keep order
        if _exists(dead):
            continue                              # not actually broken
        if dead not in text:
            left.append({"path": dead, "why": "not written in this file"})
            continue
        s = successor(dead, roots)
        if not s:
            left.append({"path": dead, "why": "no successor on disk"})
            continue
        # Straight token swap. The dead path does not survive anywhere in the
        # file — see the module docstring for why writing it back would undo
        # the repair on the next grade.
        text = text.replace(dead, s["path"])
        fixed.append({"path": dead, "now": s["path"], "rule": s["rule"]})

    if apply and text != original:
        try:
            tmp = md_path + ".repair.tmp"
            with open(tmp, "w") as f:
                f.write(text)
            os.replace(tmp, md_path)
        except OSError as e:
            return {"file": md_path, "fixed": [], "left": left, "error": str(e)}
    return {"file": md_path, "fixed": fixed, "left": left}


def dead_claims(store_dir: str = STORE_DIR) -> Dict[str, List[str]]:
    """Every memory file that names a path which is not there: file -> paths."""
    out: Dict[str, List[str]] = {}
    try:
        from ask import _load
        mems = _load(store_dir)
    except Exception:
        return out
    for m in mems:
        if not m.get("active"):
            continue
        for e in (m.get("evidence") or []):
            loc = str(e.get("locator") or "")
            if not loc.startswith("path:"):
                continue
            p = loc[5:].strip()
            if _exists(p):
                continue
            src = e.get("source") or ""
            if src:
                out.setdefault(src, []).append(p)
    return out


def run(apply: bool = False, store_dir: str = STORE_DIR) -> Dict[str, Any]:
    claims = dead_claims(store_dir)
    fixed, left = [], []
    for md, paths in claims.items():
        r = repair_file(md, paths, apply=apply)
        fixed.extend([dict(x, file=md) for x in r["fixed"]])
        left.extend([dict(x, file=md) for x in r["left"]])
    if apply and fixed:
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a") as f:
                for x in fixed:
                    f.write(json.dumps(dict(x, ts=time.strftime(
                        "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))) + "\n")
        except OSError:
            pass
    return {"files": len(claims), "fixed": fixed, "left": left,
            "applied": bool(apply)}


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Repoint memories at files that moved. No model involved.")
    ap.add_argument("--apply", action="store_true",
                    help="actually edit the .md files (default: say only)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = run(apply=a.apply)
    if a.json:
        print(json.dumps({"tool_name": "meditate_repair", "success": True,
                          "data": res, "metadata": {"log": LOG_PATH},
                          "errors": []}, indent=2))
        return 0
    if not res["fixed"] and not res["left"]:
        print("nothing to repair — every path the memories name is there")
        return 0
    for x in res["fixed"]:
        print("%s  %s\n    -> %s   (%s)"
              % ("fixed " if res["applied"] else "would fix",
                 x["path"], x["now"], x["rule"]))
    for x in res["left"]:
        print("left   %s\n    %s — needs a person" % (x["path"], x["why"]))
    if res["fixed"] and not res["applied"]:
        print("\nnothing was written. re-run with --apply")
    elif res["fixed"]:
        print("\nrepaired %d; run `meditate grade` to clear them" % len(res["fixed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
