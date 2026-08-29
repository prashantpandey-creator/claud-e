"""status — where am I, and the ONE next action. Bare `meditate` runs this.

One screen: store health, goals, queues, fleet, heartbeat — ending with a
single decided `next:` line. Never a menu. Priority of the decision:

  1. repair queue open      -> fix knowledge first (a fleet on drifted
                               facts builds wrong)
  2. dispatchable goals     -> meditate go
  3. stilling overdue       -> /meditate
  4. otherwise              -> still; nothing owed

Plumbing (grade, drift, ask, archive, distill, goals, report, who, drive,
dashboard, sessions, launch, doctor) still exists under `meditate help`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

MEDITATION_DIR = os.path.expanduser("~/.claude/meditation")
STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")
STILL_MAX_DAYS = 3


def gather(meditation_dir: str = MEDITATION_DIR, store_dir: str = STORE_DIR,
           goals_dir: Optional[str] = None, history_path: Optional[str] = None,
           ledger_path: Optional[str] = None) -> Dict[str, Any]:
    import drive as dv
    import goals as gl
    from ask import _load

    d: Dict[str, Any] = {}
    mems = [m for m in _load(store_dir) if m.get("active")]
    d["store"] = {
        "active": len(mems),
        "verified": sum(1 for m in mems
                        if m["epistemic"]["evidence_status"] == "machine_checked"),
        "formed": sum(1 for m in mems if "commit-fact" in m.get("tags", [])),
    }
    kw = {}
    if goals_dir:
        kw["goals_dir"] = goals_dir
    if history_path:
        kw["history_path"] = history_path
    d["goals"] = gl.scan(**kw)
    d["dispatchable"] = dv.dispatchable(goals_dir, ledger_path or dv.LEDGER_PATH,
                                        history_path)
    d["cooling"] = getattr(dv.dispatchable, "cooling", 0)
    d["repair_open"] = os.path.exists(os.path.join(meditation_dir, "repair-queue.md"))
    hb = os.path.join(meditation_dir, "heartbeat.log")
    d["heartbeat_h"] = round((time.time() - os.path.getmtime(hb)) / 3600, 1) \
        if os.path.exists(hb) else None
    still = os.path.join(meditation_dir, "STILLNESS.md")
    d["still_days"] = round((time.time() - os.path.getmtime(still)) / 86400, 1) \
        if os.path.exists(still) else None

    # the ONE decision
    if d["repair_open"]:
        d["next"] = "meditate go  (repair queue open — fix knowledge before new work)"
    elif d["dispatchable"]:
        d["next"] = "meditate go  (%d goal agent(s) ready to launch)" % len(d["dispatchable"])
    elif d["still_days"] is None or d["still_days"] > STILL_MAX_DAYS:
        d["next"] = "/meditate  (stilling pass overdue)"
    else:
        d["next"] = "nothing owed — the world is graded and the fleet is out"
    return d


def _fit(text: str, width: int) -> str:
    """Cut at a word, or do not cut at all.

    The front page sliced every goal title at [:26] and every next-step at
    [:66], so all six titles on this machine came out chopped mid-word:
    "Meditate closes its own lo", "Production stable — paymen",
    "Astrology readings instant". That is the first thing anyone reads, and a
    title cut mid-word is not shortened — it is unreadable.

    Two rules: cut on a space, and say so with an ellipsis. A cut with no
    mark is indistinguishable from a title that really ends there.
    """
    t = (text or "").strip()
    if len(t) <= width:
        return t
    cut = t[:width - 1]
    sp = cut.rfind(" ")
    if sp > width * 0.55:          # only if a word boundary is actually near
        cut = cut[:sp]
    return cut.rstrip(" ,;:—-") + "…"


def _dormant_from_server(timeout_s: float = 0.4) -> List[Dict[str, Any]]:
    """What you started and left — from the running server, or nothing.

    Deliberately NOT computed here. projects.revival_cards() walks 42 repos
    and shells out to git per repo; wiring it straight into this page took it
    from 0.0s to 10.0s, measured. The front page is the one screen that has
    to answer instantly, and dormancy is the least urgent thing on it.

    The server holds it warm on an hour's cache, so this is one local request
    with a short fuse. If nothing answers, the line is simply absent — the
    same rule the rest of the tool follows about a question it cannot cheaply
    answer.
    """
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:7711/api/state",
                                     headers={"X-Meditate": "1"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return json.load(r).get("dormant") or []
    except Exception:
        return []


def _headline_safe(text: str) -> str:
    """goals._headline, or the text unchanged if goals cannot be imported.
    Two callers here want it and neither should fail over a missing import."""
    try:
        from goals import _headline
        return _headline(text)
    except Exception:
        return text or ""


def _term_width(default: int = 80) -> int:
    import shutil
    try:
        w = shutil.get_terminal_size((default, 24)).columns
    except Exception:
        return default
    # a piped or zero-width terminal reports nonsense; 80 is the safe floor
    return w if 40 <= w <= 200 else default


def status_text(**kw) -> str:
    """One screen a stranger can read. No internal vocabulary, and the thing
    that needs you is the thing that stands out."""
    d = gather(**kw)
    s = d["store"]
    # ANSI: bold for what needs you, dim for context. Plain text if piped.
    tty = sys.stdout.isatty()
    B = "\033[1;33m" if tty else ""      # attention
    G = "\033[0;32m" if tty else ""      # all good
    D = "\033[2m" if tty else ""         # quiet detail
    R = "\033[0m" if tty else ""

    lines: List[str] = []
    pct = 100.0 * s["verified"] / s["active"] if s["active"] else 0.0
    lines.append("I know %d things about your work. %.0f%% still check out. "
                 "%d I picked up on my own."
                 % (s["active"], pct, s["formed"]))
    if d["heartbeat_h"] is not None:
        lines.append("%sLast self-check %.0f h ago.%s" % (D, d["heartbeat_h"], R))
    lines.append("")

    if d["goals"]:
        # _headline drops the file-notation tail a goal title carries (the
        # em-dash and everything after it), which is what made four of the
        # six titles here too long in the first place. The column is then
        # sized from the TERMINAL and the titles it actually has to hold, so
        # on any normal window nothing is cut at all.
        lines.append("%sWhat you're working on%s" % (D, R))
        _headline = _headline_safe
        term = _term_width()
        titles = [_headline(g["title"]) for g in d["goals"]]
        room = max(18, term - len("   99 of 99 done  (grew by 9)"))
        col = min(room, max((len(t) for t in titles), default=18))
        for g, title in zip(d["goals"], titles):
            grown = "  (grew by %d)" % g["scope_delta"] if g.get("scope_delta", 0) > 0 else ""
            lines.append("  %-*s %d of %d done%s" %
                         (col, _fit(title, col), g["done"], g["total"], grown))
            if g["next"]:
                lines.append("      %snext:%s %s"
                             % (D, R, _fit(_headline(g["next"]), term - 13)))
        lines.append("")

    if d["repair_open"]:
        # Name the IDEA, not the count. "Some of what I know stopped matching
        # reality" is a mood — it gives a person nothing to decide with, and
        # voice.py already solved this exact problem for the spoken briefing
        # (_idea_of_broken: say which thing, in the owner's own words). The
        # front page was still printing the mood.
        said = None
        try:
            import voice as _v
            it = _v._idea_of_broken(kw.get("store_dir") or STORE_DIR)
            if it and it.get("idea"):
                more = ""
                try:
                    n = int(it["n"])
                    if n > 1:
                        more = "  (%d like it)" % n
                except (ValueError, TypeError, KeyError):
                    pass
                said = "%s! You told me: %s — not true anymore.%s%s" % (
                    B, _fit(_headline_safe(it["idea"]), _term_width() - 34), R, more)
        except Exception:
            said = None
        lines.append(said or
                     "%s! Some of what I know stopped matching reality.%s" % (B, R))
    if d["cooling"]:
        lines.append("%s%d goal(s) already have someone working on them.%s"
                     % (D, d["cooling"], R))

    # What you started and left. Dim, one line, below the live work — never
    # urgent, and until now shown on no screen a person actually opens.
    #
    # ASKED OF THE SERVER, never computed here. Calling revival_cards()
    # directly walks 42 repos and shells out per repo: it took the front page
    # from 0.0s to 10.0s, measured, which is a nicety charging ten seconds on
    # the one screen that has to be instant. The server already holds this
    # warm on an hour's cache. No server, no line — dormancy is worth exactly
    # nothing if it costs the page.
    _names = ", ".join(c.get("project", "") for c in _dormant_from_server()[:3])
    if _names:
        lines.append("%sAlso sitting: %s%s   (meditate projects --revive)"
                     % (D, _fit(_names, _term_width() - 34), R))

    nxt = d["next"]
    if "nothing owed" in nxt:
        lines.append("%sNothing needs you. Everything checks out and the work "
                     "is moving.%s" % (G, R))
    else:
        # strip the internal parenthetical, keep the human reason
        cmd = nxt.split("(")[0].strip()
        why = nxt.split("(")[1].rstrip(")").strip() if "(" in nxt else ""
        why = (why.replace("repair queue open — fix knowledge before new work",
                           "some of what I know needs checking first")
                  .replace("stilling pass overdue",
                           "it's been a while since we cleared the decks"))
        why = re.sub(r"(\d+) goal agent\(s\) ready to launch",
                     r"\1 piece(s) of work ready to start", why)
        lines.append("%s→ %s%s%s" % (B, cmd, R, ("   " + D + why + R) if why else ""))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate", description="Status + the one next action")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.json:
        d = gather()
        d["goals"] = [{"name": g["name"], "pct": g["pct"], "done": g["done"],
                       "total": g["total"], "next": g["next"]} for g in d["goals"]]
        d["dispatchable"] = [g["name"] for g in d["dispatchable"]]
        print(json.dumps({"tool_name": "meditate_status", "success": True,
                          "data": d, "metadata": {"store_dir": STORE_DIR},
                          "errors": []}, indent=2))
        return 0
    print(status_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
