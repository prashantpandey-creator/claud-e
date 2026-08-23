"""address — what he calls you.

A companion that never uses your name is a search box with a voice. This is
one small setting rather than a word hardcoded into five files, because the
right answer is personal: sir, boss, mate, or your actual name.

    ~/.claude/meditation/address        one line, e.g. "sir"
    MEDITATE_ADDRESS=boss               overrides it

Used sparingly on purpose. Every sentence is grovelling; once, at the point
where he hands the decision back, is a person talking.
"""
from __future__ import annotations

import os

PATH = os.path.expanduser("~/.claude/meditation/address")
DEFAULT = "sir"


def term() -> str:
    """What to call the owner. Never empty, never a sentence."""
    v = (os.environ.get("MEDITATE_ADDRESS") or "").strip()
    if not v:
        try:
            with open(PATH) as f:
                v = f.read().strip()
        except OSError:
            v = ""
    v = " ".join((v or DEFAULT).split())[:24]
    return v or DEFAULT


def set_term(value: str) -> str:
    v = " ".join((value or "").split())[:24] or DEFAULT
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w") as f:
        f.write(v + "\n")
    return v


def close(sentence: str) -> str:
    """Attach the address to a closing line, without doubling punctuation."""
    s = (sentence or "").rstrip()
    if not s:
        return s
    t = term()
    if t.lower() in s.lower():
        return s
    tail = ""
    while s and s[-1] in ".!?":
        tail = s[-1] + tail
        s = s[:-1]
    return "%s, %s%s" % (s, t, tail or ".")
