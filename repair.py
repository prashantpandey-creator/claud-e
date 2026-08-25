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


# ---- the index guard --------------------------------------------------------
# Measured 2026-08-25 on the live store. The graded store gated exactly ONE
# delivery lane — coordination.facts_for(), <=2 machine_checked facts per edit,
# 3 facts served in 24h. The lane that actually carries the weight is MEMORY.md:
# ~5,000 tokens read into EVERY session by Claude Code's own harness, which no
# hook loads and which nothing here ever wrote back to. A demoted memory kept
# being read into every session, verbatim, until a person ran /meditate.
#
# The funnel that made it obvious: 638 stored -> 563 active -> 561
# machine_checked -> 71 reachable by the serving index (12.6%). 492 active
# memories could not reach a session through the graded lane at all.
#
# This does not edit the index. It says which lines to look at, and stays quiet
# otherwise — on the store as it stands today it flags zero.

_LINK = re.compile(r"\[[^\]]*\]\(([^)]+\.md)\)")


def _index_status(store_dir: str) -> Dict[str, str]:
    """basename of a memory file -> its worst reason to distrust it, or ''."""
    try:
        from ask import _load
        mems = _load(store_dir)
    except Exception:
        # Cannot read the grades. Say NOTHING. "I could not check" must never
        # render as "it is stale" — that inversion is this project's oldest bug.
        return {}
    worst: Dict[str, str] = {}
    for m in mems:
        if not m.get("active"):
            continue                      # superseded is history, not rot
        reason = ""
        if "drifted" in (m.get("flags") or []):
            reason = "drifted"            # can be flagged while grade recovered
        else:
            st = (m.get("epistemic") or {}).get("evidence_status")
            if st and st != "machine_checked":
                reason = st
        for e in (m.get("evidence") or []):
            src = str(e.get("source") or "")
            if not src.endswith(".md"):
                continue
            base = os.path.basename(src)
            if reason:
                worst[base] = reason      # any bad memory taints the file
            else:
                worst.setdefault(base, "")
    return worst


def stale_index_lines(memory_dir: Optional[str] = None,
                      store_dir: str = STORE_DIR) -> List[Dict[str, Any]]:
    """MEMORY.md lines whose target is demoted, drifted, or not on disk.

    A target the store has never graded is NOT flagged. "No memory for this
    file" means the grader has not seen it, not that the fact is wrong; saying
    "broken" when you mean "I don't know" is the defect this tool exists to
    catch, and it would be embarrassing to ship it here.
    """
    if memory_dir is None:
        # NOT paths.memory_root() — that is the PARENT of the per-project dirs
        # (~/claude-sync/memory), which holds no MEMORY.md of its own, so the
        # default silently checked nothing. Measured: 9 memory dirs exist, one
        # per cwd-slug; the four PuranGPT cwds are symlinks onto a single
        # backing dir, so checking every dir cannot double-report them.
        try:
            from nidra_bridge import _memory_dirs
            dirs = _memory_dirs()
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for d in dirs:
            out.extend(stale_index_lines(d, store_dir))
        return out
    index = os.path.join(memory_dir, "MEMORY.md")
    try:
        with open(index, errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    status = _index_status(store_dir)
    out: List[Dict[str, Any]] = []
    for n, line in enumerate(lines, 1):
        for target in _LINK.findall(line):
            if "://" in target:
                continue                  # an external link is not a memory
            base = os.path.basename(target)
            if not _exists(os.path.join(memory_dir, target)):
                reason = "missing_file"
            else:
                reason = status.get(base) or ""
            if reason:
                out.append({"line": n, "target": target, "reason": reason,
                            "text": line.strip()[:120]})
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
