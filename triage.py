"""triage — which chats are worth going back to, and which are done.

A roster of 56 "live" sessions is a wall you scroll past. Almost none of them
want anything from you; a few are sitting on an unanswered question. The
difference is not recency, and it is not size — it is whether the last thing
that happened left something OPEN.

So each chat is read from its tail and sorted into one of:

    waiting    the last word was YOURS and nothing answered it, or the
               assistant asked you something and you never replied
    mid-work   it stopped in the middle of doing something
    resumable  substantial, recent, nothing explicitly open
    finished   it concluded; safe to archive
    stale      old and nothing open

Only `waiting` and `mid-work` become action items, because those are the only
two states where a human is actually owed something. Everything else is
counted and collapsed — that is the point, to stop holding every chat in your
head at once.

Reading tails, not whole files: 155 transcripts, ~64 KB each from the end.

    python3 triage.py             # ranked, human readable
    python3 triage.py --json      # envelope
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

PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")
TAIL_BYTES = 96_000          # enough for the last several exchanges
MIN_MESSAGES = 3             # below this there is no thread to resume
STALE_DAYS = 14

# The assistant stopped mid-stride: it said what it was about to do next.
_MID_WORK = re.compile(
    r"\b(i'?ll (now|next|go|start|run|check|add|wire|build)|let me (now|go|run|check)"
    r"|next (step|up|i)|about to|starting (on|with)|then i'?ll|working on it"
    r"|one moment|hold on)\b", re.I)

# It wrapped up.
_CONCLUDED = re.compile(
    r"\b(all (done|green|set)|that'?s (it|everything|done)|nothing (else|left)"
    r"|complete[d]?\.|finished\.|shipped|pushed|doctor: all green"
    r"|let me know if|anything else)\b", re.I)

# The assistant put a question to you and stopped.
_ASKED_YOU = re.compile(r"\?\s*$")


def _tail_objects(path: str, nbytes: int = TAIL_BYTES) -> List[Dict[str, Any]]:
    """Parse whatever whole JSON lines live in the last nbytes of the file."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > nbytes:
                f.seek(size - nbytes)
                f.readline()          # discard the partial line
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if isinstance(o, dict):
            out.append(o)
    return out


def _text_of(msg: Dict[str, Any]) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = [p.get("text", "") for p in c
                 if isinstance(p, dict) and p.get("type") == "text"]
        return " ".join(x for x in parts if x)
    return ""


def _is_noise(text: str) -> bool:
    """Hook output, tool results and system reminders are not a person talking."""
    t = text.strip()
    if not t:
        return True
    return (t.startswith("<") or t.startswith("Caveat:")
            or "system-reminder" in t[:200]
            or t.startswith("[Request interrupted"))


def _is_system_prompt(text: str) -> bool:
    """A prompt the tool wrote for itself, not something a person typed."""
    t = (text or "").lstrip()
    if len(t) < 400:
        return False
    head = t[:80].lower()
    return (head.startswith("you are ") or head.startswith("system:")
            or "hard rules:" in t[:1500].lower())


def last_turn(path: str) -> Dict[str, Any]:
    """Who spoke last, what they said, and whether a HUMAN said it.

    NOT promptSource. That looked like the signal and is not: "lets get back
    to tutor" — plainly a person, typos and all — is tagged `sdk`, because the
    field records the TRANSPORT (the desktop app) and not whether a human was
    at the keyboard. Filtering on it threw away every real chat.

    The tool does start sessions of its own — the advisor is one per question —
    and each looks like an unanswered message, because nobody replies to a
    prompt. What marks those is the SHAPE of the opening turn: a system prompt.
    Nobody opens a chat by typing "You are ..." followed by a page of rules.
    """
    role, text, ts = "", "", ""
    first_user = ""
    for o in _tail_objects(path):
        msg = o.get("message")
        if not isinstance(msg, dict):
            continue
        r = msg.get("role")
        if r not in ("user", "assistant"):
            continue
        t = _text_of(msg)
        if r == "user" and _is_noise(t):
            continue
        if not t.strip():
            continue
        role, text, ts = r, t, o.get("timestamp") or ts
        if r == "user" and not first_user:
            first_user = t
    return {"role": role, "text": text.strip(), "ts": ts,
            "machine": _is_system_prompt(first_user)}


def _age_hours(ts: str) -> Optional[float]:
    if not ts:
        return None
    try:
        import datetime as dt
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return max(0.0, (time.time() - d.timestamp()) / 3600.0)
    except Exception:
        return None


def classify(turn: Dict[str, Any], age_h: Optional[float],
             messages: int) -> Dict[str, Any]:
    """One chat's state, and whether it is owed anything.

    Order matters: an unanswered question outranks everything, because it is
    the only state where the chat is waiting on YOU rather than the reverse.
    """
    text = (turn.get("text") or "")[-1200:]
    role = turn.get("role")
    old = age_h is not None and age_h > STALE_DAYS * 24

    if messages < MIN_MESSAGES:
        return {"state": "stale", "action": None, "why": "barely started"}

    # An unanswered message decays: after a week the moment has passed and
    # calling it "waiting" is false urgency — a 23-day-old half-sentence was
    # being quoted back as the top thing owed.
    waiting_dead = age_h is not None and age_h > 7 * 24

    if role == "user":
        if waiting_dead:
            return {"state": "stale", "action": None,
                    "why": "unanswered, but %d days old" % int(age_h / 24)}
        return {"state": "waiting", "action": "reply",
                "why": "you spoke last and nothing answered"}

    if role == "assistant" and _ASKED_YOU.search(text.strip()):
        if waiting_dead:
            return {"state": "stale", "action": None,
                    "why": "its question expired unanswered"}
        return {"state": "waiting", "action": "reply",
                "why": "it asked you something and stopped"}

    if role == "assistant" and _MID_WORK.search(text) and not _CONCLUDED.search(text):
        return {"state": "mid-work", "action": "resume",
                "why": "it stopped part-way through something"}

    if role == "assistant" and _CONCLUDED.search(text):
        return {"state": "finished", "action": "archive",
                "why": "it wrapped up"}

    if old:
        return {"state": "stale", "action": None,
                "why": "nothing open, and %d days old" % int(age_h / 24)}

    return {"state": "resumable", "action": None, "why": "nothing explicitly open"}


def _score(state: str, age_h: Optional[float], messages: int) -> float:
    """Rank within a state: fresher and meatier first, but never across states."""
    recency = 1.0 / (1.0 + (age_h or 0) / 24.0)      # halves each day
    substance = min(1.0, messages / 40.0)
    base = {"waiting": 100, "mid-work": 60, "resumable": 20,
            "finished": 5, "stale": 0}[state]
    return round(base + 20 * recency + 10 * substance, 2)


def triage(root: str = PROJECTS_ROOT, max_files: int = 400) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    try:
        slugs = [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
    except OSError:
        return {"sessions": [], "counts": {}, "action_items": []}

    paths: List[str] = []
    for slug in slugs:
        d = os.path.join(root, slug)
        for fn in os.listdir(d):
            if fn.endswith(".jsonl"):
                paths.append(os.path.join(d, fn))
    # newest first, and cap the work
    paths.sort(key=lambda p: -os.path.getmtime(p))
    paths = paths[:max_files]

    programmatic = 0
    for p in paths:
        try:
            if os.path.getsize(p) < 2000:
                continue
        except OSError:
            continue
        turn = last_turn(p)
        if not turn["role"]:
            continue
        if turn.get("machine"):
            programmatic += 1           # the tool's own calls are not chats
            continue
        age_h = _age_hours(turn["ts"])
        if age_h is None:
            age_h = max(0.0, (time.time() - os.path.getmtime(p)) / 3600.0)
        objs = _tail_objects(p, 24_000)
        messages = sum(1 for o in objs
                       if isinstance(o.get("message"), dict)
                       and o["message"].get("role") == "user")
        c = classify(turn, age_h, messages)
        rows.append({
            "id": os.path.basename(p)[:-6],
            "short": os.path.basename(p)[:8],
            "age_h": round(age_h, 1),
            "messages": messages,
            "state": c["state"],
            "action": c["action"],
            "why": c["why"],
            "last_role": turn["role"],
            "last_said": " ".join(turn["text"].split())[:120],
            "score": _score(c["state"], age_h, messages),
        })

    rows.sort(key=lambda r: -r["score"])
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    items = [r for r in rows if r["action"] in ("reply", "resume")][:8]
    return {"sessions": rows, "counts": counts, "action_items": items,
            "programmatic_skipped": programmatic}


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="meditate chats", description="Which chats want something from you")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all", action="store_true", help="list every chat, not just the ones owed")
    a = ap.parse_args(argv)
    d = triage()
    if a.json:
        print(json.dumps({"tool_name": "meditate_triage", "success": True,
                          "data": d, "metadata": {"root": PROJECTS_ROOT},
                          "errors": []}, indent=2))
        return 0

    c = d["counts"]
    total = sum(c.values())
    if d.get("programmatic_skipped"):
        print("(%d headless tool calls skipped — not chats)"
              % d["programmatic_skipped"])
    print("%d chats — %s" % (total, ", ".join(
        "%d %s" % (v, k) for k, v in sorted(c.items(), key=lambda kv: -kv[1]))))
    if not d["action_items"]:
        print("\nNothing is waiting on you.")
        return 0
    print("\nWANTS SOMETHING FROM YOU")
    for r in d["action_items"]:
        verb = "reply" if r["action"] == "reply" else "resume"
        print("  %-8s %-6s %4.0fh  %s" % (verb, r["short"], r["age_h"], r["why"]))
        print("           %s" % r["last_said"][:96])
    if a.all:
        print("\nEVERYTHING ELSE")
        for r in d["sessions"]:
            if r["action"] in ("reply", "resume"):
                continue
            print("  %-9s %-6s %4.0fh  %s" % (r["state"], r["short"], r["age_h"], r["why"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
