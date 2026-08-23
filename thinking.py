"""thinking — what he is doing RIGHT NOW, for the face to show while you wait.

A companion that goes quiet for forty seconds and then answers has, from your
side of the screen, hung. The fix is not a spinner: a spinner tells you
something is happening, which you already assumed. What you want to know is
WHAT.

So each slow step writes one short line here as it starts, and the mascot reads
it. Every line is a real stage that really ran — nothing here invents progress,
and there is no percentage, because none of these steps knows how long it will
take.

    note("reading what I know")
    ...
    clear()
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

PATH = os.environ.get("MEDITATE_THINKING_FILE") or os.path.expanduser(
    "~/.claude/meditation/thinking.jsonl")

# Anything older than this is a crash, not a thought. Without it a process that
# died mid-step would leave the face saying "reading what I know" forever.
STALE_S = 120.0


def note(step: str, detail: str = "") -> None:
    """Record the step that is starting now. Never raises into the caller."""
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        with open(PATH, "w") as f:
            f.write(json.dumps({"step": step[:80], "detail": detail[:80],
                                "ts": time.time()}) + "\n")
    except OSError:
        pass


def read() -> Optional[Dict[str, Any]]:
    """The current step, or None when nothing is running or it went stale."""
    try:
        with open(PATH) as f:
            row = json.loads(f.readline() or "{}")
    except (OSError, ValueError):
        return None
    if not row.get("step"):
        return None
    if time.time() - float(row.get("ts", 0)) > STALE_S:
        return None
    row["age_s"] = round(time.time() - float(row.get("ts", 0)), 1)
    return row


def clear() -> None:
    try:
        with open(PATH, "w") as f:
            f.write("")
    except OSError:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="What the companion is doing now")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--clear", action="store_true")
    a = ap.parse_args(argv)
    if a.clear:
        clear()
        return 0
    r = read()
    if a.json:
        print(json.dumps({"tool_name": "meditate_thinking", "success": True,
                          "data": r or {}, "metadata": {"path": PATH},
                          "errors": []}, indent=2))
    else:
        print(r["step"] if r else "")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
