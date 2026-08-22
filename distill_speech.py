"""distill_speech — turn project data into something worth SAYING.

The difference this module exists for:

    readout      "mila: 25% done. Next is resubmit to Apple."
    distillation "Mila hasn't moved in 15 days — it's waiting on Apple, and
                  that's the longest anything of yours has sat."

A readout hands you fields and makes you do the thinking. Distillation does
the thinking and hands you the consequence. The rules, in order of what
actually matters to a person:

  1. STUCK beats PROGRESS      — how long it has sat is the news, not the %
  2. CHANGE beats STATE        — scope grew / a milestone just landed
  3. COST beats COUNT          — "23 facts you can no longer trust", not "23"
  4. COMPARISON beats ABSOLUTE — across projects, imbalance IS the insight
  5. ONE number, at most       — this is speech, not a table

Everything here is deterministic phrasing over data that already exists.
No LLM: a sentence generated from measured fields can be checked; a
generated opinion cannot.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

STALE_DAYS = 7.0        # past this, "how long" is the headline
FRESH_DAYS = 2.0


def _spoken_task(task: str) -> str:
    """Milestones are written for a checklist, not a mouth. Strip the
    parenthetical tooling notes and status shouts that a person would never
    say aloud: '(appstore skill; submission WAITING_FOR_REVIEW)'."""
    import re
    t = re.sub(r"\([^)]*\)", "", task or "")           # drop parentheticals
    t = re.sub(r"\b[A-Z][A-Z_]{3,}\b", "", t)          # drop SCREAMING tokens
    t = re.sub(r"\s+", " ", t).strip(" ;,-.")
    return t


def _days(n: Optional[float]) -> str:
    if n is None:
        return "a while"
    if n < 1:
        return "today"
    if n < 2:
        return "yesterday"
    if n < 14:
        return "%d days" % round(n)
    if n < 60:
        return "%d weeks" % round(n / 7)
    return "%d months" % round(n / 30)


def distill_project(p: Dict[str, Any]) -> str:
    """One sentence about ONE project: the thing that matters, not the fields."""
    name = p.get("project", "it")
    idle = p.get("last_touched_days")
    task = ""
    if p.get("open_tasks"):
        task = _spoken_task(p["open_tasks"][0].get("task") or "")[:70]
    pct = p.get("pct")
    rot = int(p.get("repair_items") or 0)
    grew = int(p.get("scope_delta") or 0)

    # 1. stuck — duration leads
    if idle is not None and idle >= STALE_DAYS:
        s = "%s hasn't moved in %s" % (name.capitalize(), _days(idle))
        if task:
            s += " — it's waiting on %s" % task.rstrip(".")
        return s + "."

    # 2. change — scope widened
    if grew > 0:
        s = ("%s grew by %d milestone%s, so the percentage dropped on purpose"
             % (name.capitalize(), grew, "s" if grew != 1 else ""))
        if task:
            s += "; next is %s" % task.rstrip(".")
        return s + "."

    # 3. cost — knowledge rot as consequence
    if rot:
        s = ("%d thing%s I knew about %s no longer check out, so I've stopped "
             "trusting them" % (rot, "s" if rot != 1 else "", name))
        if task:
            s += "; next is %s" % task.rstrip(".")
        return s + "."

    # 4. moving — the task is the news, the number is context
    if task:
        lead = "%s is on %s" % (name.capitalize(), task.rstrip("."))
        if pct is not None and pct > 0:
            lead += " (%.0f%% of the way)" % pct
        if idle is not None and idle < FRESH_DAYS:
            lead += ", worked %s" % _days(idle)
        return lead + "."

    # 5. nothing tracked
    if pct is not None and pct >= 100:
        return "%s is finished — every milestone is ticked." % name.capitalize()
    return ("%s has no goal tracked yet, so I can't tell you if it's moving."
            % name.capitalize())


def distill_portfolio(rows: List[Dict[str, Any]]) -> str:
    """One sentence across ALL projects: the imbalance is the insight."""
    live = [r for r in rows if (r.get("messages") or 0) or r.get("goals")]
    if not live:
        return "Nothing tracked yet — no projects, no goals, nothing to report."

    total = sum(r.get("messages") or 0 for r in live) or 1
    top = max(live, key=lambda r: r.get("messages") or 0)
    share = 100.0 * (top.get("messages") or 0) / total

    # the neglected: has goals or history but sat longest
    stale = [r for r in live
             if r is not top and (r.get("last_touched_days") or 0) >= STALE_DAYS]
    stale.sort(key=lambda r: -(r.get("last_touched_days") or 0))

    rot = sum(int(r.get("repair_items") or 0) for r in live)

    # all quiet
    if share < 60 and not stale and not rot:
        return "Everything's moving and nothing's stale — no imbalance to report."

    parts = []
    if share >= 60:
        parts.append("%s took %.0f%% of your attention"
                     % (top["project"], share))
    if stale:
        s = stale[0]
        parts.append("%s hasn't moved in %s"
                     % (s["project"], _days(s.get("last_touched_days"))))
    if rot:
        parts.append("%d fact%s across your work stopped checking out"
                     % (rot, "s" if rot != 1 else ""))
    return "; ".join(parts).capitalize() + "."
