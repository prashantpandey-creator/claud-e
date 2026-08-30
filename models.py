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
                                  "sessions": set(), "first": "", "last": ""})

    for f in _transcripts(limit):
        sid = os.path.basename(f)[:8]
        driver = None
        here: Dict[str, int] = {}
        try:
            fh = open(f, errors="replace")
        except OSError:
            continue
        with fh:
            for ln in fh:
                if '"model"' not in ln and '"tool_result"' not in ln:
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
                elif driver:
                    for p in (msg.get("content") or []):
                        if isinstance(p, dict) and p.get("type") == "tool_result" \
                                and p.get("is_error"):
                            slot(driver)["tool_errors"] += 1
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
    ledger = os.path.expanduser("~/.claude/meditation/dispatch.jsonl")
    seen: Dict[str, Dict[str, int]] = {}
    rows = 0
    try:
        with open(ledger) as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("kind") != kind or not r.get("model"):
                    continue
                rows += 1
                s = seen.setdefault(r["model"], {"dispatched": 0, "produced": 0})
                s["dispatched"] += 1
                if r.get("produced"):
                    s["produced"] += 1
    except OSError:
        pass
    return {"kind": kind, "rows": rows, "by_model": seen,
            "enough": rows >= 10}


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
        if s["dispatched"] >= 5:
            return {"model": best, "effort": effort, "basis": "evidence",
                    "why": "on %s tasks %s produced work %d of %d times (%.0f%%)"
                           % (kind, best, s["produced"], s["dispatched"], 100 * rate)}
    return {"model": model, "effort": effort, "basis": "default",
            "why": reason + " — no like-for-like evidence yet (%d recorded %s runs)"
                   % (ev["rows"], kind)}
