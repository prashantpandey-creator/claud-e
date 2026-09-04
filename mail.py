#!/usr/bin/env python3
"""mail.py — reach the owner when he is away, and hear him back.

OUT: one digest per heartbeat pass when something changed (a new item under
YOUR HANDS, a step shipped, a run stopped, a hold, a close-out), and the
run's summary at the deadline. Through ~/bin/sendmail (Gmail SMTP, app
password in ~/.sendmail.conf) — headless, no OAuth, nothing new to keep.

Every mail carries a nonce in its subject: `[claud-e #ab12cd34]`. A reply
that echoes it is the owner's, and the nonce says which items the mail was
about. mail-state.json remembers what was sent so a quiet pass sends nothing.

The recipient defaults to the sendmail account itself — the owner mailing
himself — so there is no address to configure and none to leak.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

MEDITATION_DIR = os.path.expanduser("~/.claude/meditation")
SENDMAIL = os.path.expanduser("~/bin/sendmail")
CONF = os.path.expanduser("~/.sendmail.conf")
STATE_NAME = "mail-state.json"
TAG = "claud-e"


def read_conf(path: str = CONF) -> Dict[str, Any]:
    try:
        d = json.load(open(path))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def configured(sendmail: str = SENDMAIL, conf: str = CONF) -> bool:
    return os.path.exists(sendmail) and bool(read_conf(conf).get("user"))


def owner_address(conf: str = CONF) -> str:
    return str(read_conf(conf).get("user") or "")


def _default_runner(argv: List[str], body: str) -> Tuple[bool, str]:
    try:
        r = subprocess.run(argv, input=body, capture_output=True, text=True, timeout=60)
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()[:200]
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)[:200]


def send(subject: str, body: str, to: str = "", runner: Optional[Callable] = None,
         sendmail: str = SENDMAIL, conf: str = CONF) -> Dict[str, Any]:
    """One mail. Body on stdin (sendmail's own contract). Never raises."""
    runner = runner or _default_runner
    to = to or owner_address(conf)
    if not to:
        return {"sent": False, "why": "no recipient: ~/.sendmail.conf has no user"}
    ok, out = runner([sendmail, to, subject], body)
    return {"sent": bool(ok), "why": "" if ok else out, "to": to, "subject": subject}


def new_nonce() -> str:
    return uuid.uuid4().hex[:8]


def subject_with(nonce: str, text: str) -> str:
    return "[%s #%s] %s" % (TAG, nonce, text)


# ---------------------------------------------------------------------------
# the digest
# ---------------------------------------------------------------------------

def _state_path(meditation_dir: str) -> str:
    return os.path.join(meditation_dir, STATE_NAME)


def load_state(meditation_dir: str = MEDITATION_DIR) -> Dict[str, Any]:
    try:
        d = json.load(open(_state_path(meditation_dir)))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(st: Dict[str, Any], meditation_dir: str = MEDITATION_DIR) -> None:
    os.makedirs(meditation_dir, exist_ok=True)
    tmp = _state_path(meditation_dir) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, _state_path(meditation_dir))


def fingerprint(g: Dict[str, Any]) -> Dict[str, Any]:
    """What a mail is about: the items whose change is worth a mail."""
    nodes = g.get("nodes") or []
    return {
        "hands": sorted(n["id"] for n in nodes if n.get("kind") == "human" and n.get("status") == "waiting"),
        "done": sorted(n["id"] for n in nodes if n.get("status") == "done" and n.get("kind") != "human"
                       and n.get("result")),
        "stopped": sum(1 for e in g.get("events") or [] if e.get("what") == "stopped"),
        "held": bool(float(g.get("hold_until") or 0) > time.time()),
        "armed": bool(g.get("armed")),
        "closed": bool(g.get("summary_at")),
    }


def digest(g: Optional[Dict[str, Any]], last: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The mail for what changed since the last one, or None."""
    if not g:
        return None
    fp = fingerprint(g)
    prev = last.get("fingerprint") or {}
    if prev == fp:
        return None
    nodes = {n["id"]: n for n in g.get("nodes") or []}
    lines: List[str] = []
    items: List[Dict[str, str]] = []
    new_hands = [i for i in fp["hands"] if i not in (prev.get("hands") or [])]
    if new_hands:
        lines.append("YOUR HANDS — new")
        for i in new_hands:
            n = nodes[i]
            lines.append("  - %s" % n["title"][:140])
            items.append({"kind": "human", "id": i, "title": n["title"][:80]})
        lines.append("")
    new_done = [i for i in fp["done"] if i not in (prev.get("done") or [])]
    if new_done:
        lines.append("SHIPPED")
        for i in new_done:
            n = nodes[i]
            r = n.get("result") or {}
            lines.append("  - %s › %s%s" % (n.get("goal_title", "")[:36], n["title"][:80],
                                            (" · commits " + ", ".join(c[:9] for c in r.get("verified_commits", [])[:4]))
                                            if r.get("verified_commits") else ""))
            items.append({"kind": "node", "id": i, "title": n["title"][:80]})
        lines.append("")
    if fp["stopped"] > (prev.get("stopped") or 0):
        stops = [e for e in g.get("events") or [] if e.get("what") == "stopped"][prev.get("stopped") or 0:]
        lines.append("STOPPED")
        for e in stops:
            n = nodes.get(e.get("node", ""), {})
            lines.append("  - %s: %s (attempt %s)" % (n.get("title", e.get("node", ""))[:70], e.get("why", "")[:100], e.get("attempt")))
            if e.get("node") and not any(it["id"] == e["node"] for it in items):
                items.append({"kind": "node", "id": e["node"], "title": n.get("title", "")[:80]})
        lines.append("")
    if fp["held"] and not prev.get("held"):
        lines.append("HOLDING — a usage limit answered; the run resumes by itself in 30 minutes")
        lines.append("")
    if prev.get("armed") and not fp["armed"] and not fp["closed"]:
        lines.append("PAUSED — %s" % (g.get("paused_why") or ""))
        lines.append("")
    if fp["closed"] and not prev.get("closed"):
        lines.append("CLOSED — the summary was mailed separately")
        lines.append("")
    if not lines:
        return None
    m = g.get("metrics") or {}
    lines.append("now: %d of %d done · %d running · $%.2f spent%s"
                 % (m.get("done", 0), m.get("nodes", 0), m.get("running", 0), m.get("spent_usd", 0) or 0,
                    (" · until " + g["until"]) if g.get("until") else ""))
    lines.append("")
    lines.append("Reply to this mail to act: 'done' ticks the first YOUR HANDS item above; "
                 "anything else is sent to the agent as your steer. Open: http://127.0.0.1:7711/twin")
    nonce = new_nonce()
    head = ("your hands: %d new" % len(new_hands)) if new_hands else \
           ("shipped: %d" % len(new_done)) if new_done else \
           ("stopped: %d" % (fp["stopped"] - (prev.get("stopped") or 0))) if fp["stopped"] > (prev.get("stopped") or 0) else \
           "run update"
    return {"subject": subject_with(nonce, "CLAUD-E — " + head), "body": "\n".join(lines),
            "nonce": nonce, "items": items, "fingerprint": fp}


def send_digest(meditation_dir: str = MEDITATION_DIR, runner: Optional[Callable] = None,
                campaign_state: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """The heartbeat's step. Loads the campaign, mails what changed, remembers it."""
    g = campaign_state
    if g is None:
        try:
            import campaign as cp
            g = cp.load(meditation_dir)
        except Exception:
            g = None
    st = load_state(meditation_dir)
    d = digest(g, st)
    if not d:
        return {"sent": False, "why": "nothing changed"}
    r = send(d["subject"], d["body"], runner=runner)
    if r.get("sent"):
        st["fingerprint"] = d["fingerprint"]
        st.setdefault("sent", {})[d["nonce"]] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now or time.time())),
                                                  "items": d["items"], "subject": d["subject"]}
        st["last_sent"] = st["sent"][d["nonce"]]["ts"]
        save_state(st, meditation_dir)
    return {"sent": bool(r.get("sent")), "why": r.get("why", ""), "subject": d["subject"], "nonce": d["nonce"]}


def send_summary(subject: str, text: str, runner: Optional[Callable] = None,
                 meditation_dir: str = MEDITATION_DIR) -> Dict[str, Any]:
    nonce = new_nonce()
    r = send(subject_with(nonce, subject), text, runner=runner)
    if r.get("sent"):
        st = load_state(meditation_dir)
        st.setdefault("sent", {})[nonce] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                                            "items": [], "subject": subject, "summary": True}
        st["last_sent"] = st["sent"][nonce]["ts"]
        save_state(st, meditation_dir)
    return r


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate mail", description=__doc__.split("\n")[0])
    ap.add_argument("--digest", action="store_true", help="mail what changed in the run since the last mail")
    ap.add_argument("--test", action="store_true", help="send one line to prove the lane")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not configured():
        if not a.quiet:
            print("mail is not configured: needs ~/bin/sendmail and a user in ~/.sendmail.conf")
        return 0
    if a.test:
        r = send(subject_with(new_nonce(), "CLAUD-E — the mail lane is live"),
                 "This is the twin. If you are reading this, mail out works.\n")
        print(json.dumps(r) if a.json else ("sent to %s" % r.get("to") if r.get("sent") else "not sent: " + r.get("why", "")))
        return 0 if r.get("sent") else 1
    if a.digest:
        r = send_digest()
        if a.json:
            print(json.dumps(r))
        elif not a.quiet or r.get("sent"):
            print(("mailed: " + r["subject"]) if r.get("sent") else ("no mail: " + r.get("why", "")))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
