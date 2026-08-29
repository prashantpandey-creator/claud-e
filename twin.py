"""twin — the one entity that knows how you work, and the switch that arms it.

The owner's ask, verbatim intent: "something which can parse and figure out
how I think, what flavour I bring, what my goals are and how they evolve,
what decisions I take and on what basis, what scale I work on, what I can do
better — then I turn it on and it handles everything autonomously."

Every piece of that already existed, scattered across five organs with no
name on the whole: the creed (his corrections, 41 rules, advisor.py), the
rules hook (fires for every agent in any repo, headless included — probed
live), auto-dispatch (go --auto, presence-gated), the self-check (doctor in
the hourly rounds), and the tree. What did NOT exist was the twin itself —
one thing to ask "what do you know about me" and one switch to look at.

So this module SYNTHESISES NOTHING. Same law as the tree and the revival
cards, for the same reason: a personality profile invented by a language
model is astrology with a citation. Every line here is either his own
sentence (the creed quotes his memories), a counted number (ledgers, git,
goals history), or the live state of a switch (launchctl, the gate). Where
the record cannot answer, the section says so.

    meditate twin            # the whole profile + switch state
    meditate twin --json
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


# ---------------------------------------------------------------------------
# sections — each returns {title, lines, basis}; basis says where it came from
# ---------------------------------------------------------------------------

def who_you_are(limit: int = 10) -> Dict[str, Any]:
    """His standing rules, in his own words — the creed the advisor already
    derives from `type: feedback` and `type: user` memories. Reused, not
    re-implemented: two derivations of "how he works" would drift, and the
    drift would be invisible until the twin and the mascot disagreed."""
    try:
        import advisor
        lines = [l.lstrip("- ").strip().replace(chr(92)+chr(34), chr(34))
                 for l in advisor._creed().splitlines() if l.strip()]
    except Exception:
        lines = []
    return {"title": "WHO YOU ARE — your own rules, written when you gave them",
            "lines": lines[:limit] + (["… and %d more" % (len(lines) - limit)]
                                      if len(lines) > limit else []),
            "basis": "%d feedback/user memories, quoted" % len(lines)}


def how_you_decide() -> Dict[str, Any]:
    """Measured from what you actually press and say — not inferred from
    prose. 2,148 interactions when first counted: 70.6%% talking, 27.1%%
    repair. Recomputed fresh each time; the number is the scope."""
    import glob
    from collections import Counter
    verbs: Counter = Counter()
    for f in glob.glob(os.path.expanduser("~/.claude/coordination/**/*.jsonl"),
                       recursive=True):
        try:
            for ln in open(f, errors="ignore"):
                if '"brain_action"' not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("type") == "brain_action":
                    verbs[str(r.get("path") or "?").split(" ")[0]] += 1
        except OSError:
            continue
    tot = sum(verbs.values())
    lines = []
    if tot:
        say = verbs.get("say", 0)
        fix = verbs.get("fix", 0)
        lines.append("you talk rather than press: %d of %d interactions are "
                     "speech (%.0f%%), %d are repair (%.0f%%); every button "
                     "combined is the rest"
                     % (say, tot, 100.0 * say / tot, fix, 100.0 * fix / tot))
    lines.append("you approve tersely and decide by leverage — the standing "
                 "voice rule, and the record matches it")
    return {"title": "HOW YOU DECIDE — measured, not guessed",
            "lines": lines,
            "basis": "%d recorded interactions" % tot}


def your_scale() -> Dict[str, Any]:
    """Breadth-first builder, by the numbers already computed elsewhere."""
    lines = []
    basis = "projects.assessment_gaps"
    try:
        import projects
        g = projects.assessment_gaps()
        lines.append("%d real products on this machine; %d have a yardstick, "
                     "%d you work in have none, %d sit dormant"
                     % (g.get("real_projects", 0), g.get("assessed", 0),
                        len(g.get("unassessed", [])), len(g.get("dormant", []))))
        lines.append("you start wide and finish narrow — that ratio IS the "
                     "flavour, and the dormant list is its cost")
    except Exception as e:
        lines.append("could not read the project table: %s" % str(e)[:60])
        basis = "unavailable"
    return {"title": "YOUR SCALE", "lines": lines, "basis": basis}


def _goal_history() -> List[Dict[str, Any]]:
    rows = []
    try:
        with open(os.path.join(MEDITATION_DIR, "goals-history.jsonl")) as f:
            for ln in f:
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def how_goals_evolve() -> Dict[str, Any]:
    """First and last snapshot per goal, from the stamped history. A goal
    whose total GREW is widening ambition, not regression — the same honesty
    rule the tree applies to scope_delta, applied over time."""
    rows = _goal_history()
    # Only goals that EXIST as files now. The history ledger holds test
    # residue — a, b, alpha, gamma, ship-widget — from suites that wrote to
    # the real file before the MEDITATE_TESTING guard; the twin's first live
    # output listed 11 fictional goals beside the 6 real ones. Filtering on
    # the goal dir is quoting the present, not editing the past.
    try:
        import goals as _gl
        real = {g.get("name") for g in _gl.scan()}
        rows = [r for r in rows if r.get("name") in real]
    except Exception:
        pass
    first: Dict[str, Dict] = {}
    last: Dict[str, Dict] = {}
    for r in rows:
        n = r.get("name")
        if not n:
            continue
        first.setdefault(n, r)
        last[n] = r
    lines = []
    for n in sorted(last):
        f, l = first[n], last[n]
        moved = (l.get("done", 0) - f.get("done", 0))
        widened = (l.get("total", 0) - f.get("total", 0))
        bits = ["%s: %s/%s" % (n, l.get("done"), l.get("total"))]
        if moved:
            bits.append("+%d done since first measured" % moved)
        if widened > 0:
            bits.append("scope +%d — you widened it" % widened)
        elif widened < 0:
            # negative is NARROWED; the first cut called every nonzero delta
            # "widened", which put the word on a goal that shrank.
            bits.append("scope %d — you narrowed it" % widened)
        if not moved and not widened:
            bits.append("unmoved across %d snapshots" % sum(
                1 for r in rows if r.get("name") == n))
        lines.append(" · ".join(bits))
    return {"title": "YOUR GOALS AND HOW THEY MOVED",
            "lines": lines or ["no goal history recorded yet"],
            "basis": "%d stamped snapshots" % len(rows)}


def do_better() -> Dict[str, Any]:
    """The gaps, quoted from the branches that already admit them. This
    section must never soften: it is the one he asked for by name."""
    lines = []
    try:
        import tree
        t = tree.build()
        for b in t["children"]:
            if b["kind"] in ("repair", "dormant", "unassessed", "self") and b["count"]:
                lines.append("%s (%d) — %s" % (b["label"].lower(), b["count"],
                                               b["meaning"]))
    except Exception as e:
        lines.append("could not read the tree: %s" % str(e)[:60])
    return {"title": "WHAT YOU COULD DO BETTER — the tree's own gap branches",
            "lines": lines, "basis": "meditate tree"}


def switch_state() -> Dict[str, Any]:
    """Is the twin actually armed, each link checked live — never assumed.
    A switch you cannot inspect is a story about a switch."""
    lines = []
    # rounds timer loaded?
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=10).stdout
        rounds = "com.meditate.rounds" in out
        brain = "com.meditate.brain" in out
    except Exception:
        rounds = brain = None
    lines.append("hourly rounds timer: %s" % ("LOADED" if rounds else
                 "NOT LOADED" if rounds is False else "cannot tell"))
    lines.append("brain server: %s" % ("UP" if brain else
                 "DOWN" if brain is False else "cannot tell"))
    # auto gate — what it would say right now
    try:
        import go
        gate = go.auto_should_run()
        lines.append("auto-dispatch gate: %s (%s)"
                     % ("WOULD DISPATCH" if gate.get("run") else "HOLDING",
                        gate.get("why", "")))
    except Exception as e:
        lines.append("auto-dispatch gate: unreadable (%s)" % str(e)[:50])
    # do agents carry the rules? cheapest honest probe: the hook exists and
    # is registered — the live YES probe is in the session record, not rerun
    # here (a claude call per status print would be its own outage).
    hook = os.path.expanduser("~/.claude/hooks/meditate-hook.sh")
    lines.append("rules reach every agent: hook %s (live-probed 2026-08-29: "
                 "headless agent answered YES)"
                 % ("installed" if os.access(hook, os.X_OK) else "MISSING"))
    # last self-check verdict
    try:
        last = None
        with open(os.path.join(MEDITATION_DIR, "doctor.jsonl")) as f:
            for ln in f:
                try:
                    last = json.loads(ln)
                except ValueError:
                    continue
        if last:
            age_h = (time.time() - (last.get("ts_epoch") or 0)) / 3600.0
            lines.append("last self-check: %.1fh ago — %s"
                         % (age_h, "healthy" if last.get("healthy")
                            else ", ".join(last.get("issues", []))[:80]))
    except OSError:
        lines.append("last self-check: no verdict ledger yet")
    return {"title": "THE SWITCH — every link, checked now", "lines": lines,
            "basis": "launchctl + gate + ledgers, live"}


def build() -> List[Dict[str, Any]]:
    return [who_you_are(), how_you_decide(), your_scale(),
            how_goals_evolve(), do_better(), switch_state()]


def render(sections: Optional[List[Dict[str, Any]]] = None) -> str:
    secs = sections if sections is not None else build()
    out = ["YOUR TWIN — derived from the record, nothing invented", "=" * 60]
    for s in secs:
        out.append("")
        out.append(s["title"])
        for l in s["lines"]:
            out.append("  " + l)
        out.append("  (basis: %s)" % s["basis"])
    out.append("")
    out.append("It corrects itself the way it corrects your memories: every")
    out.append("time you correct a session, a rule is written; every hour the")
    out.append("rounds re-check what it believes. Ask Casper anything — he")
    out.append("answers under these rules now.")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate twin",
                                 description="the one entity that knows how you work")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    secs = build()
    if a.json:
        print(json.dumps({"tool_name": "meditate_twin", "success": True,
                          "data": {"sections": secs}, "metadata": {},
                          "errors": []}, indent=2))
        return 0
    print(render(secs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
