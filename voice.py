"""voice — Casper: the form that decides WHAT to say and WHETHER now is the moment.

Everything the ghost would tell you already exists in the data — projects,
drift, goals, fleet, the done-digest. Casper adds exactly two honest
judgements on top:

  briefing()          the ONE highest-leverage thing worth saying right now,
                      in plain words, with the next action named. Not a list —
                      a ghost that reads you ten items is noise, not presence.

  interruptibility()  is now a good moment? Measured, NOT mind-read. The only
                      thing observable is your ACTIVITY: editing in the last
                      minute = flow (never interrupt); live but idle a few
                      minutes = a natural pause (the moment); no live session =
                      away (talk to no one). It is a proxy for receptiveness,
                      and it says so — Casper never claims to feel your mood.

Speaking is a thin delivery step, not a speech engine: on macOS `--speak`
hands the headline to the built-in `say`. Any real TTS lane plugs in there.

    meditate voice            # the brief + whether now is the moment
    meditate voice --speak    # and say it aloud IF the moment is right
    meditate voice --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

MEDITATION_DIR = os.path.expanduser("~/.claude/meditation")
STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.join(
    MEDITATION_DIR, "nidra_store")
COORD_DIR = os.environ.get("MEDITATE_COORD_DIR") or os.path.expanduser(
    "~/.claude/coordination/sessions")

FLOW_S = 90          # edited within this = in flow, do NOT interrupt
PAUSE_MAX_S = 1800   # live but idle up to 30 min = a pause worth speaking into
LIVE_S = 3600        # presence younger than this = the session still counts


def interruptibility(coord_dir: str = COORD_DIR) -> Dict[str, Any]:
    """flow | pause | away — from real activity, labeled as a proxy."""
    newest = None
    now = time.time()
    if os.path.isdir(coord_dir):
        for fn in os.listdir(coord_dir):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(coord_dir, fn)
            try:
                age = now - os.path.getmtime(p)
            except OSError:
                continue
            if age > LIVE_S:
                continue
            if newest is None or age < newest:
                newest = age
    basis = "proxy for receptiveness, measured from edit activity (not mood)"
    if newest is None:
        return {"state": "away", "interrupt_ok": False,
                "since_s": None, "basis": basis,
                "why": "no live session — no one to speak to"}
    if newest < FLOW_S:
        return {"state": "flow", "interrupt_ok": False, "since_s": int(newest),
                "basis": basis, "why": "editing seconds ago — never break flow"}
    if newest < PAUSE_MAX_S:
        return {"state": "pause", "interrupt_ok": True, "since_s": int(newest),
                "basis": basis,
                "why": "live but idle %d min — a natural pause" % (newest // 60)}
    return {"state": "settled", "interrupt_ok": True, "since_s": int(newest),
            "basis": basis, "why": "long idle — safe to surface something"}


def briefing(meditation_dir: str = MEDITATION_DIR, store_dir: str = STORE_DIR,
             goals_dir: Optional[str] = None,
             history_path: Optional[str] = None) -> Dict[str, Any]:
    """The single most important thing to say — highest leverage first.

    Priority mirrors the tool's own law: broken knowledge before new work,
    then the fleet, then the most-attended project's next task, then quiet.
    """
    import status as st

    d = st.gather(meditation_dir=meditation_dir, store_dir=store_dir,
                  goals_dir=goals_dir, history_path=history_path)

    # 1. knowledge broke — the ghost's most urgent whisper
    if d.get("repair_open"):
        return {"headline": "Some of what I know stopped being true — "
                            "a few facts failed their own receipts.",
                "action": "meditate fix", "kind": "repair",
                "next": d.get("next", "")}

    # 2. dispatchable work waiting
    if d.get("dispatchable"):
        n = len(d["dispatchable"])
        g = d["dispatchable"][0]
        return {"headline": "%d goal%s ready to move, starting with %s."
                % (n, "s" if n != 1 else "", g.get("title", g.get("name", ""))),
                "action": "meditate go", "kind": "goals",
                "next": (g.get("next") or "")[:80]}

    # 3. the project you live in, and its open task
    goals = d.get("goals", [])
    live = [g for g in goals if g.get("next") and g["done"] < g["total"]]
    if live:
        g = live[0]
        return {"headline": "On %s you're at %.0f%% — next is %s."
                % (g["title"], g["pct"], (g["next"] or "")[:70]),
                "action": "meditate projects", "kind": "task",
                "next": g["next"]}

    # 4. stilling overdue
    days = d.get("still_days")
    if days is None or days > 3:
        return {"headline": "It's been a while since we cleared the mind — "
                            "%s sessions are waiting to settle."
                % (("%.0f days; " % days) if days else ""),
                "action": "/meditate", "kind": "still", "next": ""}

    return {"headline": "All clear — knowledge holds, goals are moving, "
                        "nothing is bothering me.",
            "action": "", "kind": "clear", "next": ""}


def _speak(text: str) -> bool:
    try:
        subprocess.Popen(["say", text[:400]], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


LAST_SAID = os.path.join(MEDITATION_DIR, ".casper-last.txt")


def _already_said(headline: str) -> bool:
    try:
        return open(LAST_SAID).read().strip() == headline.strip()
    except OSError:
        return False


def _remember_said(headline: str) -> None:
    try:
        os.makedirs(MEDITATION_DIR, exist_ok=True)
        with open(LAST_SAID, "w") as f:
            f.write(headline.strip())
    except OSError:
        pass


def _notify(headline: str, action: str) -> bool:
    """A native, dismissable macOS notification — the gentle delivery. Visual,
    not a voice barging in. Gated by the caller on interruptibility."""
    title = "\U0001F47B Casper"
    sub = action or "meditate"
    body = headline.replace('"', "'")[:200]
    script = ('display notification "%s" with title "%s" subtitle "%s"'
              % (body, title, sub))
    try:
        r = subprocess.run(["osascript", "-e", script], timeout=8,
                           capture_output=True)
        return r.returncode == 0
    except Exception:
        return False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Casper — what to say, and when")
    ap.add_argument("--speak", action="store_true",
                    help="say the headline aloud IF the moment is right")
    ap.add_argument("--notify", action="store_true",
                    help="post a native notification IF the moment is right")
    ap.add_argument("--force", action="store_true",
                    help="speak even if you're in flow (override the gate)")
    ap.add_argument("--quiet", action="store_true",
                    help="heartbeat mode: only deliver, print nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    b = briefing()
    t = interruptibility()
    data = {"briefing": b, "timing": t}

    moment_ok = t["interrupt_ok"] or args.force
    # on the heartbeat path (--quiet), suppress a headline already delivered —
    # don't re-nag the same thing every pause. --force always re-says.
    fresh = args.force or not _already_said(b["headline"])
    spoke = notified = False
    if b["headline"] and moment_ok and b["kind"] != "clear" and fresh:
        if args.speak:
            spoke = _speak(b["headline"])
        if args.notify:
            notified = _notify(b["headline"], b["action"])
        if spoke or notified:
            _remember_said(b["headline"])
    data["spoke"] = spoke
    data["notified"] = notified

    if args.json:
        print(json.dumps({"tool_name": "meditate_voice", "success": True,
                          "data": data, "metadata": {}, "errors": []}, indent=2))
        return 0

    if args.quiet:
        return 0
    print("👻 Casper")
    print("  \"%s\"" % b["headline"])
    if b["action"]:
        print("   → %s" % b["action"])
    print("  [%s] %s" % (t["state"], t["why"]))
    if args.speak and not spoke and not t["interrupt_ok"]:
        print("  (held my tongue — you're in flow. `--force` to override.)")
    elif spoke:
        print("  (spoke it aloud)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
