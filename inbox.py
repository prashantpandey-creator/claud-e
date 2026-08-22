"""inbox — agents talking to each other. The missing orchestration primitive.

Until now sessions could SEE each other (presence) and report upward
(beacons), but never speak to one another. Two agents on the same repo could
only collide and be warned. Now they can coordinate.

    meditate tell <to> "message"     # to = session id | project | goal | all
    meditate inbox                   # what is waiting for me
    meditate inbox --sid X           # someone else's (for the dashboard)

Delivery rides the channel that already works: the hook. An agent with
unread mail gets it injected at SessionStart AND at its next tool use — so a
message reaches a working agent within one action, not "whenever it looks".

Two laws, both tested:
  - delivered ONCE. A message repeated on every tool call is pressure, and
    pressure is what this whole layer exists to remove.
  - addressed, not broadcast-by-default. `all` exists but must be typed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

COORD_ROOT = os.environ.get("MEDITATE_COORD_ROOT") or os.path.expanduser(
    "~/.claude/coordination")
MSG_PATH = os.path.join(COORD_ROOT, "messages.jsonl")
READ_PATH = os.path.join(COORD_ROOT, "messages-read.json")
MAX_DELIVER = 3          # per fetch — a wall of mail is noise, not coordination
KEEP_HOURS = 48          # older mail is history, not a message


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def send(to: str, body: str, frm: str = "", coord_root: str = COORD_ROOT) -> Dict[str, Any]:
    """Address a message. `to` is a session id prefix, a project, a goal, or 'all'."""
    msg = {"id": "%s-%d" % (re.sub(r"[^a-z0-9]", "", to.lower())[:12], int(time.time() * 1000) % 10**9),
           "to": to.strip(), "from": (frm or os.environ.get("MEDITATE_SID") or "owner")[:16],
           "body": body.strip()[:600], "ts": _now()}
    os.makedirs(coord_root, exist_ok=True)
    with open(os.path.join(coord_root, "messages.jsonl"), "a") as f:
        f.write(json.dumps(msg) + "\n")
    return msg


def _read_ids(coord_root: str) -> Dict[str, List[str]]:
    try:
        with open(os.path.join(coord_root, "messages-read.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def _mark(sid: str, ids: List[str], coord_root: str) -> None:
    r = _read_ids(coord_root)
    r.setdefault(sid[:16], [])
    r[sid[:16]] = (r[sid[:16]] + ids)[-200:]      # bounded
    try:
        os.makedirs(coord_root, exist_ok=True)
        tmp = os.path.join(coord_root, "messages-read.json.tmp")
        with open(tmp, "w") as f:
            json.dump(r, f)
        os.replace(tmp, os.path.join(coord_root, "messages-read.json"))
    except OSError:
        pass


def _matches(msg: Dict[str, Any], sid: str, cwd: str, projects: List[str]) -> bool:
    to = str(msg.get("to", "")).lower()
    if to == "all":
        return True
    # exact id, or a prefix of at least 4 chars (real session ids are UUIDs
    # addressed by their first 8; the old >=6 guard silently dropped short ids)
    if sid and to:
        s = sid.lower()
        if s == to or (len(to) >= 4 and s.startswith(to)):
            return True
    if to in [p.lower() for p in projects]:
        return True
    return False


def fetch(sid: str, cwd: str = "", coord_root: str = COORD_ROOT,
          mark_read: bool = True) -> List[Dict[str, Any]]:
    """Unread mail addressed to this session. Marks delivered unless asked not to."""
    path = os.path.join(coord_root, "messages.jsonl")
    if not os.path.exists(path):
        return []
    try:
        from projects import normalize
        projects = [normalize(cwd)] if cwd else []
    except Exception:
        projects = []
    already = set(_read_ids(coord_root).get(sid[:16], []))
    cutoff = time.time() - KEEP_HOURS * 3600
    out: List[Dict[str, Any]] = []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if m.get("id") in already or m.get("from", "")[:16] == sid[:16]:
                    continue                       # never deliver my own mail
                try:
                    t = time.mktime(time.strptime(str(m.get("ts", ""))[:19],
                                                  "%Y-%m-%dT%H:%M:%S"))
                    if t < cutoff:
                        continue
                except Exception:
                    pass
                if _matches(m, sid, cwd, projects):
                    out.append(m)
    except OSError:
        return []
    out = out[:MAX_DELIVER]
    if out and mark_read:
        _mark(sid, [m["id"] for m in out], coord_root)
    return out


def render(msgs: List[Dict[str, Any]]) -> str:
    """The line(s) the hook injects — plain, addressed, actionable."""
    if not msgs:
        return ""
    if len(msgs) == 1:
        m = msgs[0]
        return "MESSAGE from %s: %s" % (m["from"][:8], m["body"])
    return "MESSAGES (%d):\n" % len(msgs) + "\n".join(
        "  from %s: %s" % (m["from"][:8], m["body"][:180]) for m in msgs)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Agent-to-agent messages")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("send"); s.add_argument("to"); s.add_argument("body", nargs="+")
    s.add_argument("--from", dest="frm", default="")
    r = sub.add_parser("read"); r.add_argument("--sid", default=os.environ.get("MEDITATE_SID", ""))
    r.add_argument("--cwd", default=os.getcwd()); r.add_argument("--peek", action="store_true")
    r.add_argument("--json", action="store_true")
    l = sub.add_parser("list"); l.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "send":
        m = send(args.to, " ".join(args.body), args.frm)
        print("sent to %s: %s" % (m["to"], m["body"][:80]))
        return 0
    if args.cmd == "read":
        msgs = fetch(args.sid or "unknown", args.cwd, mark_read=not args.peek)
        if args.json:
            print(json.dumps({"tool_name": "meditate_inbox", "success": True,
                              "data": {"count": len(msgs), "messages": msgs},
                              "metadata": {"sid": args.sid}, "errors": []}, indent=2))
        else:
            print(render(msgs) or "no messages")
        return 0
    if args.cmd == "list":
        rows = []
        if os.path.exists(MSG_PATH):
            for line in open(MSG_PATH, errors="replace"):
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        if args.json:
            print(json.dumps({"tool_name": "meditate_inbox_list", "success": True,
                              "data": {"count": len(rows), "messages": rows[-30:]},
                              "metadata": {}, "errors": []}, indent=2))
        else:
            for m in rows[-20:]:
                print("  %s  %s -> %-14s %s" % (m.get("ts", "")[11:19], m.get("from", "")[:8],
                                                m.get("to", ""), m.get("body", "")[:70]))
            if not rows:
                print("no messages yet")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
