"""creed — the owner's standing rules, derived from his own corrections.

ONE derivation, several consumers. Before this the same question was answered
in two places that could not agree:

    ~/.claude/meditation/rules.md   7 rules, hand-written, last touched 08-25
    advisor._creed()               41 rules, derived from the memory store

Measured 2026-08-29. All 7 hand-written ones ARE grounded in real memories —
nothing was invented for him — but 34 of his rules reached only the mascot,
which talks, and never the dispatched agents, which act. The ones missing
from the agents' side are exactly the action-governing ones: work in a
worktree, commit local first, clean the disk before a training run, make one
test call and read real usage before any bulk LLM run, auto-open every
deliverable in the Browser pane.

TWO THINGS THIS FIXES, and neither is a new layer:

1. ORDER IS RECENCY. The store holds rules that contradict each other because
   he changed his mind, which is his right: "commit to a LOCAL branch and
   STOP" (2026-07-10) against "push automatically when the suite is green"
   (2026-08-25, explicitly 'WIDENED'). Handed to a model unordered, the
   contradiction resolves by whichever sentence reads more forcefully — the
   exact failure advisor.py already documented for its hand-written persona.
   No semantic conflict-detector here: dates are parsed, newest leads, and
   the block SAYS a later rule supersedes an earlier one. Ordering is honest;
   guessing which of two rules "really" wins is not.

2. THE RIGHT RULES REACH THE RIGHT SURFACE. Action rules go to whoever is
   about to touch a repo; voice rules go to whoever is about to speak. Both
   are the same sentences from the same store, split by what they govern.

rules.md is NEVER written by this module. It is his file; the derived block
is appended alongside it, so a rule he types by hand cannot be deleted by a
derivation, and a rule he records in a memory cannot be missed by a stale
hand-copy.

    meditate creed              # the derived standing rules
    meditate creed --voice      # the taste half
    meditate creed --json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

# What makes a rule ACTION-governing: it constrains something an agent does to
# the world (a repo, a deploy, a provider, a spend), rather than how it talks.
_ACTION = re.compile(
    r"\b(git|worktree|branch|commit|push|deploy|prod|production|merge|rebase|"
    r"test|suite|verify|verified|proof|prove|live|browser|open|link|run|build|"
    r"ship|install|disk|train|training|cost|usage|token|api|provider|groq|"
    r"openrouter|cerebras|screenshot|delete|migration|secret|rotate)\b", re.I)

_DATE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")

_CACHE: Dict[str, Any] = {"key": None, "rules": None}


def _memory_files() -> List[str]:
    try:
        from nidra_bridge import _memory_dirs
        dirs = _memory_dirs()
    except Exception:
        return []
    out: List[str] = []
    for d in dirs:
        out.extend(glob.glob(os.path.join(d, "*.md")))
    return sorted(out)


def rules() -> List[Dict[str, Any]]:
    """Every standing rule he has given, with its date and what it governs.

    Only `feedback` (a correction, with the reason he gave) and `user` (who he
    is). Never `project` or `reference` — those are facts about the work and
    reach an agent through other channels; mixing them in would turn the creed
    into a briefing.
    """
    files = _memory_files()
    if not files:
        return []
    try:
        key = "%d:%f" % (len(files), max(os.path.getmtime(f) for f in files))
    except OSError:
        key = None
    if key and _CACHE["key"] == key and _CACHE["rules"] is not None:
        return _CACHE["rules"]

    out: List[Dict[str, Any]] = []
    for f in files:
        try:
            head = open(f, errors="ignore").read(2400)
        except OSError:
            continue
        t = re.search(r"^\s*type:\s*(\w+)", head, re.M)
        if not t or t.group(1) not in ("feedback", "user"):
            continue
        d = re.search(r"^description:\s*(.+)$", head, re.M)
        if not d:
            continue
        text = d.group(1).strip().strip('"').strip().replace('\\"', '"')
        # The date lives in the PROSE ("STANDING (owner, 2026-07-10)"), not in
        # frontmatter — these files carry no dated field. Body first, because
        # a description often repeats an older date than the note beneath it.
        dates = _DATE.findall(head)
        stamp = "-".join(max(dates)) if dates else ""
        out.append({
            "text": text,
            "date": stamp,
            "kind": "action" if _ACTION.search(text) else "voice",
            "source": os.path.basename(f),
        })
    # Newest first. Undated rules sort last: an undated rule cannot claim to
    # supersede a dated one, and pretending otherwise would let the oldest
    # sentence in the store outrank a correction he gave this week.
    out.sort(key=lambda r: r["date"] or "0000-00-00", reverse=True)
    if key:
        _CACHE.update({"key": key, "rules": out})
    return out


def standing(kind: Optional[str] = None, budget: int = 3200,
             rows: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The rules that fit, newest first, optionally only one kind.

    A budget that TRUNCATES, never one that summarises: the tail is dropped
    whole and the caller can say how many were dropped. A compressed rule is a
    rule someone rewrote, and a rewritten rule is not his any more.
    """
    rs = rows if rows is not None else rules()
    if kind:
        rs = [r for r in rs if r["kind"] == kind]
    out: List[Dict[str, Any]] = []
    used = 0
    for r in rs:
        n = len(r["text"]) + 3
        if used + n > budget:
            break
        out.append(r)
        used += n
    return out


def render(kind: Optional[str] = "action", budget: int = 3200,
           rows: Optional[List[Dict[str, Any]]] = None) -> str:
    """The block a prompt actually carries."""
    rs = rules() if rows is None else rows
    picked = standing(kind, budget, rs)
    if not picked:
        return ""
    pool = [r for r in rs if not kind or r["kind"] == kind]
    head = ("HOW HE WORKS — standing rules he gave, NEWEST FIRST; where two "
            "conflict the earlier one is superseded")
    lines = [head]
    for r in picked:
        lines.append("- %s%s" % (("[%s] " % r["date"]) if r["date"] else "", r["text"]))
    dropped = len(pool) - len(picked)
    if dropped:
        # Say the cap. A silent truncation reads as "that is all of them".
        lines.append("- (%d older rules not shown — `meditate creed` for all)" % dropped)
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate creed",
                                 description="the owner's standing rules, derived")
    ap.add_argument("--voice", action="store_true", help="the taste half")
    ap.add_argument("--all", action="store_true", help="both kinds")
    ap.add_argument("--budget", type=int, default=3200)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    kind = None if a.all else ("voice" if a.voice else "action")
    rs = rules()
    if a.json:
        print(json.dumps({"tool_name": "meditate_creed", "success": True,
                          "data": {"count": len(rs), "rules": rs},
                          "metadata": {}, "errors": []}, indent=2))
        return 0
    n_act = sum(1 for r in rs if r["kind"] == "action")
    print(render(kind, a.budget, rs) or "no rules recorded yet")
    print("\n%d rules in the store — %d action, %d voice" % (len(rs), n_act, len(rs) - n_act))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
