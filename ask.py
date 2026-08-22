"""ask — query the graded store; and the repair queue that closes the loop.

Two organs the system was missing:

1. SPEECH — the store could only serve ambiently (rules at SessionStart,
   facts on file edits). `meditate ask "query"` retrieves over every active
   memory (nidra's lexical tf-idf), verified facts ranked first, every hit
   carrying its grade. An unverified hit is SHOWN as unverified — the answer
   never launders a stale claim into clean fact.

2. CLOSED CORRECTION — `meditate drift` used to print failures that
   evaporated. Now every grade pass writes (or clears) the repair queue:
   ~/.claude/meditation/repair-queue.md — the exact failing claims with the
   lines to fix, phrased as work. SessionStart nudges one line while the
   queue exists. /meditate (or any session) consumes it; the next grade pass
   re-verifies and clears it. Detection deterministic, repair judgment.

CLI:
  meditate ask "what do we know about deploys"
  meditate ask "query" --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
NIDRA_ROOT = os.path.expanduser("~/projects/nidra")
sys.path.insert(0, NIDRA_ROOT)

STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")
MEDITATION_DIR = os.path.expanduser("~/.claude/meditation")
QUEUE_NAME = "repair-queue.md"

_RANK = {"machine_checked": 0, "source_linked": 1, "unverified": 2}


def _load(store_dir: str) -> List[Dict[str, Any]]:
    mp = os.path.join(store_dir, "memories.jsonl")
    out = []
    if os.path.exists(mp):
        with open(mp, errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    return out


def query(q: str, store_dir: str = STORE_DIR, k: int = 6) -> List[Dict[str, Any]]:
    """Relevance via nidra retrieval, then verified-first within the hit set."""
    try:
        from nidra.retrieval import retrieve
    except ImportError:
        return []
    mems = [m for m in _load(store_dir) if m.get("active")]
    hits = retrieve(mems, q, k=k * 2)
    hits.sort(key=lambda m: _RANK.get(
        m.get("epistemic", {}).get("evidence_status", "unverified"), 3))
    return hits[:k]


def write_repair_queue(drift: Dict[str, Any],
                       meditation_dir: str = MEDITATION_DIR) -> Optional[str]:
    """Materialize caught drift as WORK; remove the queue when the world is clean.

    Only memories with failing/flagged evidence become queue items — the
    evidence-free session stubs are noise, not work.
    """
    items = [m for m in drift.get("memories", [])
             if m.get("failing") or "drifted" in (m.get("flags") or [])]
    path = os.path.join(meditation_dir, QUEUE_NAME)
    if not items:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass
        return None
    lines = ["# Repair queue — knowledge that failed verification",
             "",
             "Each item is a memory whose evidence no longer matches the world.",
             "Fix the source .md (or supersede the memory), then run",
             "`meditate grade` — a clean re-check clears this file and counts",
             "as a REPAIR in `meditate report`.",
             ""]
    for m in items:
        lines.append("## %s  [%s]" % (m.get("id"), m.get("status")))
        lines.append("- statement: %s" % m.get("statement", "")[:200])
        for f in m.get("failing", []):
            lines.append("- FAILS %s" % f.get("claim"))
            if f.get("line"):
                lines.append("  - line: %s" % f["line"][:160])
        if not m.get("failing"):
            lines.append("- content anchor no longer matches its source "
                         "(the .md was edited after grading — refresh or supersede)")
        lines.append("")
    try:
        os.makedirs(meditation_dir, exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(lines))
        return path
    except OSError:
        return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Query the graded store")
    ap.add_argument("query", nargs="+", help="what to look up")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-k", type=int, default=6)
    args = ap.parse_args(argv)
    q = " ".join(args.query)
    hits = query(q, k=args.k)
    env = {"tool_name": "meditate_ask", "success": True,
           "data": {"query": q, "count": len(hits),
                    "hits": [{"id": m["id"],
                              "grade": m["epistemic"]["evidence_status"],
                              "statement": m["statement"]} for m in hits]},
           "metadata": {"store_dir": STORE_DIR}, "errors": []}
    if args.json:
        print(json.dumps(env, indent=2))
        return 0
    if not hits:
        print("No graded memory matches: %s" % q)
        return 0
    print("Graded memory — %d hit(s) for: %s" % (len(hits), q))
    for m in hits:
        g = m["epistemic"]["evidence_status"]
        mark = "✓" if g == "machine_checked" else ("~" if g == "source_linked" else "✗ UNVERIFIED")
        print("  %s %s" % (mark, m["statement"][:160]))
        if g == "unverified":
            print("      (evidence failed or absent — do not act on this without checking)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
