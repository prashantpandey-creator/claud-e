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

# How many turns one dispatched agent gets. Stated, not derived: the curve
# above says short is cheap, and nothing on this machine has yet measured how
# long a headless agent actually runs.
BLOCK_TURNS = 12


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
    """Ask the DISPATCHER what it would send. Do not re-discover it.

    The first cut walked status/goals/projects itself and built a parallel
    list — and it disagreed with reality within the hour: go.py's dry run
    returned `would: 0` while this planned six agents, because go applies
    gates this copy never knew about (cooldown after a recent dispatch,
    someone already working in that repo, the presence gate). A plan that
    lists work the dispatcher will refuse is fiction with a cost estimate
    attached.

    One discovery, one set of gates. This module's job is the layer go does
    NOT have: who to send, at what effort, and what it is projected to cost.
    """
    out: List[Dict[str, Any]] = []
    try:
        import go
        res = go.run(0)          # dry run: plan only, launch nothing
        data = res.get("data", res) if isinstance(res, dict) else {}
        for line in (data.get("would") or []):
            kind, _, rest = str(line).partition(":")
            kind = kind.strip().lower()
            if kind not in ("repair", "goal", "thread"):
                kind = "goal"
            # Carry the GOAL NAME, not just the prose. go.py's dry-run line
            # reads "goal: <name> -> <milestone>", and the first cut kept only
            # the sentence — so the console's run button dispatched with an
            # EMPTY argument, which means the whole fleet rather than the one
            # goal it was pointing at. A button that does something broader
            # than its label is the Casper fall-through again.
            rest = rest.strip()
            name, sep, milestone = rest.partition("->")
            # `what` is the THING, `why` is the reason — never both in both.
            # Keeping the whole "name -> milestone" string in `what` printed
            # the milestone twice in the console, once in the title and again
            # in the line under it.
            out.append({"kind": kind,
                        "what": (name.strip() if sep else rest[:70]) or line,
                        "name": name.strip() if sep else "",
                        "why": ("next: " + milestone.strip()[:70]) if sep
                               else "queued by the dispatcher"})
        out.append({"_gates": {"cooling": data.get("cooling", 0)}})
    except Exception as e:
        out.append({"kind": "unknown", "what": "could not ask the dispatcher",
                    "why": str(e)[:60]})
    gates = {}
    if out and "_gates" in out[-1]:
        gates = out.pop()["_gates"]
    # Dormant repos are NOT in go's queue — reviving one is the owner's call,
    # never automatic — so they are offered separately and marked as such.
    try:
        import projects as pj
        for c in pj.revival_cards(limit=3):
            out.append({"kind": "revive", "what": c["project"],
                        "why": "untouched %s · not queued, needs your go"
                               % c.get("idle", "a while"), "offer": True})
    except Exception:
        pass
    if gates.get("cooling"):
        out.append({"kind": "note", "what": "%d goal(s) cooling" % gates["cooling"],
                    "why": "recently dispatched — the dispatcher is holding them",
                    "offer": True})
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
    notes = [i for i in items if i.get("kind") == "note"]
    items = [i for i in items if i.get("kind") != "note"]
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
        # A CAP, not a prediction. The tempting number was the model's
        # measured leash — but that is how long the OWNER let it run in an
        # interactive session, which says nothing about a headless agent
        # working alone. Borrowing it would dress an unrelated measurement as
        # a forecast. Dispatched agents have no measured turn counts yet (0
        # recorded runs), so this is a stated block size, and the projection
        # below is a CEILING for that block.
        # The turn ceiling is gone. It was never enforced — the plan said
        # "up to 12 turns" and a measured agent ran 14 — and it modelled the
        # wrong quantity anyway: the three real runs spent 4,955 output tokens
        # against 618,402 cache-READ, so projecting output alone described
        # about 1% of the volume. The cap is dollars now, enforced by the CLI,
        # set from what the same kind of task actually cost.
        try:
            import models as _md2
            cap = _md2.budget_for(it["kind"])
        except Exception:
            cap = {"usd": 2.00, "basis": "default", "why": "policy unreadable"}
        turns = BLOCK_TURNS
        out_tok = (r.get("out", 0) + r.get("think", 0)) * turns
        if not r:
            unmeasured += 1
        total_out += out_tok
        cost = None
        if full and full in px:
            cost = px[full] * out_tok / 1_000_000
            priced += cost
        agents.append({**it, "cap_usd": cap["usd"], "cap_basis": cap["basis"],
                       "cap_why": cap["why"],
                       "model": pick["model"], "measured_as": full,
                       "effort": pick["effort"], "basis": pick["basis"],
                       "policy_why": pick["why"], "turns": turns,
                       "out_tokens": out_tok, "usd": cost})
    return {"agents": agents, "queued": len(items), "notes": notes,
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
        out.append("           send %s at %s effort [%s] · capped at $%.2f [%s]"
                   % (a["model"], a["effort"] or "default", a["basis"],
                      a["cap_usd"], a["cap_basis"]))
        if a["measured_as"] is None:
            out.append("           no measured rate for this model — the turn "
                       "count is a floor, not a projection")
    for n in d.get("notes") or []:
        out.append("")
        out.append("  note     %s — %s" % (n["what"], n["why"]))
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
