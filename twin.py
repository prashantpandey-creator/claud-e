"""twin — CLAUD-E: the one entity that knows how you work, and its switch.

Named the way WALL-E is named — a machine with a hyphen and a job. The FORM
stays abstract (standing rule: forms, never borrowed likenesses); only the
naming pattern is borrowed.

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
    if not lines:
        # A stranger's first run lands here. The first cut asserted "you
        # approve tersely and decide by leverage" over ZERO interactions —
        # the author's own rule spoken about a person it had never met, the
        # exact synthesised-profile sin this module forbids. Empty record,
        # empty claim.
        lines.append("not enough recorded yet to say — this section fills "
                     "in as you use the tool")
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
        # The flavour sentence only when the numbers actually show it:
        # asserted for a person with 1 product it is a horoscope.
        if g.get("real_projects", 0) >= 10 and \
                g.get("assessed", 0) * 3 < g.get("real_projects", 0):
            lines.append("you start wide and finish narrow — that ratio IS "
                         "the flavour, and the dormant list is its cost")
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
        n_snaps = sum(1 for r in rows if r.get("name") == n)
        if not moved and not widened:
            # One snapshot is a baseline, not stagnation. "Unmoved across 1
            # snapshots" read as an accusation on a goal created yesterday.
            bits.append("first snapshot — nothing to compare yet"
                        if n_snaps <= 1 else
                        "unmoved across %d snapshots" % n_snaps)
        lines.append(" · ".join(bits))
    return {"title": "YOUR GOALS AND HOW THEY MOVED",
            "lines": lines or ["no goal history recorded yet"],
            "basis": "%d stamped snapshots" % len(rows)}


def goal_series() -> Dict[str, Any]:
    """Every stamped snapshot per goal, as a plottable series.

    The text twin says "+8 done since first measured"; a line shows WHEN the
    eight landed and where the scope stepped up under them. Same rows, same
    filter (goals that exist now), no smoothing and no interpolation — the
    points are the snapshots, and a gap in the record stays a gap.
    """
    rows = _goal_history()
    try:
        import goals as _gl
        real = {g.get("name") for g in _gl.scan()}
        rows = [r for r in rows if r.get("name") in real]
    except Exception:
        pass
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        n = r.get("name")
        if not n:
            continue
        out.setdefault(n, []).append({"ts": r.get("ts", ""),
                                      "done": r.get("done", 0),
                                      "total": r.get("total", 0)})
    return {"goals": out, "points": sum(len(v) for v in out.values())}


def who_did_the_work() -> Dict[str, Any]:
    """Which model drove, at what effort, and how its commands fared.

    Attributed per TURN — 9 of 40 sessions used more than one model, so a
    session-level label is false on a quarter of the record. `effort` is a row
    field and thinking_tokens sits inside usage.output_tokens_details; the two
    halves of "how hard was it trying" live in different places, which is why
    neither had ever reached a report.

    Never a ranking. Error share is confounded by task difficulty and
    difficulty is recorded nowhere.
    """
    lines: List[str] = []
    basis = "unavailable"
    try:
        import models as _md
        d = _md.scan(limit=40)
        for r in d["models"][:6]:
            share = "—" if r["error_share"] is None else "%.1f%%" % (100 * r["error_share"])
            pct = lambda v: "—" if v is None else "%.0f%%" % (100 * v)
            leash = ("you let it run %d turns before speaking" % r["leash"]) \
                if r["leash"] else "no unattended stretch recorded"
            lines.append("%s — %d turns; %s writing, %s reading, %s running "
                         "commands; %s of its tool calls errored; %d thinking "
                         "tokens a turn at %s; %s"
                         % (r["model"], r["turns"], pct(r["make_share"]),
                            pct(r["look_share"]), pct(r["run_share"]), share,
                            r["think_per_turn"], r["effort_mix"], leash))
        if lines:
            lines.append("error share is NOT a quality score and leash is NOT "
                         "a rating — difficulty and what you used each one FOR "
                         "are recorded nowhere. This says what happened while "
                         "each was driving, never which is better")
        basis = ("%d transcripts, attributed per turn (%d sessions mixed models)"
                 % (d["scanned"], d["mixed_sessions"]))
    except Exception as e:
        lines.append("could not read the transcripts: %s" % str(e)[:60])
    return {"title": "WHO DID THE WORK — model and effort, from the record",
            "lines": lines, "basis": basis}


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
    # No baked-in probe citation. The first cut appended "(live-probed
    # 2026-08-29: headless agent answered YES)" unconditionally — the
    # author's own measurement stamped onto every machine, INCLUDING next to
    # "hook MISSING", where it contradicted itself in one line.
    lines.append("rules reach every agent: hook %s"
                 % ("installed" if os.access(hook, os.X_OK)
                    else "MISSING — run install.sh"))
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



# ---------------------------------------------------------------------------
# the boot sequence — a visual cue that never lies
# ---------------------------------------------------------------------------
#
# `meditate twin` spends ~12 real seconds deriving before it can say a word
# (the project field alone is ~10s of git). It used to spend them in silence
# and then dump text. The boot sequence spends them visibly instead — and the
# law of this module extends to its theatre: every line lands when its
# section ACTUALLY finishes, labelled with its section's own basis string and
# its measured duration. No fake spinner over a sleep, no progress bar with
# an invented total. Sci-fi is the styling; the content is the real work.
#
# TTY only. Piped, --json, and MEDITATE_TESTING get plain output, so scripts
# and tests never see an escape code.

_GOLD = "\033[38;5;179m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"
_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_WORDMARK = """\
   ██████╗ ██╗      █████╗  ██╗   ██╗ ██████╗        ███████╗
  ██╔════╝ ██║     ██╔══██╗ ██║   ██║ ██╔══██╗       ██╔════╝
  ██║      ██║     ███████║ ██║   ██║ ██║  ██║ █████╗█████╗
  ██║      ██║     ██╔══██║ ██║   ██║ ██║  ██║ ╚════╝██╔══╝
  ╚██████╗ ███████╗██║  ██║ ╚██████╔╝ ██████╔╝       ███████╗
   ╚═════╝ ╚══════╝╚═╝  ╚═╝  ╚═════╝  ╚═════╝        ╚══════╝"""

# An ORIGINAL face — the naming pattern is WALL-E's, the face is ours.
_FACE_BOOTING = """\
             ┌───────────────┐
             │   ──     ──   │   deriving…
             └───┬───────┬───┘"""
_FACE_ONLINE = """\
             ┌───────────────┐
             │   ◉       ◉   │   awake
             └───┬───────┬───┘"""

_BOOT_LABELS = [
    ("memory lattice", "your rules, recovered", who_you_are),
    ("decision record", "how you actually decide", how_you_decide),
    ("project field", "everything you have built", your_scale),
    ("goal trajectories", "where each goal moved", how_goals_evolve),
    ("model attribution", "who drove, at what effort", who_did_the_work),
    ("gap analysis", "what the record holds against you", do_better),
    ("switch integrity", "every autonomous link, checked", switch_state),
]


def boot(write=None) -> List[Dict[str, Any]]:
    """Derive every section, showing each one land as it truly does."""
    import threading
    w = write or (lambda t: (sys.stdout.write(t), sys.stdout.flush()))
    w(_GOLD + _WORDMARK + _RESET + "\n")
    w(_DIM + _FACE_BOOTING + _RESET + "\n")
    w(_DIM + "\n  every line lands when its derivation truly finishes\n\n" + _RESET)
    sections: List[Dict[str, Any]] = []
    for name, sub, fn in _BOOT_LABELS:
        box: Dict[str, Any] = {}
        th = threading.Thread(target=lambda f=fn: box.update(s=f()))
        t0 = time.time()
        th.start()
        i = 0
        while th.is_alive():
            w("\r  " + _GOLD + _SPIN[i % len(_SPIN)] + _RESET +
              " %-18s " % name + _DIM + sub + _RESET)
            i += 1
            time.sleep(0.08)
        th.join()
        dt = time.time() - t0
        s = box.get("s") or {"title": name, "lines": [], "basis": "failed"}
        sections.append(s)
        w("\r  " + _GOLD + "▸" + _RESET + " %-18s " % name +
          "%-42s" % s["basis"][:42] + _DIM + " %5.1fs" % dt + _RESET + "\n")
    w(_GOLD + "\n" + _FACE_ONLINE + _RESET + "\n")
    w(_GOLD + _BOLD + "◤ CLAUD-E ONLINE" + _RESET +
      _DIM + " — derived from the record, nothing invented\n" + _RESET)
    return sections


def build() -> List[Dict[str, Any]]:
    return [who_you_are(), how_you_decide(), your_scale(),
            how_goals_evolve(), who_did_the_work(), do_better(), switch_state()]


def render(sections: Optional[List[Dict[str, Any]]] = None) -> str:
    secs = sections if sections is not None else build()
    out = ["CLAUD-E — your digital twin, derived from the record, nothing invented",
           "=" * 60]
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
    ap.add_argument("--no-boot", action="store_true",
                    help="plain output even on a terminal")
    a = ap.parse_args(argv)
    animate = (not a.json and not a.no_boot
               and sys.stdout.isatty()
               and not os.environ.get("MEDITATE_TESTING"))
    secs = boot() if animate else build()
    if a.json:
        print(json.dumps({"tool_name": "meditate_twin", "success": True,
                          "data": {"sections": secs}, "metadata": {},
                          "errors": []}, indent=2))
        return 0
    print(render(secs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
