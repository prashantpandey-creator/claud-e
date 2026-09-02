"""models — which model did which work, and how its commands actually fared.

Every assistant turn in a Claude Code transcript carries `message.model`, and
every tool call it issues gets a result that is either fine or `is_error`.
That makes two things measurable without anyone labelling anything:

    WHO DID IT      per TURN, not per session — 9 of 30 recent sessions used
                    more than one model, one of them five, so "this session
                    was Sonnet" is false on a third of the record
    WHAT HAPPENED   the share of that model's tool calls that came back an
                    error, and what it spent doing it

WHAT THIS IS NOT. An error rate is not a quality score, and this module must
never print one. A model handed the hard half of an afternoon — the flaky
deploys, the 60-second curls, the greps into files that turn out not to exist
— will error more than one asked to reword a docstring, and it may have been
the better worker in the same hour. Difficulty is not recorded anywhere, so
the confound cannot be removed, only stated. Every number here is "what
happened while this model was driving", never "how good this model is", and
the report says so on its own face.

    meditate models             # per model: turns, tool calls, error share
    meditate models --sessions  # which model drove which session
    meditate models --json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

PROJECTS = os.path.expanduser("~/.claude/projects")

# Not a model — Claude Code's own placeholder on synthesised rows. Counting it
# would invent a seventh model that never ran anything.
_NOT_A_MODEL = {"<synthetic>", ""}


def _transcripts(limit: Optional[int] = None) -> List[str]:
    files = sorted(glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")),
                   key=lambda p: os.path.getmtime(p), reverse=True)
    return files[:limit] if limit else files


def scan(limit: Optional[int] = 40) -> Dict[str, Any]:
    """Per-model counts, attributed turn by turn.

    The attribution walks the file in order and remembers the model of the
    last assistant turn, because a tool_result arrives in a LATER row than the
    tool_use that caused it. Attaching the error to whoever was driving when
    the call was issued is the only join the transcript supports.
    """
    per: Dict[str, Dict[str, Any]] = {}
    sessions: List[Dict[str, Any]] = []
    effort_all: Dict[str, int] = {}

    def slot(m):
        return per.setdefault(m, {"model": m, "turns": 0, "tool_calls": 0,
                                  "tool_errors": 0, "out_tokens": 0,
                                  "think_tokens": 0, "effort": {},
                                  "make": 0, "look": 0, "run": 0, "mcp": 0,
                                  "interrupted": 0, "bursts": [],
                                  "sessions": set(), "first": "", "last": ""})

    for f in _transcripts(limit):
        sid = os.path.basename(f)[:8]
        driver = None
        here: Dict[str, int] = {}
        # How many turns in a row it ran before the owner spoke again. This is
        # LEASH LENGTH — a behavioural measure of how far he let each model go
        # unattended, not an opinion about any of them.
        streak: Dict[str, int] = {}
        try:
            fh = open(f, errors="replace")
        except OSError:
            continue
        with fh:
            for ln in fh:
                interrupted = "Request interrupted" in ln
                # The fast path must not drop PLAIN HUMAN MESSAGES: they carry
                # neither "model" nor "tool_result", so the first cut skipped
                # every one and no burst ever closed — leash read None for all
                # six models while the prototype had measured 5 to 17.
                if '"model"' not in ln and '"tool_result"' not in ln \
                        and '"user"' not in ln and not interrupted:
                    continue
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                msg = d.get("message") or {}
                ts = (d.get("timestamp") or "")[:19]
                if d.get("type") == "assistant":
                    m = msg.get("model") or ""
                    if m in _NOT_A_MODEL:
                        continue
                    driver = m
                    s = slot(m)
                    s["turns"] += 1
                    s["sessions"].add(sid)
                    here[m] = here.get(m, 0) + 1
                    u = msg.get("usage") or {}
                    s["out_tokens"] += u.get("output_tokens") or 0
                    # thinking_tokens sits in usage.output_tokens_details, and
                    # `effort` is a ROW field, not a message field — the two
                    # halves of "how hard was it trying" live in different
                    # places, which is why neither was in any report.
                    det = u.get("output_tokens_details") or {}
                    s["think_tokens"] += det.get("thinking_tokens") or 0
                    e = d.get("effort")
                    if e:
                        s["effort"][e] = s["effort"].get(e, 0) + 1
                        effort_all[e] = effort_all.get(e, 0) + 1
                    if ts:
                        s["first"] = min(s["first"] or ts, ts)
                        s["last"] = max(s["last"], ts)
                    for p in (msg.get("content") or []):
                        if isinstance(p, dict) and p.get("type") == "tool_use":
                            s["tool_calls"] += 1
                            # WHAT KIND of work, not just how much. A model at
                            # 19% make is producing code; one at 93% run is
                            # executing someone else's plan. That difference
                            # was invisible in a turn count.
                            n = p.get("name") or ""
                            if n in ("Edit", "Write", "NotebookEdit"):
                                s["make"] += 1
                            elif n in ("Read", "Grep", "Glob"):
                                s["look"] += 1
                            elif n == "Bash":
                                s["run"] += 1
                            elif n.startswith("mcp__"):
                                s["mcp"] += 1
                    streak[m] = streak.get(m, 0) + 1
                elif d.get("type") == "user":
                    c = msg.get("content")
                    if isinstance(c, list):
                        for p in c:
                            if isinstance(p, dict) and p.get("type") == "tool_result" \
                                    and p.get("is_error") and driver:
                                slot(driver)["tool_errors"] += 1
                    elif isinstance(c, str) and c.strip():
                        # a real human message closes every open burst
                        if interrupted and driver:
                            slot(driver)["interrupted"] += 1
                        for mm, n in streak.items():
                            if n:
                                slot(mm)["bursts"].append(n)
                        streak = {}
        if here:
            top = max(here, key=here.get)
            sessions.append({"session": sid, "models": here, "primary": top,
                             "mixed": len(here) > 1,
                             "turns": sum(here.values())})

    # Ordered hardest-first so "mostly max" and "mostly low" read at a glance.
    LADDER = ["max", "xhigh", "high", "medium", "low"]
    rows = []
    for m, s in per.items():
        calls = s["tool_calls"]
        eff = s["effort"]
        seen = sum(eff.values())
        rows.append({**s,
                     "sessions": len(s["sessions"]),
                     "error_share": round(s["tool_errors"] / calls, 3) if calls else None,
                     "out_per_turn": round(s["out_tokens"] / s["turns"]) if s["turns"] else 0,
                     "think_per_turn": round(s["think_tokens"] / s["turns"]) if s["turns"] else 0,
                     # None, not 0: a turn with no effort recorded is not a
                     # turn that tried nothing.
                     "top_effort": (max(eff, key=eff.get) if eff else None),
                     "make_share": round(s["make"] / calls, 3) if calls else None,
                     "look_share": round(s["look"] / calls, 3) if calls else None,
                     "run_share": round(s["run"] / calls, 3) if calls else None,
                     # median, not mean: one 200-turn night would drag a mean
                     # into fiction for every other burst.
                     "leash": (sorted(s["bursts"])[len(s["bursts"]) // 2]
                               if s["bursts"] else None),
                     "bursts": len(s["bursts"]),
                     "effort_mix": " ".join("%s %d%%" % (k, round(100 * eff[k] / seen))
                                            for k in LADDER if eff.get(k)) or "—"})
    rows.sort(key=lambda r: -r["turns"])
    mixed = sum(1 for x in sessions if x["mixed"])
    return {"models": rows, "sessions": sessions,
            "scanned": len(_transcripts(limit)),
            "mixed_sessions": mixed, "effort_all": effort_all}


def render(d: Optional[Dict[str, Any]] = None) -> str:
    d = d or scan()
    out = ["WHICH MODEL DID WHICH WORK", "=" * 62]
    out.append("  %-24s %6s %7s %8s %8s %8s %5s" %
               ("model", "turns", "calls", "errored", "out/turn", "think/tn", "sess"))
    for r in d["models"]:
        share = "—" if r["error_share"] is None else "%.1f%%" % (100 * r["error_share"])
        out.append("  %-24s %6d %7d %8s %8d %8d %5d" %
                   (r["model"][:24], r["turns"], r["tool_calls"], share,
                    r["out_per_turn"], r["think_per_turn"], r["sessions"]))
    out.append("")
    out.append("  what kind of work each did, and how far you let it run:")
    out.append("  %-24s %6s %6s %6s %8s %7s" %
               ("model", "make", "look", "run", "leash", "stopped"))
    for r in d["models"]:
        pct = lambda v: "—" if v is None else "%.0f%%" % (100 * v)
        out.append("  %-24s %6s %6s %6s %8s %7s" %
                   (r["model"][:24], pct(r["make_share"]), pct(r["look_share"]),
                    pct(r["run_share"]),
                    "—" if r["leash"] is None else "%d turns" % r["leash"],
                    r["interrupted"] if r["interrupted"] else "none"))
    out.append("")
    out.append("  make = Edit/Write · look = Read/Grep · run = Bash.")
    out.append("  leash = the MEDIAN turns it ran before you spoke again — a")
    out.append("  behavioural measure of how far you let it go, confounded by")
    out.append("  what you happened to use each one FOR. 'stopped' counts real")
    out.append("  interruptions; none recorded is not the same as flawless.")
    out.append("")
    out.append("  effort each was run at:")
    for r in d["models"]:
        out.append("  %-24s %s" % (r["model"][:24], r["effort_mix"]))
    out.append("")
    out.append("  %d transcripts · %d of %d sessions used MORE THAN ONE model, so"
               % (d["scanned"], d["mixed_sessions"], len(d["sessions"])))
    out.append("  every number above is attributed per TURN, never per session.")
    out.append("")
    # The caveat is part of the output, not a footnote someone can drop.
    out.append("  'errored' is the share of this model's tool calls that came back")
    out.append("  an error. It is NOT a quality score: a model handed the hard half")
    out.append("  of an afternoon errors more than one asked to reword a docstring,")
    out.append("  and difficulty is recorded nowhere. This says what happened while")
    out.append("  each model was driving — nothing about which is better.")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate models",
                                 description="which model did which work")
    ap.add_argument("--sessions", action="store_true", help="per-session breakdown")
    ap.add_argument("--limit", type=int, default=40, help="how many transcripts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    d = scan(a.limit)
    if a.json:
        print(json.dumps({"tool_name": "meditate_models", "success": True,
                          "data": d, "metadata": {}, "errors": []}, indent=2))
        return 0
    if a.sessions:
        print("%-10s %-26s %6s  %s" % ("session", "primary model", "turns", "mixed with"))
        for s in d["sessions"]:
            others = ", ".join(m for m in s["models"] if m != s["primary"])
            print("%-10s %-26s %6d  %s" % (s["session"], s["primary"][:26],
                                           s["turns"], others or "—"))
        return 0
    print(render(d))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


# ---------------------------------------------------------------------------
# choosing WHO to dispatch — and refusing to pretend the evidence is there
# ---------------------------------------------------------------------------
#
# Measured 2026-08-30, before any of this was written:
#   0 of 6 goal files name a model, so k["model"] is always "" and every
#   dispatch falls through to a hardcoded `--model sonnet`;
#   `effort` appears 0 times in go.py, launch.py and goals.py, so every agent
#   this tool has ever dispatched ran at the CLI default.
#
# So the fleet was not choosing badly — it was not choosing at all, invisibly.
#
# What this does NOT do is rank models by the error share above. That number
# is confounded by task difficulty, which is recorded nowhere; picking a model
# with it would be the exact defect the caveat in this module exists to stop.
# And there is no per-task evidence yet either: of 58 dispatch rows only 6
# opened a window and 3 of 6 headless logs held any output at all.
#
# So `pick` returns a choice, the REASON for it, and whether that reason is
# EVIDENCE or a stated default — and the dispatcher records model, effort and
# reason on every row, so the third thing becomes possible: comparing like
# with like, per task kind, once the record exists. A default that says it is
# a default can be argued with; a hardcoded one cannot.

# Task kinds the dispatcher actually emits, and what each really is.
_KINDS = {
    "repair":  "a memory failed its own check — one file, verifiable, bounded",
    "goal":    "an open milestone — open-ended, multi-file, needs judgment",
    "thread":  "a continuation chat — resume a known thread",
    "revive":  "read a dormant repo and report where it stands, change nothing",
}

# Stated defaults, each with the reason it is defensible WITHOUT a quality
# claim. Cost and latency are facts about the models; "better" is not.
_DEFAULTS = {
    "repair":  ("sonnet", "high",
                "bounded and verifiable, so the cheaper, faster model is the "
                "defensible default — not a quality judgment"),
    "goal":    ("opus", "max",
                "open-ended and multi-file: the expensive choice is the "
                "defensible default when the work cannot be checked cheaply"),
    "thread":  ("sonnet", "high",
                "the context is already written down, so the task is to "
                "continue rather than to decide"),
    "revive":  ("sonnet", "high",
                "read-and-report, no code change — the cheapest thing that "
                "can read a repo honestly"),
}


def evidence_for(kind: str, limit: int = 40) -> Dict[str, Any]:
    """What the record can say about THIS task kind. Usually: nothing yet.

    Per-kind is the only comparison that is not confounded by difficulty —
    repair tasks resemble each other. It reads the dispatch ledger, which
    began carrying model/effort/kind on 2026-08-30; before that date the rows
    cannot answer, and this says so rather than averaging over silence.
    """
    # OUTCOMES COME FROM THE SPEND LEDGER, not the dispatch ledger.
    #
    # The dispatch ledger has a `produced` field that NOTHING EVER WROTE — it
    # was designed and never populated, so every row read 0, and pick() then
    # recommended opus on the stated grounds that it "produced work 0 of 18
    # times (0%)". A rate computed over a field nobody sets is not evidence,
    # it is an empty column with a percentage sign on it.
    #
    # spend.jsonl is written by reconcile() from an agent's own result line,
    # so `ok` there means the run really finished. That is an outcome.
    rows = 0
    seen: Dict[str, Dict[str, int]] = {}
    for r in spend()["rows"]:
        if not (r.get("name") or "").startswith(kind):
            continue
        m = r.get("model") or "?"
        rows += 1
        s = seen.setdefault(m, {"dispatched": 0, "produced": 0})
        s["dispatched"] += 1
        if r.get("ok") and (r.get("cost_usd") or 0) > 0:
            s["produced"] += 1
    return {"kind": kind, "rows": rows, "by_model": seen,
            "enough": rows >= 6}


def pick(kind: str, limit: int = 40) -> Dict[str, Any]:
    """Which model and effort to dispatch, and WHY — evidence or default."""
    model, effort, reason = _DEFAULTS.get(
        kind, ("sonnet", "high", "unknown task kind — the safe middle"))
    ev = evidence_for(kind, limit)
    if ev["enough"]:
        # Only once like-for-like rows exist, and only on a rate that means
        # something: did the agent produce anything at all.
        ranked = sorted(ev["by_model"].items(),
                        key=lambda kv: -(kv[1]["produced"] / max(1, kv[1]["dispatched"])))
        best, s = ranked[0]
        rate = s["produced"] / max(1, s["dispatched"])
        # A WINNER AT 0% IS NOT A WINNER. Sorting descending makes the only
        # model in the data "best" even when it never produced anything, and
        # the reason string then reads "produced work 0 of 18 times (0%)" as
        # a justification. Zero is an absence; fall through and say so.
        if s["dispatched"] >= 3 and rate > 0:
            return {"model": best, "effort": effort, "basis": "evidence",
                    "why": "on %s tasks %s produced work %d of %d times (%.0f%%)"
                           % (kind, best, s["produced"], s["dispatched"], 100 * rate)}
    return {"model": model, "effort": effort, "basis": "default",
            "why": reason + " — no like-for-like evidence yet (%d recorded %s runs)"
                   % (ev["rows"], kind)}


# ---------------------------------------------------------------------------
# what a dispatched agent ACTUALLY spent
# ---------------------------------------------------------------------------

SPEND_LEDGER = os.path.expanduser("~/.claude/meditation/spend.jsonl")
AGENT_LOGS = os.path.expanduser("~/.claude/meditation/agents")


def _result_line(text: str) -> Optional[Dict[str, Any]]:
    """The `--output-format json` result object, from anywhere in a log.

    It is not always the last line: a crashed or chatty run can append after
    it, and a run still in flight has none at all. Scanned from the end, and
    absence returns None rather than a zero — an agent that has not finished
    has not spent nothing.
    """
    for ln in reversed(text.splitlines()):
        ln = ln.strip()
        if not ln.startswith("{") or '"total_cost_usd"' not in ln:
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get("type") == "result":
            return d
    return None


def reconcile(log_dir: Optional[str] = None,
              ledger: Optional[str] = None) -> Dict[str, Any]:
    """Read finished agent logs and record what each one really cost.

    Dispatch is fire-and-forget by design — blocking on an agent would freeze
    the rounds — so the actual cannot be written at launch. This closes the
    loop afterwards: every log with a result line becomes one spend row, keyed
    by the log's own name so a second pass cannot double-count.
    """
    import glob as _g
    log_dir = log_dir or AGENT_LOGS
    ledger = ledger or SPEND_LEDGER
    seen = set()
    try:
        with open(ledger) as f:
            for ln in f:
                try:
                    seen.add(json.loads(ln).get("log"))
                except ValueError:
                    continue
    except OSError:
        pass

    added, pending = [], 0
    for path in sorted(_g.glob(os.path.join(log_dir, "*.log"))):
        key = os.path.basename(path)
        if key in seen:
            continue
        try:
            text = open(path, errors="replace").read()
        except OSError:
            continue
        res = _result_line(text)
        if res is None:
            pending += 1          # still running, or died before reporting
            continue
        head = {}
        for ln in text.splitlines()[:4]:
            if ln.startswith("# model:"):
                bits = ln[8:].split("effort:")
                head["model"] = bits[0].strip()
                head["effort"] = bits[1].strip() if len(bits) > 1 else ""
        u = res.get("usage") or {}
        row = {"log": key,
               "name": key.split("-", 2)[-1].replace(".log", ""),
               "model": head.get("model", ""), "effort": head.get("effort", ""),
               "ok": not res.get("is_error"),
               "cost_usd": res.get("total_cost_usd"),
               "turns": res.get("num_turns"),
               "duration_ms": res.get("duration_ms"),
               "out_tokens": u.get("output_tokens"),
               "cache_creation": u.get("cache_creation_input_tokens"),
               "cache_read": u.get("cache_read_input_tokens")}
        added.append(row)

    if added:
        try:
            with open(ledger, "a") as f:
                for r in added:
                    f.write(json.dumps(r) + "\n")
        except OSError:
            pass
    return {"added": len(added), "pending": pending, "rows": added}


def spend(ledger: Optional[str] = None) -> Dict[str, Any]:
    """Every recorded dispatch, and what it really cost."""
    rows = []
    try:
        with open(ledger or SPEND_LEDGER) as f:
            for ln in f:
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        pass
    total = sum(r.get("cost_usd") or 0 for r in rows)
    per: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        m = r.get("model") or "?"
        s = per.setdefault(m, {"model": m, "runs": 0, "usd": 0.0, "turns": 0})
        s["runs"] += 1
        s["usd"] += r.get("cost_usd") or 0
        s["turns"] += r.get("turns") or 0
    return {"runs": len(rows), "total_usd": round(total, 4),
            "per_model": sorted(per.values(), key=lambda x: -x["usd"]),
            "rows": rows}


def budget_for(kind: str, headroom: float = 2.0) -> Dict[str, Any]:
    """A real, ENFORCED dollar cap for one dispatched agent.

    `--max-budget-usd` halts the run (subtype error_max_budget_usd, proven
    live), which is strictly better than the turn ceiling this replaced: that
    one was never enforced at all — the plan said "up to 12 turns" and bro-os
    ran 14.

    It is a STOP SIGNAL, NOT A HARD CEILING. A single API call can already
    cost more than a tight cap, so it overshoots by up to one call: measured
    $0.0242 against a $0.001 cap. At realistic caps the overshoot is one
    call's worth, and the number below says which basis it came from.

    Derived from what the same KIND of task really cost — the only comparison
    task difficulty does not poison.
    """
    d = spend()
    same = [r for r in d["rows"]
            if (r.get("name") or "").startswith(kind) and r.get("cost_usd")]
    if len(same) >= 3:
        costs = sorted(r["cost_usd"] for r in same)
        med = costs[len(costs) // 2]
        return {"usd": round(max(0.25, med * headroom), 2), "basis": "evidence",
                "why": "median of %d measured %s runs ($%.2f) x%.1f"
                       % (len(same), kind, med, headroom),
                "runs": len(same), "median": round(med, 4)}
    # No like-for-like runs yet. A stated default, not a guess dressed up:
    # every dispatch loads ~26k cache-creation tokens before it does anything,
    # so nothing useful finishes under a quarter.
    return {"usd": 2.00, "basis": "default",
            "why": "no measured %s runs yet (%d recorded) — a stated cap, and "
                   "every dispatch pays ~26k cache-creation tokens before it "
                   "starts" % (kind, len(same)),
            "runs": len(same), "median": None}
