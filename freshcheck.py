"""freshcheck — has this install actually been used yet?

Several suites assert on the LIVE world: coverage above zero, graded facts
served at edit time, a heartbeat that has fired. Those are the right
assertions on a machine that has been running for a week and the wrong ones
five seconds after install — where they went red and told a new user the tool
was broken when it had simply never been used.

"No data yet" is not a failure. It is a different state, and it needs a
different word.
"""
from __future__ import annotations

import os

STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")
MEDITATION_DIR = os.path.expanduser("~/.claude/meditation")


def graded_memories() -> int:
    p = os.path.join(STORE_DIR, "memories.jsonl")
    try:
        with open(p, errors="replace") as f:
            return sum(1 for ln in f if ln.strip())
    except OSError:
        return 0


def heartbeat_fired() -> bool:
    return os.path.exists(os.path.join(MEDITATION_DIR, "heartbeat.log"))


def is_fresh(min_memories: int = 5) -> bool:
    """True when this install has not been used enough to judge."""
    return graded_memories() < min_memories or not heartbeat_fired()


def skip(reason: str = "") -> bool:
    """Print a skip line and return True, for suites that run standalone."""
    print("  SKIP  fresh install — nothing to measure yet%s"
          % (": " + reason if reason else ""))
    return True
