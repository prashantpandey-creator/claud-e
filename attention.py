"""attention — is the person here, and are they in the middle of something?

The old answer was a proxy: how long ago did a session touch a file. That is
a guess about a human made from the timestamps of a program. It said "away —
no live session" while two sessions were running and someone was typing, and
it silenced the companion for a whole evening.

This asks the machine directly:

  idle_s     seconds since the last key or mouse event   (ioreg, exact)
  frontmost  the app in front right now                  (System Events)
  in_meeting a meeting app is the one in front

Deliberately NOT used, having been checked and found worthless here:
  - "a meeting app is running": matched com.apple.audio and facetimemessage,
    background helpers that are always alive. Running is not meeting.
  - Do Not Disturb / Focus: ~/Library/DoNotDisturb is unreadable without Full
    Disk Access. Asking the owner for that, to read a flag, is a bad trade.

Every field degrades to None rather than to a guess, and the caller is told
which signals it actually got.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

# Apps that mean "do not speak": if one of these is in front, there is almost
# certainly another human in the conversation already.
MEETING_APPS = {"zoom.us", "zoom", "Microsoft Teams", "Teams", "Webex",
                "Cisco Webex Meetings", "FaceTime", "Google Meet", "Skype",
                "Discord", "GoToMeeting", "BlueJeans", "Around", "Whereby"}

# Apps that mean "making something" as opposed to reading or browsing.
WORK_APPS = {"Terminal", "iTerm2", "iTerm", "Alacritty", "kitty", "WezTerm",
             "Code", "Visual Studio Code", "Cursor", "Xcode", "Zed",
             "IntelliJ IDEA", "PyCharm", "WebStorm", "GoLand", "CLion",
             "Sublime Text", "Neovim", "Emacs", "Claude", "Warp", "Ghostty"}

IDLE_CMD = ["ioreg", "-c", "IOHIDSystem"]
FRONT_CMD = ["osascript", "-e",
             'tell application "System Events" to return name of first '
             'application process whose frontmost is true']


def idle_seconds() -> Optional[float]:
    """Seconds since the last human key press or mouse move.

    This is the signal the whole thing turns on: it is about the PERSON, not
    about a process, and it is exact rather than inferred.
    """
    try:
        out = subprocess.run(IDLE_CMD, capture_output=True, text=True,
                             timeout=4).stdout
    except Exception:
        return None
    m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
    if not m:
        return None
    return int(m.group(1)) / 1_000_000_000.0


def frontmost_app() -> Optional[str]:
    try:
        r = subprocess.run(FRONT_CMD, capture_output=True, text=True, timeout=6)
    except Exception:
        return None
    name = (r.stdout or "").strip()
    return name or None


def signals() -> Dict[str, Any]:
    idle = idle_seconds()
    front = frontmost_app()
    got: List[str] = []
    if idle is not None:
        got.append("idle")
    if front:
        got.append("frontmost")
    return {
        "idle_s": idle,
        "frontmost": front,
        "in_meeting": bool(front and front in MEETING_APPS),
        "at_work_app": bool(front and front in WORK_APPS),
        "measured": got,
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Is the person here and busy?")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    s = signals()
    if a.json:
        print(json.dumps({"tool_name": "meditate_attention", "success": True,
                          "data": s, "metadata": {}, "errors": []}, indent=2))
    else:
        idle = s["idle_s"]
        print("idle      %s" % ("%.1f s" % idle if idle is not None else "unknown"))
        print("frontmost %s" % (s["frontmost"] or "unknown"))
        print("meeting   %s" % ("yes — stay quiet" if s["in_meeting"] else "no"))
        print("making    %s" % ("yes" if s["at_work_app"] else "no"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
