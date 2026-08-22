"""vocabulary — the words YOUR work is made of, for the recogniser to expect.

A general speech recogniser has never heard of your projects. Measured on this
machine, "Casper run nidra grade on PuranGPT then check Mila and the sangama
peers" came back as "...on Param then check Miller and the son appears" —
4 wrong words out of 14, every single one a name from this workspace.

Apple's recogniser takes `contextualStrings`: a list of terms to expect. Feed
it the names that actually appear in your goals, projects and memories and the
errors go away, because they stop being unheard-of words.

Nothing here is hardcoded to one person's projects — the terms are derived
from whatever this install already knows about.

    python3 vocabulary.py --json      # {"data": {"terms": [...]}}
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")

# The recogniser degrades if you hand it a dictionary; it wants the words that
# are surprising, not every word you know.
MAX_TERMS = 100

# What you can say TO the tool. These are fixed because they are the tool's own
# verbs, not anybody's project names.
COMMANDS = [
    "Casper", "meditate", "nidra", "sangama", "pulse",
    "grade", "repair queue", "sleep pass", "fleet", "dispatch",
    "stillness", "graded memory", "drift", "receipt",
]

_STOP = {
    "The", "This", "That", "There", "These", "Those", "It", "Its", "If", "In",
    "On", "At", "To", "For", "And", "But", "Or", "So", "We", "You", "Your",
    "My", "I", "A", "An", "Not", "No", "Now", "Next", "Open", "Fix", "Live",
    "Was", "Is", "Are", "Be", "Been", "Has", "Have", "Had", "Do", "Does",
    "Did", "Will", "Would", "Can", "Could", "Should", "One", "Two", "Both",
    "All", "Only", "Never", "Always", "Read", "Use", "Run", "Add", "New",
    "Old", "Then", "When", "What", "Which", "Who", "Why", "How", "From",
    "With", "Without", "Before", "After", "Every", "Each", "Some", "Any",
    "LIVE", "OPEN", "FIXED", "DEAD", "NEXT", "LAW", "TODO", "NOTE", "WARN",
}


_DICT_PATH = "/usr/share/dict/words"
_ENGLISH: Optional[set] = None


def _english() -> set:
    """The system word list, used to throw ordinary English away.

    Without this the vocabulary filled up with WAITING, FIRST, LICENSE and NOT
    — this corpus writes status flags in capitals, and a rule that treats
    ALLCAPS as a product name learns emphasis instead of names. Under a 100-term
    cap that junk crowds out the words the recogniser actually needs.
    """
    global _ENGLISH
    if _ENGLISH is None:
        try:
            with open(_DICT_PATH, errors="ignore") as f:
                _ENGLISH = {ln.strip().lower() for ln in f if ln.strip()}
        except OSError:
            _ENGLISH = set()
    return _ENGLISH


def _is_name(word: str) -> bool:
    """A word worth teaching the recogniser: a name or an odd spelling, not an
    ordinary English word that happened to start a sentence."""
    w = word.strip(".,;:!?()[]{}\"'`—–")
    if len(w) < 3 or len(w) > 28:
        return False
    if w in _STOP or not w[0].isalpha():
        return False
    if any(ch.isdigit() for ch in w):
        return False
    # An inner capital is a product name almost every time: PuranGPT, GitHub.
    if w[0].isupper() and any(c.isupper() for c in w[1:]) and not w.isupper():
        return True
    if w.isupper():
        return False          # emphasis in this corpus, not a name
    if not w[0].isupper():
        return False
    # Capitalised and NOT a word the language already has = a name.
    return w.lower() not in _english()


def _from_goals(goals_dir: Optional[str] = None) -> List[str]:
    try:
        import goals as gl
        rows = gl.scan(**({"goals_dir": goals_dir} if goals_dir else {}))
    except Exception:
        return []
    out: List[str] = []
    for g in rows:
        title = (g.get("title") or "").strip()
        title = re.sub(r"\(.*?\)", "", title).split("\u2014")[0].strip(" -\u2014")
        if title:
            out.append(" ".join(title.split()[:4]))   # a phrase, not an essay
        for w in re.findall(r"[A-Za-z][\w-]*", (g.get("next") or "")):
            if _is_name(w):
                out.append(w)
    return out


def _from_projects() -> List[str]:
    try:
        from projects import rollup
        return [r["project"] for r in rollup()
                if r.get("project") and (r.get("messages") or r.get("goals"))]
    except Exception:
        return []


def _from_memories(store_dir: str = STORE_DIR, limit: int = 400) -> List[str]:
    """Proper nouns the owner has actually used, most-recent memories first."""
    try:
        from ask import _load
        mems = [m for m in _load(store_dir) if m.get("active")]
    except Exception:
        return []
    seen: Dict[str, int] = {}
    for m in mems[-limit:]:
        for w in re.findall(r"[A-Za-z][\w.-]*", m.get("statement", "")):
            w = w.strip(".-")
            if _is_name(w):
                seen[w] = seen.get(w, 0) + 1
    # frequency order: a name used ten times matters more than one used once
    return [w for w, _ in sorted(seen.items(), key=lambda kv: -kv[1])]


def terms(store_dir: str = STORE_DIR, goals_dir: Optional[str] = None,
          max_terms: int = MAX_TERMS) -> List[str]:
    """The vocabulary, most important first, de-duplicated case-insensitively."""
    ordered: List[str] = []
    ordered += COMMANDS
    ordered += _from_projects()
    ordered += _from_goals(goals_dir)
    ordered += _from_memories(store_dir)

    out: List[str] = []
    seen = set()
    for t in ordered:
        t = " ".join(str(t).split())
        key = t.lower()
        if not t or key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= max_terms:
            break
    return out


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Words the recogniser should expect")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    ts = terms()
    if a.json:
        print(json.dumps({"tool_name": "meditate_vocabulary", "success": True,
                          "data": {"terms": ts, "count": len(ts)},
                          "metadata": {"store_dir": STORE_DIR,
                                       "max_terms": MAX_TERMS},
                          "errors": []}, indent=2))
    else:
        for t in ts:
            print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
