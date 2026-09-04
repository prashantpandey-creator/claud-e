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
import time
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
                        human_text = False
                        for p in c:
                            if isinstance(p, dict) and p.get("type") == "tool_result" \
                                    and p.get("is_error") and driver:
                                slot(driver)["tool_errors"] += 1
                            if isinstance(p, dict) and p.get("type") == "text" \
                                    and (p.get("text") or "").strip():
                                human_text = True
                        # `[Request interrupted by user]` arrives HERE — a
                        # list of blocks, not a string — and only the string
                        # branch below ever checked `interrupted`. 66 of the
                        # transcripts carried the marker; the count read 0 of
                        # 15,185 turns. A list-shaped human message also closes
                        # bursts, the same as a string one.
                        if human_text:
                            if interrupted and driver:
                                slot(driver)["interrupted"] += 1
                            for mm, n in streak.items():
                                if n:
                                    slot(mm)["bursts"].append(n)
                            streak = {}
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


def shipped(row: Dict[str, Any]) -> bool:
    """Did this run leave something the world can see? Verified commits or
    a ticked milestone. Exit status and cost are not outcomes."""
    if row.get("verified_commits"):
        return True
    prod = row.get("produced") or {}
    return bool(isinstance(prod, dict) and prod.get("milestone_ticked"))


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
    # spend.jsonl is written by reconcile() from an agent's own result line.
    #
    # `ok ∧ cost>0` was the outcome for a while, and it was 8 of 8 — 100% by
    # construction, so it could not tell a run that shipped from the $1.41,
    # two-turn run that left no commit. SHIPPED means: a commit the agent
    # claimed in its RESULT that `git cat-file -e` confirmed, or a milestone
    # it ticked. The claim alone is not enough; the check is.
    rows = 0
    seen: Dict[str, Dict[str, int]] = {}
    for r in spend()["rows"]:
        if not (r.get("name") or "").startswith(kind):
            continue
        m = r.get("model") or "?"
        rows += 1
        s = seen.setdefault(m, {"dispatched": 0, "produced": 0})
        s["dispatched"] += 1
        if shipped(r):
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


def _settle_worktree(head: Dict[str, str], log_dir: str, key: str) -> str:
    """Remove the agent's worktree once its branch is pushed and nothing is
    continuing the session; otherwise keep it and say why.

    Removal happens HERE, not in go.py: dispatch is fire-and-forget, so the
    only code that ever sees a finished run is this reconcile. A detached
    worktree removed on exit would have stranded the commits of every run
    that stopped early — budget cap, denied push, blocked — and broken
    `--continue`, whose cwd would be gone.
    """
    wt, branch, cwd = head.get("worktree", ""), head.get("branch", ""), head.get("cwd", "")
    if not wt or not branch:
        return "none"
    if not os.path.isdir(wt):
        return "gone"
    import glob as _g
    import subprocess as _sp
    try:
        for p in _g.glob(os.path.join(log_dir, "*.log")):
            if os.path.basename(p) == key:
                continue
            try:
                h = open(p, errors="replace").read(1500)
            except OSError:
                continue
            if "# continues: " + key in h and '"total_cost_usd"' not in \
                    open(p, errors="replace").read():
                return "kept: continue pending"
        up = _sp.run(["git", "-C", wt, "rev-list", "--count", "@{u}..HEAD"],
                     capture_output=True, text=True, timeout=10)
        if up.returncode != 0:
            # No upstream. A read-only run (revive, assess) never pushes and
            # never commits, so "kept: unpushed" would keep its worktree
            # forever — the first live probe did exactly that. Nothing unique
            # on the branch and a clean tree means nothing to lose: remove.
            # --branches/--remotes/--tags, NOT --all: --all includes HEAD,
            # and HEAD is this very branch, so every commit was "reachable
            # elsewhere" and a branch with real work counted as empty — the
            # test caught a committed worktree being removed.
            # Every other ref, listed by name and negated explicitly. Two
            # tries with --exclude got the glob rules wrong both ways
            # (--all includes HEAD; --branches wants the pattern without
            # refs/heads) and each time a branch with real work read as
            # empty and was removed — the test caught both.
            refs = _sp.run(["git", "-C", wt, "for-each-ref", "--format=%(refname)",
                            "refs/heads", "refs/remotes", "refs/tags"],
                           capture_output=True, text=True, timeout=10).stdout.split()
            others = [r for r in refs if r != "refs/heads/" + branch]
            uniq = _sp.run(["git", "-C", wt, "rev-list", "--count", "HEAD", "--not"] + others,
                           capture_output=True, text=True, timeout=10)
            dirty = _sp.run(["git", "-C", wt, "status", "--porcelain"],
                            capture_output=True, text=True, timeout=10).stdout.strip()
            if uniq.returncode == 0 and uniq.stdout.strip() == "0" and not dirty:
                r = _sp.run(["git", "-C", cwd or wt, "worktree", "remove", wt],
                            capture_output=True, text=True, timeout=30)
                if r.returncode == 0:
                    # the BRANCH stays: a Claude session is bound to the
                    # directory it ran in, and `go --continue` re-adds the
                    # worktree at the same path from this branch. Deleting
                    # it made every finished agent un-continuable.
                    return "removed (nothing on the branch; branch kept for continue)"
                return "kept: " + (r.stderr.strip()[:60] or "remove failed")
            return "kept: unpushed (no upstream, %s unique commit%s%s)" % (
                uniq.stdout.strip() or "?", "" if uniq.stdout.strip() == "1" else "s",
                ", dirty" if dirty else "")
        if up.stdout.strip() != "0":
            return "kept: unpushed (%s ahead)" % up.stdout.strip()
        r = _sp.run(["git", "-C", cwd or wt, "worktree", "remove", wt],
                    capture_output=True, text=True, timeout=30)
        return "removed" if r.returncode == 0 else "kept: " + (r.stderr.strip()[:60] or "remove failed")
    except (OSError, _sp.TimeoutExpired) as e:
        return "kept: " + str(e)[:60]


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
    # One pass at a time. Two reconcilers ran together on 2026-09-04 (the
    # brain's tick and a manual pass): both read `seen` before either wrote,
    # and one $3.83 run was ledgered twice. The lock spans read-through-
    # append, so the second pass sees the first pass's rows.
    import fcntl
    try:
        os.makedirs(os.path.dirname(ledger) or ".", exist_ok=True)
        lock = open(ledger + ".lock", "w")
    except OSError:
        lock = None
    if lock is not None:
        fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        return _reconcile_locked(log_dir, ledger)
    finally:
        if lock is not None:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()


def _reconcile_locked(log_dir: str, ledger: str) -> Dict[str, Any]:
    import glob as _g
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
            # A run the CLI refused — "claude: No such file or directory",
            # "No conversation found with session ID …" — is a FAILED run,
            # not a pending one. Its body is one non-event line and it is
            # older than any start-up takes. Recorded at cost 0 so it leaves
            # the live panel and counts against nothing but honesty.
            body = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
            first = body[0].strip() if body else ""
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                age = 0
            if first and not first.startswith("{") and age > 600:
                head = {}
                for ln in text.splitlines()[:9]:
                    if ln.startswith("# ") and ":" in ln:
                        k, _, v = ln[2:].partition(":")
                        head[k.strip()] = v.strip()
                added.append({"log": key, "name": key.split("-", 2)[-1].replace(".log", ""),
                              "model": (head.get("model", "") or "").split(" effort")[0].strip(),
                              "alias": (head.get("model", "") or "").split(" effort")[0].strip(),
                              "effort": "", "ok": False, "cost_usd": 0.0, "turns": 0,
                              "duration_ms": 0, "out_tokens": 0, "cache_creation": 0, "cache_read": 0,
                              "subtype": "died", "died": first[:160], "denials": 0,
                              "session": head.get("session", ""), "worktree": head.get("worktree", ""),
                              "branch": head.get("branch", ""), "produced": None,
                              "verified_commits": [], "worktree_state": "none"})
                continue
            pending += 1          # still running, or too young to call
            continue
        head = {}
        for ln in text.splitlines()[:9]:
            if ln.startswith("# model:"):
                bits = ln[8:].split("effort:")
                head["model"] = bits[0].strip()
                head["effort"] = bits[1].strip() if len(bits) > 1 else ""
            elif ln.startswith("# ") and ":" in ln:
                k, _, v = ln[2:].partition(":")
                head[k.strip()] = v.strip()
        # THE OUTCOME. `--json-schema` output lands in `structured_output`
        # (proven live 2026-09-03). Absent means absent — None, never a
        # verdict; an older run or a budget-cut run has no RESULT.
        produced = res.get("structured_output")
        if not isinstance(produced, dict):
            produced = None
        # The agent's commits are a CLAIM. Check each against the repo it
        # ran in; keep the claim beside the verified list so the ledger shows
        # both what it said and what was true.
        where = head.get("worktree") or head.get("cwd") or ""
        verified: List[str] = []
        for sha in (produced or {}).get("commits") or []:
            if not isinstance(sha, str) or not sha.strip() or not where:
                continue
            try:
                import subprocess as _sp
                ok_sha = _sp.run(["git", "-C", where, "cat-file", "-e",
                                  sha.strip() + "^{commit}"],
                                 capture_output=True, timeout=10).returncode == 0
            except (OSError, _sp.TimeoutExpired):
                ok_sha = False
            if ok_sha:
                verified.append(sha.strip())
        wt_state = _settle_worktree(head, log_dir, key)
        # modelUsage, NOT usage.
        #
        # `usage` is the LAST TURN only; `modelUsage` is the whole session,
        # and total_cost_usd is computed from the latter. Recording `usage`
        # printed last-turn tokens beside a whole-session bill: one run
        # showed "700 output tokens · $1.41" when it had really produced
        # 18,500 and read 771,225 cached. It was caught because that run
        # appeared to cost MORE than a run with more of every token — which
        # is impossible, and was the only reason to look.
        #
        # modelUsage also names the model that ACTUALLY ran. `--model opus`
        # resolved to claude-opus-4-8, while the alias recorded in the header
        # said "opus" — so per-model spend was being filed under a name no
        # model has.
        mu = res.get("modelUsage") or {}
        real = max(mu, key=lambda k: mu[k].get("costUSD", 0)) if mu else ""
        v = mu.get(real, {}) if real else {}
        u = res.get("usage") or {}
        row = {"log": key,
               "name": key.split("-", 2)[-1].replace(".log", ""),
               "model": real or head.get("model", ""),
               "alias": head.get("model", ""),
               "effort": head.get("effort", ""),
               "ok": not res.get("is_error"),
               "cost_usd": res.get("total_cost_usd"),
               "turns": res.get("num_turns"),
               "duration_ms": res.get("duration_ms"),
               "out_tokens": v.get("outputTokens", u.get("output_tokens")),
               "cache_creation": v.get("cacheCreationInputTokens",
                                       u.get("cache_creation_input_tokens")),
               "cache_read": v.get("cacheReadInputTokens",
                                   u.get("cache_read_input_tokens")),
               "subtype": res.get("subtype"),
               "denials": len(res.get("permission_denials") or []),
               "session": head.get("session", ""),
               "worktree": head.get("worktree", ""),
               "branch": head.get("branch", ""),
               "produced": produced,
               "verified_commits": verified,
               "worktree_state": wt_state}
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
    # PER KIND is the rollup that decides anything: it is the only grouping
    # task difficulty does not poison, and it is what the budget is set from.
    by_kind: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        k = (r.get("name") or "?").split("-")[0]
        s = by_kind.setdefault(k, {"kind": k, "runs": 0, "usd": 0.0,
                                   "out": 0, "cache_read": 0, "turns": 0,
                                   "costs": []})
        s["runs"] += 1
        s["usd"] += r.get("cost_usd") or 0
        s["out"] += r.get("out_tokens") or 0
        s["cache_read"] += r.get("cache_read") or 0
        s["turns"] += r.get("turns") or 0
        s["costs"].append(r.get("cost_usd") or 0)
    kinds = []
    for k, s in by_kind.items():
        cap = budget_for(k, rows=rows)
        kinds.append({"kind": k, "runs": s["runs"], "turns": s["turns"],
                      "usd": round(s["usd"], 4),
                      "avg": round(s["usd"] / s["runs"], 4) if s["runs"] else 0,
                      "lo": round(min(s["costs"]), 4) if s["costs"] else 0,
                      "hi": round(max(s["costs"]), 4) if s["costs"] else 0,
                      "cap_usd": cap["usd"], "cap_basis": cap["basis"],
                      # the ratio that IS the cost: cache-read against output.
                      # 10-40x on every run measured so far.
                      "read_per_out": round(s["cache_read"] / s["out"], 1)
                                      if s["out"] else None,
                      "out_per_turn": round(s["out"] / s["turns"])
                                      if s["turns"] else None})
    kinds.sort(key=lambda x: -x["usd"])
    return {"runs": len(rows), "total_usd": round(total, 4),
            "per_model": sorted(per.values(), key=lambda x: -x["usd"]),
            "per_kind": kinds, "rows": rows}


def budget_for(kind: str, headroom: float = 2.0,
               rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
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
    # rows are passed in by spend() itself — calling spend() here made the
    # two functions each other's base case and the first run blew the stack.
    src = rows if rows is not None else spend()["rows"]
    same = [r for r in src
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
