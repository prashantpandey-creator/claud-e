"""brief — the console speaks first; the tables become an appendix.

A dashboard row like "purangpt 38% · 1023 msgs · 75 chats" is a fact the
reader still has to turn into a thought. This composes the thought itself,
in the register of a junior dev catching you up:

    Two chats are waiting on you — the oldest 31 hours: "lets get back to
    tutor". Most of this month's code went into purangpt (328 commits), but
    your last 23 days of chats were mostly about building me. Everything I
    know still checks out. I'd answer the waiting chats first.

Deterministic, not an LLM call: the page polls every 4 seconds and must
render in milliseconds, and the same world must produce the same words —
an assistant that rephrases identical facts on every poll reads as unstable,
not personable. Reasoning-on-demand stays in advisor.py where you ask for it.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)


def _hours_phrase(h: float) -> str:
    if h < 1.5:
        return "the last hour"
    if h < 42:
        return "%d hours" % round(h)
    return "%d days" % round(h / 24)


def _quote(s: str, n: int = 58) -> str:
    s = " ".join((s or "").split())
    # spoken and skimmed, not clicked — a URL is noise in both registers
    import re as _re
    s = _re.sub(r"https?://\S+", "a link", s)
    return (s[: n - 1] + "…") if len(s) > n else s


def compose(state: Dict[str, Any]) -> List[str]:
    """state -> a few short sentences. Pure function, so tests can feed
    worlds and read exactly what would be said."""
    out: List[str] = []

    # 1. what is OWED comes first — it is the only thing with a person waiting
    tri = state.get("triage") or {}
    items = tri.get("action_items") or []
    replies = [i for i in items if i.get("action") == "reply"]
    resumes = [i for i in items if i.get("action") == "resume"]
    if replies:
        oldest = max(replies, key=lambda i: i.get("age_h", 0))
        lead = ("One chat is waiting on you" if len(replies) == 1
                else "%d chats are waiting on you" % len(replies))
        out.append('%s — the oldest for %s: "%s".'
                   % (lead, _hours_phrase(oldest.get("age_h", 0)),
                      _quote(oldest.get("last_said", ""))))
    if resumes:
        out.append("%d piece%s of work stopped mid-stride and would pick "
                   "straight back up." % (len(resumes),
                                          "" if len(resumes) == 1 else "s"))

    # 2. the attention story — recent chats vs the repos' own history,
    # with the window said out loud so the percentage cannot lie
    projects = state.get("projects") or []
    window = state.get("projects_window_days") or 0
    with_msgs = [p for p in projects if p.get("messages")]
    by_commits = sorted((p for p in projects if p.get("commits_recent")),
                        key=lambda p: -p["commits_recent"])
    if with_msgs and by_commits:
        talk = with_msgs[0]
        build = by_commits[0]
        # a near-tie must not be narrated as dominance: purangpt was "leading"
        # meditate 1023 msgs to 1018 — a 5-message margin
        split = (len(with_msgs) > 1
                 and with_msgs[1]["messages"] >= 0.85 * talk["messages"])
        if split:
            out.append("Your chats are split about evenly between %s and %s; "
                       "the commits this month lean %s (%d)."
                       % (talk["project"], with_msgs[1]["project"],
                          build["project"], build["commits_recent"]))
        elif talk["project"] == build["project"]:
            out.append("Your energy is in one place: %s leads both the "
                       "chats and the commits this month (%d of them)."
                       % (talk["project"], build["commits_recent"]))
        else:
            out.append("Most of this month's code went into %s (%d commits), "
                       "but your last %d days of chats were mostly about %s."
                       % (build["project"], build["commits_recent"],
                          window or 21, talk["project"]))

    # milestones the world already satisfied — the console asking for finished
    # work is the most corrosive thing it can do
    ml = state.get("milestones") or {}
    done_already = ml.get("looks_done") or []
    if done_already:
        out.append("%d milestone%s look%s already done — %s."
                   % (len(done_already), "" if len(done_already) == 1 else "s",
                      "s" if len(done_already) == 1 else "",
                      done_already[0].get("evidence", "worth a look")))

    # 3. the state of what it knows — one clause, only when it moves
    store = state.get("store") or {}
    # repair arrives as a LIST of failed facts from the console's state and as
    # a {"open": n} dict from the CLI path. Reading only one shape put
    # "everything checks out" on screen directly above "1 fact failed
    # reality-check" — the exact disagreement composing from one dict was
    # meant to make impossible.
    repair = state.get("repair") or {}
    if isinstance(repair, list):
        broken = len(repair)
    elif isinstance(repair, dict):
        broken = repair.get("open", 0)
    else:
        broken = 0
    if broken:
        out.append("%d thing%s I know stopped matching reality — worth "
                   "fixing before %s mislead%s anyone."
                   % (broken, "" if broken == 1 else "s",
                      "it" if broken == 1 else "they",
                      "s" if broken == 1 else ""))
    elif store.get("active"):
        pct = 100.0 * store.get("verified", 0) / store["active"]
        if pct >= 99:
            out.append("Everything I know still checks out.")

    # 4. one suggestion, never a menu
    if replies:
        out.append("I'd answer the waiting chats first.")
    elif broken:
        out.append("I'd let the repair pass run.")
    else:
        nxt = (state.get("next") or "").split("(")[0].strip()
        if nxt and "nothing owed" not in nxt:
            out.append("Next when you're ready: %s." % nxt)
        else:
            out.append("Nothing needs you right now.")
    return out


def gather_and_compose() -> List[str]:
    """Assemble the same state the console serves, then speak it."""
    state: Dict[str, Any] = {}
    try:
        import status as st
        d = st.gather()
        state["store"] = d.get("store", {})
        state["next"] = d.get("next", "")
        state["repair"] = {"open": 1 if d.get("repair_open") else 0}
    except Exception:
        pass
    try:
        from triage import triage
        t = triage()
        state["triage"] = {"action_items": t.get("action_items", []),
                           "counts": t.get("counts", {})}
    except Exception:
        pass
    try:
        from projects import rollup, window_days
        state["projects"] = [r for r in rollup() if r.get("messages")
                             or r.get("commits_recent")]
        state["projects_window_days"] = window_days()
    except Exception:
        pass
    return compose(state)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="meditate brief", description="Say the state, don't list it")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    lines = gather_and_compose()
    if a.json:
        print(json.dumps({"tool_name": "meditate_brief", "success": True,
                          "data": {"sentences": lines}, "metadata": {},
                          "errors": []}, indent=2))
    else:
        print(" ".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
