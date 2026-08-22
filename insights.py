"""insights — the patterns the flat lists hide, surfaced in plain words.

Pulse showed correct rows — 8 orbs, 6 goals, a fleet, a repair queue — but
the meaning across them stayed in the reader's head. This computes the
patterns that emerge NATURALLY from that same data:

  - by PROJECT: everything about one project in one place (its live sessions,
    its goals, what its agents are saying) — because that is how the work
    actually clusters
  - a one-line HEADLINE: what is happening right now, busiest first
  - NEEDS YOU vs MOVING ITSELF: the only split that decides your next minute

All derived from the state dict Pulse already builds. No new sources, no
invented numbers. Empty world -> empty patterns, honest headline.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List


def _project_of(cwd: str) -> str:
    """The human name of a working directory — last meaningful path segment."""
    parts = [p for p in str(cwd).rstrip("/").split("/") if p]
    if not parts:
        return "~"
    # skip a bare 'vedic puran' wrapper in favor of a repo inside it
    name = parts[-1]
    return name


def insights(state: Dict[str, Any]) -> Dict[str, Any]:
    live = state.get("live_sessions", []) or []
    goals = state.get("goals", []) or []
    fleet = state.get("fleet", []) or []
    repair = state.get("repair", []) or []

    # cluster by project
    clusters: Dict[str, Dict[str, Any]] = {}
    for s in live:
        proj = _project_of(s.get("cwd", ""))
        c = clusters.setdefault(proj, {"project": proj, "live": 0,
                                       "labels": [], "goals": []})
        c["live"] += 1
        if s.get("label"):
            c["labels"].append(s["label"])
    for g in goals:
        proj = _project_of(g.get("cwd", ""))
        c = clusters.setdefault(proj, {"project": proj, "live": 0,
                                       "labels": [], "goals": []})
        c["goals"].append({"title": g.get("title", g.get("name", "")),
                           "pct": g.get("pct", 0), "next": g.get("next", "")})
    projects = sorted(clusters.values(),
                      key=lambda c: (-c["live"], -len(c["goals"])))

    # headline: busiest first
    if live:
        top = projects[0]
        others = sum(c["live"] for c in projects[1:])
        headline = "%d Claude session%s live — %d on %s%s" % (
            len(live), "s" if len(live) != 1 else "", top["live"], top["project"],
            (", %d elsewhere" % others) if others else "")
    elif goals:
        headline = "quiet — no sessions live; %d goal%s waiting" % (
            len(goals), "s" if len(goals) != 1 else "")
    else:
        headline = "quiet — nothing live, nothing owed"

    # needs you vs moving itself
    needs_you: List[str] = []
    if repair:
        needs_you.append("%d fact%s failed reality-check — repair before trusting them"
                         % (len(repair), "s" if len(repair) != 1 else ""))
    covered = {f.get("goal") for f in fleet if f.get("says") or f.get("live_session")}
    idle_goals = [g for g in goals
                  if g.get("pct", 0) < 100 and g.get("name") not in covered]
    for g in idle_goals[:4]:
        needs_you.append("goal '%s' (%.0f%%) has no agent — next: %s"
                         % (g.get("title", "")[:40], g.get("pct", 0),
                            (g.get("next") or "")[:50]))

    moving: List[str] = []
    for f in fleet:
        if f.get("says"):
            mark = "done" if f.get("says_done") else "…"
            moving.append("%s: %s (%s)" % (f.get("goal", ""), f.get("says", ""), mark))
        elif f.get("milestone_ticked"):
            moving.append("%s: milestone done" % f.get("goal", ""))

    return {"headline": headline, "projects": projects,
            "needs_you": needs_you, "moving": moving}
