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

    def slot(m):
        return per.setdefault(m, {"model": m, "turns": 0, "tool_calls": 0,
                                  "tool_errors": 0, "out_tokens": 0,
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

    rows = []
    for m, s in per.items():
        calls = s["tool_calls"]
        rows.append({**s,
                     "sessions": len(s["sessions"]),
                     "error_share": round(s["tool_errors"] / calls, 3) if calls else None,
                     "out_per_turn": round(s["out_tokens"] / s["turns"]) if s["turns"] else 0})
    rows.sort(key=lambda r: -r["turns"])
    mixed = sum(1 for x in sessions if x["mixed"])
    return {"models": rows, "sessions": sessions,
            "scanned": len(_transcripts(limit)),
            "mixed_sessions": mixed}


def render(d: Optional[Dict[str, Any]] = None) -> str:
    d = d or scan()
    out = ["WHICH MODEL DID WHICH WORK", "=" * 62]
    out.append("  %-26s %6s %7s %8s %7s %6s" %
               ("model", "turns", "calls", "errored", "out/turn", "sess"))
    for r in d["models"]:
        share = "—" if r["error_share"] is None else "%.1f%%" % (100 * r["error_share"])
        out.append("  %-26s %6d %7d %8s %7d %6d" %
                   (r["model"][:26], r["turns"], r["tool_calls"], share,
                    r["out_per_turn"], r["sessions"]))
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
