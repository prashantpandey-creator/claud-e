"""swarm — a dispatch plan that re-derives itself from what the record says.

Not a scheduler and not a learning layer. It reads the open work, asks the
dispatch policy who should take each piece, and projects the token spend from
rates MEASURED on this machine. Every run re-derives, so as the ledger fills
the plan changes on its own — that is the whole of "self-adjusting" here, and
it needs no state of its own to be true.

THE COST MODEL IS TOKENS, NOT MONEY. No price table exists on this machine
and prices are an external fact this tool must not guess. Tokens are measured
here and certain; money is optional and comes from a file the owner controls
(~/.claude/meditation/pricing.json, {"model": usd_per_million_output}). With
no such file the plan is priced in tokens and says so.

THE ONE MEASURED LAW IT PLANS AROUND. Cost per turn RISES with session
length, because every turn re-reads the accumulated context:

    28-turn session      57K cache-read tokens per turn
    386-turn session    196K per turn
    5,836-turn session  376K per turn

Same work split into short agents instead of one long one: 334M against
2,196M, 6.6x cheaper. So the plan prefers MANY SHORT AGENTS over one long
one — not because small is elegant, but because the curve was measured.

WHAT IT REFUSES. It does not rank models by quality: error share is
confounded by task difficulty, which is recorded nowhere. Who takes a piece
of work comes from models.pick, which states whether its reason is evidence
or a default. A projection is labelled a projection.

    meditate swarm            # the plan
    meditate swarm --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

PRICING = os.path.expanduser("~/.claude/meditation/pricing.json")

# Measured 2026-08-29 across 40 transcripts: per-turn cost climbs with the
# length of the session it is in. These are the observed anchors, used only to
# say WHY short agents are preferred — never to fabricate a per-turn number
# for a model that has one measured.
_LENGTH_CURVE = [(28, 57_000), (386, 196_000), (5836, 376_000)]


def prices() -> Dict[str, float]:
    """USD per million output tokens, if the owner has written any down."""
    try:
        with open(PRICING) as f:
            d = json.load(f)
        return {k: float(v) for k, v in d.items()}
    except (OSError, ValueError, TypeError):
        return {}


def _rates() -> Dict[str, Dict[str, int]]:
    """Measured tokens per turn, per model, from this machine's own record."""
    try:
        import models as _md
        return {r["model"]: {"out": r["out_per_turn"],
                             "think": r["think_per_turn"],
                             "turns": r["turns"],
                             "leash": r["leash"] or 8}
                for r in _md.scan(limit=40)["models"]}
    except Exception:
        return {}


def _match(rates: Dict[str, Dict[str, int]], alias: str) -> Optional[str]:
    """`opus` -> the measured claude-opus-5 row. The policy speaks in CLI
    aliases and the record speaks in full ids; without this join every
    projection silently falls back to a default and the numbers stop being
    measurements."""
    alias = (alias or "").lower()
    exact = [m for m in rates if m.lower() == alias]
    if exact:
        return exact[0]
    # Tie-break on TURNS, not on tokens-per-turn. The first cut sorted by
    # output size, so "opus" resolved to whichever opus was most VERBOSE
    # (4-8 at 1,927/turn) rather than the one actually doing the work
    # (opus-5, 15,424 turns against 2,222). Most-used is the representative
    # rate; most-verbose is an accident of what it was asked to do.
    hits = sorted((m for m in rates if alias and alias in m.lower()),
                  key=lambda m: -rates[m].get("turns", 0))
    return hits[0] if hits else None


def open_work() -> List[Dict[str, Any]]:
    """Every piece of work the fleet could take right now, with its kind."""
    out: List[Dict[str, Any]] = []
    try:
        import status as st
        d = st.gather()
        if d.get("repair_open"):
            out.append({"kind": "repair", "what": "the repair queue",
                        "why": "knowledge that failed its own check"})
        for g in (d.get("dispatchable") or []):
            out.append({"kind": "goal",
                        "what": g.get("title") or g.get("name") or "a goal",
                        "why": (g.get("next") or "the open milestone")[:80],
                        "name": g.get("name", "")})
    except Exception:
        pass
    try:
        import go
        for t in go.thread_work():
            out.append({"kind": "thread", "what": t.get("title", "a thread"),
                        "why": "a live continuation chat"})
    except Exception:
        pass
    try:
        import projects as pj
        for c in pj.revival_cards(limit=3):
            out.append({"kind": "revive", "what": c["project"],
                        "why": "untouched %s" % c.get("idle", "a while"),
                        "name": c["project"]})
    except Exception:
        pass
    return out


def plan(max_agents: int = 6) -> Dict[str, Any]:
    """Who takes what, at what effort, and what it is projected to cost."""
    rates = _rates()
    px = prices()
    items = open_work()
    # Broken knowledge first, then goals: everything read downstream of a
    # failed fact is suspect. Same leverage order as the tree and the
    # briefing — one order for the whole tool, not three.
    rank = {"repair": 0, "goal": 1, "thread": 2, "revive": 3}
    items.sort(key=lambda i: rank.get(i["kind"], 9))

    agents: List[Dict[str, Any]] = []
    total_out = 0
    priced = 0.0
    unmeasured = 0
    for it in items[:max_agents]:
        try:
            import models as _md
            pick = _md.pick(it["kind"])
        except Exception:
            pick = {"model": "sonnet", "effort": "high", "basis": "fallback",
                    "why": "policy unreadable"}
        full = _match(rates, pick["model"])
        r = rates.get(full or "", {})
        # A short agent by construction: the measured curve says per-turn cost
        # climbs with session length, so the plan buys turns in small blocks.
        turns = min(r.get("leash", 8) or 8, 25)
        out_tok = (r.get("out", 0) + r.get("think", 0)) * turns
        if not r:
            unmeasured += 1
        total_out += out_tok
        cost = None
        if full and full in px:
            cost = px[full] * out_tok / 1_000_000
            priced += cost
        agents.append({**it, "model": pick["model"], "measured_as": full,
                       "effort": pick["effort"], "basis": pick["basis"],
                       "policy_why": pick["why"], "turns": turns,
                       "out_tokens": out_tok, "usd": cost})
    return {"agents": agents, "queued": len(items),
            "projected_out_tokens": total_out,
            "projected_usd": round(priced, 2) if px else None,
            "unmeasured_models": unmeasured,
            "priced": bool(px)}


def render(d: Optional[Dict[str, Any]] = None) -> str:
    d = d or plan()
    out = ["SWARM PLAN — re-derived from the record on every run", "=" * 64]
    if not d["agents"]:
        out.append("  nothing open to dispatch.")
        return "\n".join(out)
    for a in d["agents"]:
        out.append("")
        out.append("  %-8s %s" % (a["kind"], a["what"][:60]))
        out.append("           %s" % a["why"][:70])
        out.append("           send %s at %s effort [%s] · ~%d turns, ~%s out-tokens%s"
                   % (a["model"], a["effort"] or "default", a["basis"],
                      a["turns"], format(a["out_tokens"], ","),
                      "" if a["usd"] is None else " (~$%.2f)" % a["usd"]))
        if a["measured_as"] is None:
            out.append("           no measured rate for this model — the turn "
                       "count is a floor, not a projection")
    out.append("")
    out.append("  %d queued, %d planned · projected ~%s output tokens%s"
               % (d["queued"], len(d["agents"]),
                  format(d["projected_out_tokens"], ","),
                  "" if d["projected_usd"] is None
                  else " ≈ $%.2f" % d["projected_usd"]))
    if not d["priced"]:
        out.append("  priced in TOKENS: no price table on this machine, and")
        out.append("  prices are an external fact this tool will not guess.")
        out.append("  Write ~/.claude/meditation/pricing.json to get money.")
    out.append("")
    out.append("  SHORT AGENTS ON PURPOSE. Measured: per-turn cost climbs with")
    out.append("  session length — 57K cache-read tokens a turn at 28 turns,")
    out.append("  376K at 5,836. The same work split short cost 334M against")
    out.append("  2,196M, 6.6x cheaper. Every block above is capped at a")
    out.append("  model's own measured leash.")
    out.append("")
    out.append("  Each 'send X' comes from the dispatch policy, which states")
    out.append("  whether its reason is EVIDENCE or a DEFAULT. It never ranks")
    out.append("  models by error share — that number is confounded by task")
    out.append("  difficulty, which is recorded nowhere.")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate swarm",
                                 description="a dispatch plan, re-derived each run")
    ap.add_argument("--agents", type=int, default=6)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    d = plan(a.agents)
    if a.json:
        print(json.dumps({"tool_name": "meditate_swarm", "success": True,
                          "data": d, "metadata": {}, "errors": []}, indent=2))
        return 0
    print(render(d))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
