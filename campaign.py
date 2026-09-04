#!/usr/bin/env python3
"""The all-goals run: plan the whole graph, get one go, execute, watch, steer.

The owner's ask (2026-09-03): a mode where the twin reads everything it has,
plans the completion of EVERY present goal — elaborated as far as it can into
a graph, because solving one step is how you see the next — says who will do
each step, shows it in plain words, waits for one "go", and then runs it.
It is the biggest run the tool will make, so it is watched with numbers and
steered mid-flight.

The graph is BUILT, never typed:
  - every open milestone of every goal is a node, in the goal author's order
    (the order in the file is a dependency the author already decided)
  - an elaborator expands a milestone into sub-steps with their own order;
    the milestone then depends on all of them
  - a finished node's RESULT.next joins the graph as its child — the step
    the agent saw with its hands on the work

Nothing dispatches before `go`. `go` arms the campaign and sends the ready
wave; every heartbeat `tick` advances it: reads results, grows the graph,
marks blocked / failed / stuck, and sends the next ready wave under the cap.
`steer` is `go --continue` on the node's own session. `pause` disarms.

Every external effect is injectable: elaborator, dispatch, read_result,
log_mtime, now, continue_fn. The tests run with no agent and no money.

    python3 campaign.py plan            build the graph, write the page
    python3 campaign.py show            the page
    python3 campaign.py go [--max N]    arm and send the first wave
    python3 campaign.py tick            advance (the heartbeat calls this)
    python3 campaign.py status          one screen of numbers
    python3 campaign.py pause "why"     disarm
    python3 campaign.py steer <node> "message"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

MEDITATION_DIR = os.path.expanduser("~/.claude/meditation")
STATE_NAME = "campaign.json"
PAGE_NAME = "campaign.md"
STALL_S = 45 * 60            # running, no result, log unchanged this long
# Wall-time bound per run: 2x the kind's median duration from the ledger,
# floored and capped. Nothing bounded an agent's wall time before — stuck
# was a flag on the page; the process ran on. A stopped run is resumed once
# with the fact, then handed to the owner.
WALL_FACTOR = 2.0
WALL_MIN_S = 30 * 60
WALL_MAX_S = 120 * 60
WALL_DEFAULT_S = 60 * 60
MAX_ATTEMPTS = 2
# A usage/rate limit answer is not the node's fault: the whole campaign
# holds this long, the node keeps its session and its attempts.
HOLD_S = 30 * 60
_LIMIT_RE = re.compile(r"rate.?limit|usage limit|hit your (usage|limit)|overloaded|too many requests|\b429\b|quota", re.I)
DEFAULT_PARALLEL = 3         # the RAM law: 6+9+8 < 30 GB, from the outage
ELABORATE_BUDGET_USD = 0.35  # planning is cheap; execution is not
ELABORATE_TIMEOUT_S = 300
PREDICT_TIMEOUT_S = 600      # a prediction reads a whole repo cold; local-llm-ui timed out at 300

STEPS_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "why": {"type": "string"},
                "depends_on": {"type": "array", "items": {"type": "string"}},
                "kind": {"type": "string", "enum": ["goal", "thread", "repair", "revive", "assess", "human"]},
                "check": {"type": "string"},
            },
            "required": ["id", "title", "why", "depends_on", "kind", "check"]}}},
    "required": ["steps"],
}

# YOUR HANDS. Some steps no dispatch can finish: a credential from a
# dashboard, an approval that is Apple's or Google's, a payment, a
# signature, a decision. Sent an agent, they cost the cap and come back
# "blocked". They are a kind of their own — kept apart, never dispatched,
# ticked by the owner — and an agent's `blocked_on` becomes one at runtime.
_HUMAN_RE = re.compile(
    r"\b(owner (supplies|provides|decides|signs|pays|approves|creates|must)|your hands|"
    r"by hand|manually|dashboard[- ]only|no api path|apple('s)? (review|approval)|"
    r"app store review|app review|play console approval|approved by (apple|google)|"
    r"subscriptions? approved|password|passcode|2fa|otp|credit card|payment method|"
    r"sign (the|a) |legal|contract|business verification|verify (the )?business|"
    r"system user token|pixel id|app secret|api key from|token from)\b", re.I)


def is_human(text: str) -> bool:
    return bool(_HUMAN_RE.search(text or ""))


_SUBJ_STOP = {"the", "and", "for", "with", "from", "into", "that", "this", "owner", "supplies",
              "approved", "live", "done", "set", "made", "first", "new", "via", "env", "deploy"}


def _share_subject(a: str, b: str) -> bool:
    """Two milestone lines share a subject word (4+ letters, not filler)."""
    ta = {w for w in re.findall(r"[a-z]{4,}", (a or "").lower()) if w not in _SUBJ_STOP}
    tb = {w for w in re.findall(r"[a-z]{4,}", (b or "").lower()) if w not in _SUBJ_STOP}
    return bool(ta & tb)


IDEAS_SCHEMA = {
    "type": "object",
    "properties": {
        "ideas": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "why": {"type": "string"},
                "check": {"type": "string"},
                "kind": {"type": "string", "enum": ["goal", "thread", "repair", "revive", "assess"]},
            },
            "required": ["title", "why", "check", "kind"]}}},
    "required": ["ideas"],
}


# Every planner is read-only and under dontAsk — which denies every tool not
# NAMED. Bash programs alone left Read denied: the first prediction pass
# returned 0 milestones for 12 of 13 repos, "successfully". Named now.
PLANNER_ALLOWED = ("Read Glob Grep Bash(git:*) Bash(ls:*) Bash(cat:*) Bash(head:*) Bash(tail:*) "
                   "Bash(grep:*) Bash(rg:*) Bash(find:*) Bash(wc:*) Bash(pwd:*)")
PLANNER_DENIED = "Edit Write MultiEdit NotebookEdit Bash(git commit:*) Bash(git push:*) Bash(rm:*) Bash(mv:*)"
# Measured 2026-09-04 on purangpt-next: a prediction hit a $0.60 cap in 4
# turns (the first turn alone loads ~26k cache tokens, then README + git
# log on a large repo). Reading a repo costs more than reading a goal file.
PLANNER_BUDGET_USD = 0.80          # ideas: a goal file and a glance at the repo
ELABORATE_BUDGET_USD_REAL = 1.00   # steps: one milestone against the repo
PREDICT_BUDGET_USD = 1.50          # predictions: the whole repo, cold


def _planner_result(stdout: str, key: str) -> List[Dict[str, Any]]:
    """The structured list from a planner's result line — or an error that
    says WHY there is none. A budget cut or a refused start used to come
    back as [] and read as 'the planner found nothing'."""
    for ln in reversed(stdout.strip().splitlines()):
        if ln.startswith("{") and '"type"' in ln:
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if d.get("type") != "result":
                continue
            sub = d.get("subtype") or ""
            so = d.get("structured_output") or {}
            items = [x for x in (so.get(key) or []) if isinstance(x, dict) and x.get("title")]
            if not items and sub != "success":
                raise RuntimeError("planner ended %s at $%.2f, %s turns, %d denials"
                                   % (sub, d.get("total_cost_usd") or 0, d.get("num_turns"),
                                      len(d.get("permission_denials") or [])))
            if not items and d.get("permission_denials"):
                raise RuntimeError("planner returned nothing after %d tool denials (%s)"
                                   % (len(d["permission_denials"]),
                                      ", ".join(sorted({x.get("tool_name", "?") for x in d["permission_denials"]}))))
            return items
    raise RuntimeError("planner returned no result object")


def ideate_with_claude(goal: str, done: List[str], opens: List[str], cwd: str = "",
                       title: str = "", note: str = "") -> List[Dict[str, Any]]:
    """Ask a read-only planner what the goal file does NOT yet say.

    The first plan only expanded what was written — "it's not generating new
    ideas". This proposes up to three milestones the goal would need next,
    each with a why and a check. They land as ideas: shown, never run until
    the owner accepts one.
    """
    prompt = (
        "You are proposing, not doing. Goal: %s.\nMilestones already done: %s.\n"
        "Milestones still open: %s.\nLook at the repo in the current directory "
        "(read-only) and propose up to 3 NEW milestones that are not in either list "
        "and would move this goal furthest — concrete, one sentence each, with a "
        "`why` (what it unlocks or what breaks without it) and a `check` (how "
        "you would prove it done). Skip anything already listed. Return the "
        "ideas object.%s"
        % (title or goal, "; ".join(done[:12]) or "none", "; ".join(opens[:12]) or "none",
           ("\nContext: " + note[:600]) if note else ""))
    argv = [_claude(), "-p", prompt, "--model", "sonnet", "--output-format", "json",
            "--permission-mode", "dontAsk", "--max-budget-usd", str(PLANNER_BUDGET_USD),
            "--json-schema", json.dumps(IDEAS_SCHEMA),
            "--allowedTools", PLANNER_ALLOWED, "--disallowedTools", PLANNER_DENIED]
    r = subprocess.run(argv, cwd=cwd if cwd and os.path.isdir(cwd) else os.path.expanduser("~"),
                       capture_output=True, text=True, timeout=ELABORATE_TIMEOUT_S,
                       stdin=subprocess.DEVNULL)
    return _planner_result(r.stdout, "ideas")


def _claude() -> str:
    try:
        import go as _go
        return _go.claude_bin()
    except Exception:
        return "claude"


PREDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "milestones": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "why": {"type": "string"},
                "check": {"type": "string"},
                "size": {"type": "string", "enum": ["S", "M", "L"]},
            },
            "required": ["title", "why", "check", "size"]}}},
    "required": ["milestones"],
}
PREDICTIONS_NAME = "predictions.json"


def predict_with_claude(project: str, path: str, sha: str,
                        goal: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Read-only planner in the project's own repo: the next 3-6 milestones
    it needs, in order, each with a why, a check and a size. Goal or not."""
    gl_txt = ""
    if goal:
        gl_txt = ("\nThis project has a goal file '%s': done = %s; open = %s. Predict what comes "
                  "AFTER the open ones; do not repeat either list."
                  % (goal.get("title") or goal.get("name"),
                     "; ".join(goal.get("done_titles") or [])[:600] or "none",
                     "; ".join(goal.get("open_titles") or [])[:600] or "none"))
    prompt = (
        "You are predicting, not doing. Project: %s (repo in the current directory, at commit %s).\n"
        "Read the README, the last 20 commits, any TODO/ROADMAP, and the test state (read-only). "
        "Then predict the next 3 to 6 milestones this project needs to reach what it is "
        "evidently for, in order — concrete, one sentence each, with `why` (what it unlocks or "
        "what breaks without it), `check` (how you would prove it done), and `size` S/M/L. "
        "Prefer what the code and the commit trail imply over generic advice. Return the "
        "milestones object.%s" % (project, sha[:9], gl_txt))
    argv = [_claude(), "-p", prompt, "--model", "sonnet", "--output-format", "json",
            "--permission-mode", "dontAsk", "--max-budget-usd", str(PREDICT_BUDGET_USD),
            "--json-schema", json.dumps(PREDICT_SCHEMA),
            "--allowedTools", PLANNER_ALLOWED, "--disallowedTools", PLANNER_DENIED]
    r = subprocess.run(argv, cwd=path, capture_output=True, text=True,
                       timeout=PREDICT_TIMEOUT_S, stdin=subprocess.DEVNULL)
    return _planner_result(r.stdout, "milestones")


def load_predictions(meditation_dir: str = MEDITATION_DIR) -> Dict[str, Any]:
    try:
        return json.load(open(os.path.join(meditation_dir, PREDICTIONS_NAME)))
    except (OSError, ValueError):
        return {}


def _save_predictions(pr: Dict[str, Any], meditation_dir: str) -> None:
    os.makedirs(meditation_dir, exist_ok=True)
    tmp = os.path.join(meditation_dir, PREDICTIONS_NAME + ".tmp")
    with open(tmp, "w") as f:
        json.dump(pr, f, indent=1)
    os.replace(tmp, os.path.join(meditation_dir, PREDICTIONS_NAME))


def _head_sha(path: str) -> str:
    try:
        return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _goal_for_project(project: str, path: str, goals_dir: Optional[str]) -> Optional[Dict[str, Any]]:
    import goals as gl
    try:
        rows = gl.scan(goals_dir) if goals_dir else gl.scan()
    except Exception:
        return None
    best = None
    for g in rows:
        gc = (g.get("cwd") or "").rstrip("/")
        if (g.get("project") or "") == project or (gc and path and (path.rstrip("/") == gc or path.startswith(gc + "/"))):
            if best is None or len(gc) > len(best.get("cwd") or ""):
                best = g
    if not best:
        return None
    ms = best.get("milestones") or []
    return {"name": best["name"], "title": best.get("title"), "cwd": best.get("cwd"),
            "done_titles": [(m.get("headline") or m.get("text") or "") for m in ms if m.get("done")],
            "open_titles": [(m.get("headline") or m.get("text") or "") for m in ms if not m.get("done")],
            "file": best.get("file")}


def predict(repos: Optional[Dict[str, str]] = None, goals_dir: Optional[str] = None,
            meditation_dir: str = MEDITATION_DIR, predictor: Optional[Callable] = None,
            fresh: bool = False, active_days: Optional[int] = 30, limit: int = 20,
            ledger: Optional[str] = None) -> Dict[str, Any]:
    """Predicted milestones for every project, on its own. Keyed on the
    repo's HEAD: a project that has not moved is not re-predicted (a full
    pass is a planner call per project). Discarded titles never return."""
    import goals as gl
    predictor = predictor or predict_with_claude
    if repos is None:
        import projects as _pj
        repos = dict(_pj._repo_dirs())
        if active_days is not None:
            recent = {}
            try:
                for r in _pj.rollup():
                    recent[r.get("project")] = r.get("last_touched_days")
            except Exception:
                recent = {}
            repos = {k: v for k, v in repos.items()
                     if recent.get(k) is not None and recent[k] <= active_days}
    gone = gl.discarded_names(ledger)
    pr = load_predictions(meditation_dir)
    out = {"predicted": [], "cached": [], "skipped": [], "failed": []}
    for project, path in list(repos.items())[:limit]:
        if not path or not os.path.isdir(path):
            out["skipped"].append(project)
            continue
        sha = _head_sha(path)
        cur = pr.get(project) or {}
        # a cached EMPTY result is not a result — the broken first pass left
        # 12 repos at 0 milestones and the second pass "cached" all 12
        if not fresh and cur.get("sha") == sha and cur.get("milestones"):
            out["cached"].append(project)
            continue
        goal = _goal_for_project(project, path, goals_dir)
        try:
            ms = predictor(project, path, sha, goal)
        except Exception as e:
            out["failed"].append("%s: %s" % (project, str(e)[:80]))
            pr[project] = {"sha": sha, "ts": _now_iso(), "path": path,
                           "goal": goal["name"] if goal else None, "milestones": cur.get("milestones") or [],
                           "error": str(e)[:160]}
            _save_predictions(pr, meditation_dir)
            continue
        keep = []
        for m in ms[:6]:
            t_ = str(m.get("title") or "").strip()
            if not t_ or ("predict:%s:%s" % (project, _nid(t_))) in gone:
                continue
            keep.append({"title": t_, "why": str(m.get("why") or "").strip(),
                         "check": str(m.get("check") or "").strip(),
                         "size": m.get("size") if m.get("size") in ("S", "M", "L") else "M"})
        pr[project] = {"sha": sha, "ts": _now_iso(), "path": path,
                       "goal": goal["name"] if goal else None, "milestones": keep}
        out["predicted"].append(project)
        # save per project: a pass over 13 repos is up to an hour of planner
        # calls, and nothing reached the page until the last one finished
        _save_predictions(pr, meditation_dir)
    _save_predictions(pr, meditation_dir)
    return out


def accept_predicted(project: str, title: str, meditation_dir: str = MEDITATION_DIR,
                     goals_dir: Optional[str] = None) -> Dict[str, Any]:
    """A predicted milestone becomes a real one: appended to the project's
    goal file, or a `<project>-next` goal is written for a project without
    one. The next plan turns it into steps."""
    import goals as gl
    pr = load_predictions(meditation_dir)
    ent = pr.get(project)
    if not ent:
        return {"ok": False, "why": "no predictions for %s" % project}
    m = next((x for x in ent["milestones"] if x["title"] == title), None)
    if not m:
        return {"ok": False, "why": "no such predicted milestone"}
    gdir = goals_dir or gl.GOALS_DIR
    goal = _goal_for_project(project, ent.get("path", ""), goals_dir)
    line = "- [ ] %s <!-- predicted %s: %s; check: %s -->\n" % (
        m["title"], time.strftime("%Y-%m-%d"), m.get("why", "")[:120].replace("-->", ""),
        m.get("check", "")[:120].replace("-->", ""))
    if goal and goal.get("file") and os.path.exists(goal["file"]):
        txt = open(goal["file"], errors="replace").read()
        if "## Milestones" in txt:
            head, _, rest = txt.partition("## Milestones\n")
            lines = rest.split("\n")
            i = 0
            while i < len(lines) and (lines[i].startswith("- [") or not lines[i].strip() and i + 1 < len(lines) and lines[i + 1].startswith("- [")):
                i += 1
            lines.insert(i, line.rstrip("\n"))
            txt = head + "## Milestones\n" + "\n".join(lines)
        else:
            txt = txt.rstrip("\n") + "\n\n## Milestones\n" + line
        open(goal["file"], "w").write(txt)
        target = goal["name"]
    else:
        name = re.sub(r"[^a-z0-9-]+", "-", project.lower()).strip("-") + "-next"
        path = os.path.join(gdir, name + ".md")
        if not os.path.exists(path):
            os.makedirs(gdir, exist_ok=True)
            open(path, "w").write("---\nname: %s\ntitle: %s — what comes next\nproject: %s\ncwd: %s\nstatus: active\n---\n"
                                  "## Milestones\n%s\n## Note\nWritten from the twin's predicted milestones on %s. "
                                  "Edit freely; the checkboxes are the measurement.\n"
                                  % (name, project, project, ent.get("path") or os.path.expanduser("~"), line,
                                     time.strftime("%Y-%m-%d")))
        else:
            open(path, "a").write(line)
        target = name
    ent["milestones"] = [x for x in ent["milestones"] if x["title"] != title]
    ent.setdefault("accepted", []).append({"title": title, "ts": _now_iso(), "goal": target})
    _save_predictions(pr, meditation_dir)
    return {"ok": True, "goal": target, "title": title}


def discard_predicted(project: str, title: str, meditation_dir: str = MEDITATION_DIR,
                      ledger: Optional[str] = None, reason: str = "") -> Dict[str, Any]:
    import goals as gl
    pr = load_predictions(meditation_dir)
    ent = pr.get(project)
    if not ent:
        return {"ok": False, "why": "no predictions for %s" % project}
    before = len(ent["milestones"])
    ent["milestones"] = [x for x in ent["milestones"] if x["title"] != title]
    if len(ent["milestones"]) == before:
        return {"ok": False, "why": "no such predicted milestone"}
    gl._ledger_write(ledger, {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                              "name": "predict:%s:%s" % (project, _nid(title)), "kind": "predicted",
                              "project": project, "title": title, "reason": reason})
    _save_predictions(pr, meditation_dir)
    return {"ok": True, "project": project, "title": title}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def _nid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# who runs a node, under what cap
# ---------------------------------------------------------------------------

def agent_for(kind: str) -> Dict[str, Any]:
    """models.pick() for the model and effort, budget_for() for the cap.
    Both say their basis; the fallback says it is one."""
    try:
        import models
        p = models.pick(kind)
        b = models.budget_for(kind)
        return {"model": p.get("model", "sonnet"), "effort": p.get("effort") or "high",
                "basis": p.get("basis", "default"), "why": p.get("why", ""),
                "budget_usd": float(b.get("usd") or 2.0), "cap_basis": b.get("basis", "default")}
    except Exception as e:
        return {"model": "sonnet", "effort": "high", "basis": "fallback",
                "why": "policy unreadable: %s" % str(e)[:60],
                "budget_usd": 2.0, "cap_basis": "fallback"}


# ---------------------------------------------------------------------------
# elaboration: one milestone -> ordered sub-steps
# ---------------------------------------------------------------------------

def elaborate_with_claude(goal: str, milestone: str, cwd: str = "",
                          title: str = "", note: str = "") -> List[Dict[str, Any]]:
    """Ask a read-only planner to break one milestone into steps.

    Synchronous on purpose: planning is the phase the owner reads before
    saying go, so it finishes before the page is written. Read-only tools,
    a small cap, one milestone per call.
    """
    prompt = (
        "You are planning, not doing. Goal: %s. Open milestone: %s.\n"
        "Look at the repo in the current directory (read-only: git log, the "
        "tree, tests) and break this milestone into the smallest ordered "
        "steps an agent could take one at a time, each with a `check` — the "
        "command or observation that proves the step is done. 2 to 6 steps. "
        "depends_on names earlier step ids. kind is 'goal' for steps that "
        "change code, 'assess' for steps that only look. Do not do any of "
        "the steps. Return the steps object.%s"
        % (title or goal, milestone, ("\nContext: " + note[:600]) if note else ""))
    argv = [_claude(), "-p", prompt, "--model", "sonnet", "--output-format", "json",
            "--permission-mode", "dontAsk", "--max-budget-usd", str(ELABORATE_BUDGET_USD_REAL),
            "--json-schema", json.dumps(STEPS_SCHEMA),
            "--allowedTools", PLANNER_ALLOWED, "--disallowedTools", PLANNER_DENIED]
    r = subprocess.run(argv, cwd=cwd if cwd and os.path.isdir(cwd) else os.path.expanduser("~"),
                       capture_output=True, text=True, timeout=ELABORATE_TIMEOUT_S,
                       stdin=subprocess.DEVNULL)
    return [x for x in _planner_result(r.stdout, "steps") if x.get("id")]


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build(goals_dir: Optional[str] = None, meditation_dir: str = MEDITATION_DIR,
          elaborator: Optional[Callable[..., List[Dict[str, Any]]]] = None,
          goals_rows: Optional[List[Dict[str, Any]]] = None,
          elaborate: bool = True,
          ideator: Optional[Callable[..., List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    """The graph, from the goal files, the elaborator and the ideator. Never
    typed. Ideas are proposed for EVERY goal — a goal at 100% is exactly
    where the next milestones are missing — and run only once accepted."""
    import goals as gl
    rows = goals_rows if goals_rows is not None else (
        gl.scan(goals_dir) if goals_dir else gl.scan())
    if elaborator is None and elaborate:
        elaborator = elaborate_with_claude
    # The real ideator rides ONLY with the real elaborator. Defaulting it on
    # `elaborate` alone made every test that injected a fake elaborator call
    # the real planner — 13 tests, real money, a five-minute hang.
    if ideator is None and elaborator is elaborate_with_claude:
        ideator = ideate_with_claude
    nodes: List[Dict[str, Any]] = []
    notes: List[str] = []
    n_goals = 0
    n_ideas = 0
    for g in rows:
        if (g.get("status") or "") in ("archived", "paused"):
            continue
        opens = [m for m in (g.get("milestones") or []) if not m.get("done")]
        dones = [m for m in (g.get("milestones") or []) if m.get("done")]
        n_goals += 1
        prev_id: Optional[str] = None        # the last AGENT milestone
        trailing_human: List[Dict[str, Any]] = []   # human items since it
        for m in opens:
            text = (m.get("text") or "").strip()
            # the headline, not the raw line: a milestone reads "iOS subs
            # approved — **CORRECTED 2026-08-25: was NOT a queue wait…" in
            # the file, and the note is history, not the step
            head = (m.get("headline") or text).strip() or text
            mid = _nid(g["name"], text)
            human = is_human(text)
            # File order is the author's dependency between agent steps.
            # Through a HUMAN item it holds only when the two lines share a
            # subject word: "Pixel activated" after "Owner supplies the Pixel
            # ID" is real; "Android sign-in repaired" after "iOS subscriptions
            # approved" was a false gate that left the whole run with nothing
            # ready (measured 2026-09-04: ready set empty, 3 items waiting).
            deps0 = [prev_id] if prev_id else []
            for hn in trailing_human:
                if _share_subject(hn["title"], head):
                    deps0.append(hn["id"])
            node = {"id": mid, "goal": g["name"], "goal_title": g.get("title") or g["name"],
                    "cwd": g.get("cwd") or "", "milestone": head, "title": head,
                    "why": ("only you can do this" if human else
                            "an open milestone of " + (g.get("title") or g["name"])),
                    "kind": "human" if human else "goal", "check": "",
                    "depends_on": deps0,
                    "status": "waiting" if human else "pending",
                    "agent": ({"model": "you", "effort": "", "basis": "human", "budget_usd": 0.0,
                               "cap_basis": "none", "why": "not dispatchable"} if human
                              else agent_for("goal")),
                    "name": "%s-%s-%s" % ("human" if human else "goal", g["name"][:16], mid),
                    "steers": [], "log": "", "session": "", "result": None}
            steps: List[Dict[str, Any]] = []
            if elaborator is not None and not human:
                try:
                    steps = elaborator(g["name"], text) \
                        if (elaborator is not elaborate_with_claude and not getattr(elaborator, "wants_context", False)) \
                        else elaborator(g["name"], text if getattr(elaborator, "wants_context", False) else head,
                                        cwd=g.get("cwd") or "", title=g.get("title") or "", note=g.get("note") or "")
                except Exception as e:
                    notes.append("elaboration failed for %s / %s: %s"
                                 % (g["name"], text[:50], str(e)[:80]))
                    steps = []
                if not steps and elaborator is elaborate_with_claude:
                    # a planner that returns nothing is not a planner that
                    # succeeded — say so, or the milestone reads as atomic
                    notes.append("planner returned no steps for %s / %s — the milestone "
                                 "stands as one step" % (g["name"], head[:50]))
            ids: Dict[str, str] = {}
            for s in steps:
                ids[str(s["id"])] = mid + "." + str(s["id"])
            subs: List[Dict[str, Any]] = []
            for s in steps:
                kind = s.get("kind") if s.get("kind") in ("goal", "thread", "repair", "revive", "assess", "human") else "goal"
                if kind != "human" and is_human(str(s.get("title") or "") + " " + str(s.get("why") or "")):
                    kind = "human"
                sid = ids[str(s["id"])]
                deps = [ids[d] for d in (s.get("depends_on") or []) if d in ids] + list(deps0)
                subs.append({"id": sid, "goal": g["name"], "goal_title": node["goal_title"],
                             "cwd": node["cwd"], "milestone": head, "title": str(s["title"]).strip(),
                             "why": str(s.get("why") or "").strip(), "kind": kind,
                             "check": str(s.get("check") or "").strip(), "depends_on": deps,
                             "status": "waiting" if kind == "human" else "pending",
                             "agent": ({"model": "you", "effort": "", "basis": "human", "budget_usd": 0.0,
                                        "cap_basis": "none", "why": "not dispatchable"}
                                       if kind == "human" else agent_for(kind)),
                             "name": "%s-%s-%s" % (kind, g["name"][:16], sid.replace(".", "_")),
                             "steers": [], "log": "", "session": "", "result": None})
            node["depends_on"] = node["depends_on"] + [s["id"] for s in subs]
            nodes.extend(subs)
            nodes.append(node)
            if human:
                trailing_human.append(node)
            else:
                prev_id = node["id"]
                trailing_human = []
        # IDEAS — what the file does not say yet. Proposed, shown, and run
        # only once the owner accepts one; an accepted idea runs after the
        # goal's last open milestone.
        if ideator is not None:
            try:
                dn = [(m.get("headline") or m.get("text") or "").strip() for m in dones]
                op = [(m.get("headline") or m.get("text") or "").strip() for m in opens]
                ideas = ideator(g["name"], dn, op) if ideator is not ideate_with_claude \
                    else ideator(g["name"], dn, op, cwd=g.get("cwd") or "",
                                 title=g.get("title") or "", note=g.get("note") or "")
            except Exception as e:
                notes.append("ideas failed for %s: %s" % (g["name"], str(e)[:80]))
                ideas = []
            if not ideas and ideator is ideate_with_claude:
                notes.append("planner proposed no ideas for %s" % g["name"])
            for idea in ideas[:3]:
                t_ = str(idea.get("title") or "").strip()
                if not t_ or any(n["goal"] == g["name"] and n["title"].lower() == t_.lower() for n in nodes):
                    continue
                kind = idea.get("kind") if idea.get("kind") in ("goal", "thread", "repair", "revive", "assess") else "goal"
                iid = _nid(g["name"], "idea", t_)
                nodes.append({"id": iid, "goal": g["name"], "goal_title": g.get("title") or g["name"],
                              "cwd": g.get("cwd") or "", "milestone": t_, "title": t_,
                              "why": str(idea.get("why") or "").strip(), "kind": kind,
                              "check": str(idea.get("check") or "").strip(),
                              "depends_on": [prev_id] if prev_id else [],
                              "status": "idea", "agent": agent_for(kind),
                              "name": "%s-%s-%s" % (kind, g["name"][:16], iid),
                              "steers": [], "log": "", "session": "", "result": None,
                              "idea": True})
                n_ideas += 1
    # PROPOSED GOALS — work the memories carry that no goal file does. Not
    # nodes; a list the owner accepts from, one goal file each.
    proposed: List[Dict[str, Any]] = []
    try:
        # session evidence costs a full transcript scan; only the real plan
        # pays it. Fakes and the fast plan still get every candidate.
        real = elaborator is elaborate_with_claude
        proposed = gl.mine(goals_dir=goals_dir or gl.GOALS_DIR,
                           sessions=None if real else [])
    except Exception as e:
        notes.append("goal mining failed: %s" % str(e)[:80])
    est = round(sum(n["agent"]["budget_usd"] for n in nodes if n["status"] not in ("idea", "waiting")), 2)
    return {"id": time.strftime("%Y%m%d-%H%M%S", time.gmtime()), "created": _now_iso(),
            "armed": False, "armed_at": "", "paused_why": "", "max_parallel": DEFAULT_PARALLEL,
            "nodes": nodes, "notes": notes, "proposed_goals": proposed,
            # every goal file, steps or not — a finished goal has no row in
            # the graph and still needs its discard button
            "goals": [{"name": g["name"], "title": g.get("title") or g["name"],
                       "done": int(g.get("done") or 0), "total": int(g.get("total") or 0),
                       "status": g.get("status") or ""} for g in rows],
            "totals": {"goals": n_goals, "nodes": len([n for n in nodes if n["status"] != "idea"]),
                       "ideas": n_ideas, "human": len([n for n in nodes if n["kind"] == "human"]),
                       "est_usd": est},
            "events": [{"ts": _now_iso(), "what": "planned", "nodes": len(nodes)}],
            "metrics": {}}


def ready(g: Dict[str, Any]) -> List[Dict[str, Any]]:
    done = {n["id"] for n in g["nodes"] if n["status"] == "done"}
    return [n for n in g["nodes"] if n["status"] == "pending" and n.get("kind") != "human"
            and all(d in done for d in n["depends_on"])]


def waiting_on_you(g: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [n for n in g["nodes"] if n.get("kind") == "human" and n["status"] == "waiting"]


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def _state_path(meditation_dir: str) -> str:
    return os.path.join(meditation_dir, STATE_NAME)


class _locked:
    """One writer at a time on campaign.json: the brain's 5-minute tick, the
    hourly pass, the console's done/steer and a re-plan all load-mutate-
    save it. Held for a load→save only — never across a planner call."""

    def __init__(self, meditation_dir: str):
        self.dir = meditation_dir
        self.fh = None

    def __enter__(self):
        import fcntl
        try:
            os.makedirs(self.dir, exist_ok=True)
            self.fh = open(os.path.join(self.dir, "campaign.json.lock"), "w")
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        except OSError:
            self.fh = None
        return self

    def __exit__(self, *a):
        if self.fh is not None:
            import fcntl
            try:
                fcntl.flock(self.fh, fcntl.LOCK_UN)
            finally:
                self.fh.close()
        return False


def save(g: Dict[str, Any], meditation_dir: str = MEDITATION_DIR) -> str:
    os.makedirs(meditation_dir, exist_ok=True)
    p = _state_path(meditation_dir)
    # a NEW campaign archives the old one; the same campaign overwrites
    if os.path.exists(p):
        try:
            old = json.load(open(p))
            if old.get("id") and old.get("id") != g.get("id"):
                arch = os.path.join(meditation_dir, "campaigns")
                os.makedirs(arch, exist_ok=True)
                os.replace(p, os.path.join(arch, old["id"] + ".json"))
        except (OSError, ValueError):
            pass
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(g, f, indent=1)
    os.replace(tmp, p)
    try:
        with open(os.path.join(meditation_dir, PAGE_NAME), "w") as f:
            f.write(render(g))
    except OSError:
        pass
    return p


def load(meditation_dir: str = MEDITATION_DIR) -> Optional[Dict[str, Any]]:
    try:
        return json.load(open(_state_path(meditation_dir)))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# dispatch, real and injected
# ---------------------------------------------------------------------------

def _guarded(path: str) -> bool:
    return bool(path) and path.startswith(os.path.expanduser("~/.claude") + os.sep)


def _prior_findings(n: Dict[str, Any]) -> str:
    """What the previous attempt at this node established — its RESULT's
    `did`, `blocked_on` and `next` — so a fresh agent starts from the wall,
    not from zero. Three runs ($7.42) designed one fix; the fourth writes it."""
    r = n.get("result") or {}
    if not r.get("did"):
        return ""
    out = ["A previous agent on this exact step reported (past tense, verified by its own log):"]
    out += ["  - " + str(x)[:400] for x in r.get("did", [])[:8]]
    if r.get("blocked_on"):
        out.append("It stopped on: " + str(r["blocked_on"])[:300] + " — that block has been cleared by the owner.")
    if r.get("next"):
        out.append("It said the next step was: " + str(r["next"])[:400])
    return "\n".join(out)


def _prompt_for(n: Dict[str, Any]) -> str:
    lines = ["Goal: %s." % n["goal_title"],
             "Milestone: %s." % n["milestone"]]
    if n["title"] != n["milestone"]:
        lines.append("This step: %s." % n["title"])
    if n.get("why"):
        lines.append("Why: %s" % n["why"])
    if n.get("check"):
        lines.append("Done means: %s" % n["check"])
    lines.append("You are one node of a planned run across every goal; other agents "
                 "hold the other steps, each in its own worktree. Do this step only, "
                 "prove it with the check, and put the single next step in `next`.")
    for s in n.get("steers") or []:
        lines.append("Owner's steer: %s" % s.get("message", ""))
    pf = _prior_findings(n)
    if pf:
        lines.append(pf)
    return "\n".join(lines)


def dispatch_real(n: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    import go
    a = n["agent"]
    # Never in place. A goal whose cwd is not a repo cannot be isolated, and
    # the checkouts under it belong to whoever is typing in them; go.run
    # already refuses this unattended — the campaign must too.
    cwd = n.get("cwd") or ""
    if not go._repo_top(cwd):
        n["why_failed"] = ("cannot isolate: cwd %s is not a git repo — set cwd to the repo "
                           "in the goal file" % (cwd or "(none)"))
        return None
    prior = _prior_findings(n)
    if n.get("session") and n.get("resume_message") and not _guarded(n.get("worktree", "")):
        # the wall the agent hit has been cleared by the owner: the SAME
        # session continues with that fact, instead of a fresh agent
        # rediscovering everything up to the wall
        r = go.continue_agent(n["name"], n["resume_message"],
                              budget_usd=float(a.get("budget_usd") or 0))
        if r.get("started"):
            return {"log": r.get("log", ""), "session": r.get("session", n["session"]),
                    "worktree": n.get("worktree", ""), "why": "resumed"}
        return None
    if n.get("session") and _guarded(n.get("worktree", "")):
        # the session is bound to a worktree under ~/.claude, where writes
        # are denied; a fresh agent takes over WITH what the last one found
        n["session"] = ""
        n["resume_message"] = ""
        n["handed_over"] = True
    ok = go._headless(n.get("cwd") or os.path.expanduser("~"), _prompt_for(n), n["name"],
                      a.get("model", "sonnet"), a.get("effort", ""), float(a.get("budget_usd") or 0))
    if not ok:
        return None
    last = getattr(go._headless, "last", {}) or {}
    return {"log": last.get("log", ""), "session": last.get("session", ""),
            "worktree": last.get("worktree", ""), "why": last.get("why", "")}


def read_result_real(log: str) -> Optional[Dict[str, Any]]:
    if not log or not os.path.exists(log):
        return None
    try:
        import models
        return models._result_line(open(log, errors="replace").read())
    except Exception:
        return None


def median_duration_by_kind(ledger: Optional[str]) -> Dict[str, float]:
    """kind -> median seconds of its SUCCESSFUL runs in spend.jsonl."""
    byk: Dict[str, List[float]] = {}
    if not ledger or not os.path.exists(ledger):
        return {}
    try:
        with open(ledger, errors="replace") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("subtype") not in (None, "success"):
                    continue
                d = float(r.get("duration_ms") or 0) / 1000.0
                if d <= 0:
                    continue
                kind = (r.get("name") or "").split("-", 1)[0]
                if kind:
                    byk.setdefault(kind, []).append(d)
    except OSError:
        return {}
    out: Dict[str, float] = {}
    for k, v in byk.items():
        v.sort()
        out[k] = v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2.0
    return out


def wall_bound_s(kind: str, medians: Dict[str, float]) -> float:
    med = medians.get(kind)
    if not med:
        return float(WALL_DEFAULT_S)
    return float(min(WALL_MAX_S, max(WALL_MIN_S, WALL_FACTOR * med)))


def _death_line_real(log: str) -> str:
    """The CLI refusing to start leaves one non-event line as the whole
    body ("claude: No such file or directory", "No conversation found…").
    That line, or "" for a run that is streaming or has not spoken yet."""
    if not log or not os.path.exists(log):
        return ""
    try:
        if time.time() - os.path.getmtime(log) < 120:
            return ""
        body = [ln for ln in open(log, errors="replace").read(4000).splitlines()
                if ln.strip() and not ln.startswith("#")]
    except OSError:
        return ""
    first = body[0].strip() if body else ""
    return first[:160] if first and not first.startswith("{") else ""


def _kill_real(n: Dict[str, Any]) -> bool:
    """Stop the run's whole process group (caffeinate + claude)."""
    import signal
    try:
        import go
        pid = int((go._head(n.get("log", "")).get("pid") or "0").strip() or 0)
    except Exception:
        pid = 0
    if not pid:
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except (OSError, ProcessLookupError):
            return False


def _wall_node(n: Dict[str, Any], g: Dict[str, Any], text: str, why: str) -> Dict[str, Any]:
    """A human node raised on behalf of an agent's node; the node waits on it."""
    wall = next((m for m in g["nodes"] if m.get("kind") == "human" and m["goal"] == n["goal"]
                 and m["title"].strip().lower() == text.strip().lower()), None)
    if wall is None:
        wid = n["id"] + ".you"
        while any(m["id"] == wid for m in g["nodes"]):
            wid += "x"
        wall = {"id": wid, "goal": n["goal"], "goal_title": n["goal_title"], "cwd": n["cwd"],
                "milestone": n["milestone"], "title": text, "why": why,
                "kind": "human", "check": "", "depends_on": [], "status": "waiting",
                "agent": {"model": "you", "effort": "", "basis": "human", "budget_usd": 0.0,
                          "cap_basis": "none", "why": "not dispatchable"},
                "name": "human-%s-%s" % (n["goal"][:16], wid.replace(".", "_")),
                "steers": [], "log": "", "session": "", "result": None, "from_agent": n["id"]}
        g["nodes"].append(wall)
    if wall["id"] not in n["depends_on"]:
        n["depends_on"].append(wall["id"])
    return wall


def _stop_run(n: Dict[str, Any], g: Dict[str, Any], why: str, keep_session: bool = True) -> None:
    """A run stopped without a RESULT: once, it resumes with the fact; twice,
    the owner decides. Attempts count stops, not dispatches."""
    n["attempts"] = int(n.get("attempts") or 0) + 1
    n["stuck"] = False
    n["stopped_why"] = why[:200]
    g["events"].append({"ts": _now_iso(), "what": "stopped", "node": n["id"],
                        "why": why[:120], "attempt": n["attempts"]})
    if not keep_session:
        n["session"] = ""
        n["resume_message"] = ""
    if n["attempts"] >= MAX_ATTEMPTS:
        _wall_node(n, g, "Agent stopped twice on '%s' without a result (last: %s). Split the step, "
                         "raise its cap, or drop it." % (n["title"][:60], why[:80]),
                   "two runs ended without a RESULT; a third would be the same run again")
        n["status"] = "pending"
        n["resume_message"] = ""
        return
    n["status"] = "pending"
    if keep_session and n.get("session"):
        n["resume_message"] = ("Your previous run was stopped: %s — it produced no RESULT. Finish the "
                               "smallest shippable piece now: commit, push when green, and end with "
                               "the RESULT object." % why[:120])


def _log_mtime_real(log: str) -> Optional[float]:
    try:
        return os.path.getmtime(log)
    except OSError:
        return None


def _dispatch_ready(g: Dict[str, Any], max_parallel: int,
                    dispatch: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
                    now: Optional[float] = None) -> List[str]:
    running = sum(1 for n in g["nodes"] if n["status"] == "running")
    sent: List[str] = []
    for n in ready(g):
        if running >= max_parallel:
            break
        r = dispatch(n)
        if not r:
            n["status"] = "failed"
            n["why_failed"] = "dispatch refused"
            continue
        n["status"] = "running"
        n["log"] = r.get("log", "")
        n["session"] = r.get("session", "") or n.get("session", "")
        n["worktree"] = r.get("worktree", "") or n.get("worktree", "")
        if n.get("resume_message"):
            n["resumed_with"] = n.pop("resume_message")
        n["started"] = _now_iso()
        n["started_epoch"] = now if now is not None else time.time()
        running += 1
        sent.append(n["id"])
        g["events"].append({"ts": _now_iso(), "what": "dispatched", "node": n["id"],
                            "title": n["title"][:60], "model": n["agent"]["model"]})
    return sent


def parse_until(text: str, now: Optional[float] = None) -> Optional[float]:
    """'21:00' → today's 21:00 local (tomorrow's if already past); a number is an epoch."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        v = float(text)
        return v if v > 1e9 else None
    except ValueError:
        pass
    m = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if not m:
        return None
    t = now if now is not None else time.time()
    lt = time.localtime(t)
    target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(m.group(1)), int(m.group(2)), 0,
                          lt.tm_wday, lt.tm_yday, lt.tm_isdst))
    if target <= t:
        target += 86400
    return target


def go(meditation_dir: str = MEDITATION_DIR, max_parallel: Optional[int] = None,
       dispatch: Optional[Callable] = None, until: Optional[float] = None,
       now: Optional[Callable[[], float]] = None) -> Dict[str, Any]:
    with _locked(meditation_dir):
        return _go(meditation_dir=meditation_dir, max_parallel=max_parallel, dispatch=dispatch, until=until, now=now)


def _go(meditation_dir: str = MEDITATION_DIR, max_parallel: Optional[int] = None,
       dispatch: Optional[Callable] = None, until: Optional[float] = None,
       now: Optional[Callable[[], float]] = None) -> Dict[str, Any]:
    """The owner's go. Arms the campaign and sends the first ready wave.
    `until` (epoch) is the deadline: past it nothing new is sent, and once
    nothing is running the campaign closes out with a written summary."""
    g = load(meditation_dir)
    if not g:
        return {"armed": False, "why": "no campaign planned — run `campaign plan` first"}
    t = (now or time.time)()
    g["armed"] = True
    g["armed_at"] = _now_iso()
    g["armed_epoch"] = t
    g["paused_why"] = ""
    g["hold_until"] = 0
    if until:
        g["until_epoch"] = float(until)
        g["until"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(until)))
    else:
        g.pop("until_epoch", None)
        g.pop("until", None)
    if max_parallel:
        g["max_parallel"] = int(max_parallel)
    g["events"].append({"ts": _now_iso(), "what": "go", "until": g.get("until", "")})
    sent = _dispatch_ready(g, g.get("max_parallel") or DEFAULT_PARALLEL,
                           dispatch or dispatch_real, now=t)
    g["metrics"] = _metrics(g, t, os.path.join(meditation_dir, "spend.jsonl"))
    save(g, meditation_dir)
    return {"armed": True, "dispatched": sent, "metrics": g["metrics"], "until": g.get("until", "")}


def summarize(g: Dict[str, Any], ledger: Optional[str] = None) -> str:
    """The report at the end of a run: what shipped, what stopped, what is
    yours, what it cost — from results, never from the plan."""
    m = _metrics(g, time.time(), ledger)
    ns = [n for n in g["nodes"] if n["status"] != "idea"]
    out = ["CLAUD-E run %s — summary" % g.get("id", ""),
           "armed %s%s · %.1f h · %d of %d steps done · $%.2f spent"
           % (str(g.get("armed_at", ""))[:16], (" until " + g["until"]) if g.get("until") else "",
              m.get("hours") or 0.0, m["done"], m["nodes"], m["spent_usd"]), ""]
    out.append("SHIPPED")
    shipped = [n for n in ns if n["status"] == "done" and n.get("result")]
    if not shipped:
        out.append("  nothing finished with a result")
    for n in shipped:
        r = n["result"]
        out.append("  - %s › %s" % (n["goal_title"][:40], n["title"][:70]))
        if r.get("verified_commits"):
            out.append("      commits verified: %s%s" % (", ".join(c[:9] for c in r["verified_commits"][:6]),
                                                       " · pushed" if r.get("pushed") else " · NOT pushed"))
        elif r.get("commits"):
            out.append("      commits claimed, none verified: %s" % ", ".join(c[:9] for c in r["commits"][:4]))
        else:
            out.append("      no commits")
        for d in (r.get("did") or [])[:4]:
            out.append("      · %s" % str(d)[:110])
        if r.get("tests"):
            out.append("      tests: %s" % json.dumps(r["tests"])[:80])
    out.append("")
    out.append("STOPPED / NEEDS A DECISION")
    stopped = [n for n in ns if n["status"] in ("failed",) or n.get("stopped_why")]
    for n in stopped:
        out.append("  - %s › %s: %s" % (n["goal_title"][:30], n["title"][:60],
                                       (n.get("why_failed") or n.get("stopped_why") or "")[:100]))
    if not stopped:
        out.append("  none")
    out.append("")
    out.append("YOUR HANDS (open)")
    hands = [n for n in ns if n.get("kind") == "human" and n["status"] == "waiting"]
    for n in hands:
        out.append("  - %s" % n["title"][:110])
    if not hands:
        out.append("  nothing waits on you")
    out.append("")
    out.append("STILL TO RUN")
    todo = [n for n in ns if n["status"] in ("pending",) and n.get("kind") != "human"]
    for n in todo[:12]:
        out.append("  - %s › %s" % (n["goal_title"][:30], n["title"][:70]))
    if not todo:
        out.append("  nothing pending")
    out.append("")
    out.append("SPEND  $%.2f · %d commits verified (%d claimed) · %d pushed · %d denials · %d stops"
               % (m["spent_usd"], m["verified_commits"], m["claimed_commits"], m["pushed"], m["denials"],
                  sum(1 for e in g.get("events") or [] if e.get("what") == "stopped")))
    return "\n".join(out)


def close_out(g: Dict[str, Any], meditation_dir: str, t: float, mailer: Optional[Callable] = None) -> str:
    """The deadline passed and nothing runs: disarm, write the summary, mail it."""
    ledger = os.path.join(meditation_dir, "spend.jsonl")
    text = summarize(g, ledger)
    g["armed"] = False
    g["paused_why"] = "deadline %s reached — summary written" % (g.get("until") or time.strftime("%H:%M", time.localtime(t)))
    g["closing"] = False
    g["summary"] = text
    g["summary_at"] = _now_iso()
    try:
        with open(os.path.join(meditation_dir, "campaign-summary.md"), "w") as f:
            f.write(text + "\n")
    except OSError:
        pass
    g["events"].append({"ts": _now_iso(), "what": "closed", "why": g["paused_why"]})
    if mailer is None:
        try:
            import mail as _mail
            mailer = _mail.send_summary
        except Exception:
            mailer = None
    if mailer is not None:
        try:
            mailer("CLAUD-E run summary — %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(t)), text)
        except Exception as e:
            g["events"].append({"ts": _now_iso(), "what": "mail failed", "why": str(e)[:100]})
    return text


_CARRY = ("status", "session", "log", "worktree", "result", "spend_by_run", "spent_usd", "attempts",
          "steers", "resumed_with", "resume_message", "started", "started_epoch", "finished",
          "blocked_on", "stuck", "handed_over", "why_failed", "done_by", "note", "grown",
          "stopped_why", "agent")


def _steps_from_old(old: Dict[str, Any], mid: str) -> List[Dict[str, Any]]:
    """The sub-steps a milestone already has, in planner shape, so a re-plan
    does not pay the planner for what it planned last time."""
    subs = [n for n in old["nodes"] if n["id"].startswith(mid + ".") and not n.get("from_agent")
            and n["id"] != mid + ".next" and n["status"] != "idea"]
    out = []
    for n in subs:
        sid = n["id"][len(mid) + 1:]
        out.append({"id": sid, "title": n["title"], "why": n.get("why", ""),
                    "depends_on": [d[len(mid) + 1:] for d in n.get("depends_on") or []
                                   if d.startswith(mid + ".") and d != mid + ".next"],
                    "kind": n.get("kind", "goal"), "check": n.get("check", "")})
    return out


def replan(goals_dir: Optional[str] = None, meditation_dir: str = MEDITATION_DIR,
           elaborator: Optional[Callable] = None) -> Dict[str, Any]:
    """Re-read the goal files without losing the run. Node ids are
    sha1(goal, milestone), so every node the new graph shares with the old
    keeps its state; walls raised by agents and steps grown from results
    stay attached to their parents; milestones ticked in the file leave;
    milestones already elaborated keep their sub-steps unpaid."""
    old = load(meditation_dir)
    if not old:
        g = build(goals_dir=goals_dir, meditation_dir=meditation_dir, elaborator=elaborator)
        save(g, meditation_dir)
        return {"ok": True, "carried": 0, "new": len(g["nodes"]), "id": g["id"]}
    real = elaborator if elaborator is not None else elaborate_with_claude
    import goals as gl
    rows = gl.scan(goals_dir) if goals_dir else gl.scan()
    known = {n["id"] for n in old["nodes"]}

    def cached(goal: str, milestone: str, **kw):
        mid = _nid(goal, milestone)
        if mid in known:
            return _steps_from_old(old, mid)
        if real is elaborate_with_claude:
            return real(goal, milestone, **kw)
        return real(goal, milestone)
    cached.wants_context = True          # build() passes cwd/title/note
    new = build(goals_dir=goals_dir, meditation_dir=meditation_dir, elaborator=cached,
                goals_rows=rows, ideator=None)
    with _locked(meditation_dir):
        # the planners took minutes; merge against the file AS IT IS NOW,
        # not as it was when they started — the brain ticked meanwhile
        old = load(meditation_dir) or old
        return _merge_and_save(old, new, meditation_dir)


def _merge_and_save(old: Dict[str, Any], new: Dict[str, Any], meditation_dir: str) -> Dict[str, Any]:
    om = {n["id"]: n for n in old["nodes"]}
    carried = 0
    for n in new["nodes"]:
        o = om.get(n["id"])
        if not o:
            continue
        for k in _CARRY:
            if k in o:
                n[k] = o[k]
        # dependencies the old node gained at run time (walls, grown steps)
        for d in o.get("depends_on") or []:
            if d not in n["depends_on"] and (d in om) and (om[d].get("from_agent") or om[d].get("grown")):
                n["depends_on"].append(d)
        carried += 1
    new_ids = {n["id"] for n in new["nodes"]}
    for o in old["nodes"]:
        if o["id"] in new_ids:
            continue
        parent = o.get("from_agent") or (o["id"][:-5] if o["id"].endswith(".next") else "")
        if (o.get("from_agent") or o.get("grown") or o["status"] == "idea") and (parent in new_ids or o["status"] == "idea"):
            new["nodes"].append(o)
            new_ids.add(o["id"])
    for k in ("id", "created", "armed", "armed_at", "armed_epoch", "max_parallel", "events",
              "paused_why", "until", "until_epoch", "hold_until", "closing", "summary", "summary_at"):
        if k in old:
            new[k] = old[k]
    fresh = sum(1 for n in new["nodes"] if n["id"] not in om)
    new["events"].append({"ts": _now_iso(), "what": "replanned", "carried": carried, "new": fresh})
    new["metrics"] = _metrics(new, time.time(), os.path.join(meditation_dir, "spend.jsonl"))
    save(new, meditation_dir)
    return {"ok": True, "carried": carried, "new": fresh, "id": new["id"], "notes": new.get("notes", [])}


def pause(meditation_dir: str = MEDITATION_DIR, why: str = "") -> Dict[str, Any]:
    with _locked(meditation_dir):
        return _pause(meditation_dir=meditation_dir, why=why)


def _pause(meditation_dir: str = MEDITATION_DIR, why: str = "") -> Dict[str, Any]:
    g = load(meditation_dir)
    if not g:
        return {"armed": False, "why": "no campaign"}
    g["armed"] = False
    g["paused_why"] = why or "paused"
    g["events"].append({"ts": _now_iso(), "what": "paused", "why": why})
    save(g, meditation_dir)
    return {"armed": False, "why": g["paused_why"]}


# ---------------------------------------------------------------------------
# the monitor
# ---------------------------------------------------------------------------

def _verified_commits(n: Dict[str, Any], commits: List[str]) -> List[str]:
    where = n.get("worktree") or n.get("cwd") or ""
    out: List[str] = []
    if not where or not os.path.isdir(where):
        return out
    for sha in commits:
        if not isinstance(sha, str) or not sha.strip():
            continue
        try:
            r = subprocess.run(["git", "-C", where, "cat-file", "-e", sha.strip() + "^{commit}"],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                out.append(sha.strip())
        except (OSError, subprocess.TimeoutExpired):
            pass
    return out


def _absorb(n: Dict[str, Any], res: Dict[str, Any], g: Dict[str, Any],
            now: Optional[float] = None) -> None:
    """A finished node: record what it did, and grow the graph by its next."""
    so = res.get("structured_output")
    so = so if isinstance(so, dict) else {}
    commits = [c for c in (so.get("commits") or []) if isinstance(c, str)]
    n["result"] = {"cost_usd": float(res.get("total_cost_usd") or 0),
                   "turns": res.get("num_turns"), "subtype": res.get("subtype"),
                   "is_error": bool(res.get("is_error")),
                   "denials": len(res.get("permission_denials") or []),
                   "did": so.get("did") or [], "commits": commits,
                   "verified_commits": _verified_commits(n, commits),
                   "pushed": bool(so.get("pushed")), "milestone_ticked": so.get("milestone_ticked"),
                   "tests": so.get("tests"), "blocked_on": so.get("blocked_on"),
                   "next": (so.get("next") or "").strip()}
    n["finished"] = _now_iso()
    n["stuck"] = False
    if res.get("is_error") and _LIMIT_RE.search(json.dumps(res)[:6000] if isinstance(res, dict) else ""):
        # not this node's fault: hold the campaign, keep the session
        g["hold_until"] = (now or time.time()) + HOLD_S
        n["status"] = "pending"
        n["resume_message"] = "The previous run stopped on a usage limit. Continue from where you stopped."
        g["events"].append({"ts": _now_iso(), "what": "held", "node": n["id"],
                            "why": "usage limit — holding %d min" % (HOLD_S // 60)})
        return
    # Spend across runs. Every run's total is its own — a resumed run's
    # out_tokens and cache_read are smaller than the run it continued, so
    # they are per-invocation figures and total_cost_usd comes from the same
    # modelUsage. Runs ADD, same session or not. The status line read
    # "$3.83 spent" after five runs worth $14.10.
    hist = n.setdefault("spend_by_run", {})
    hist[n.get("log") or _now_iso()] = float(res.get("total_cost_usd") or 0)
    n["spent_usd"] = round(sum(hist.values()), 4)
    if res.get("is_error") or (res.get("subtype") not in (None, "success") and not so):
        n["status"] = "failed"
        n["why_failed"] = str(res.get("subtype") or "error")
        g["events"].append({"ts": _now_iso(), "what": "failed", "node": n["id"], "why": n["why_failed"]})
        return
    if so.get("blocked_on"):
        # The agent found the wall; the wall is the owner's. It becomes a
        # human node this node waits on, and this node keeps its session so
        # it can continue from the wall, not from the start.
        wall_text = str(so["blocked_on"]).strip()[:300]
        n["blocked_on"] = wall_text
        _wall_node(n, g, wall_text,
                   "the agent working \"%s\" hit this and only you can clear it" % n["title"][:50])
        n["status"] = "pending"
        n["resume_message"] = ""
        g["events"].append({"ts": _now_iso(), "what": "blocked", "node": n["id"], "why": wall_text[:80]})
        return
    n["status"] = "done"
    g["events"].append({"ts": _now_iso(), "what": "done", "node": n["id"],
                        "commits": len(n["result"]["verified_commits"])})
    nxt = n["result"]["next"]
    if nxt and nxt.lower().strip(". ") not in ("", "none", "nothing", "done", "n/a"):
        same_goal = [m for m in g["nodes"] if m["goal"] == n["goal"]]
        if not any(m["title"].strip().lower() == nxt.lower() for m in same_goal):
            nid = n["id"] + ".next"
            while any(m["id"] == nid for m in g["nodes"]):
                nid += "x"
            g["nodes"].append({"id": nid, "goal": n["goal"], "goal_title": n["goal_title"],
                               "cwd": n["cwd"], "milestone": n["milestone"], "title": nxt[:200],
                               "why": "named as the next step by the agent that finished %s" % n["title"][:40],
                               "kind": n["kind"], "check": "", "depends_on": [n["id"]],
                               "status": "pending", "agent": agent_for(n["kind"]),
                               "name": "%s-%s-%s" % (n["kind"], n["goal"][:16], nid.replace(".", "_")),
                               "steers": [], "log": "", "session": "", "result": None,
                               "grown": True})
            g["events"].append({"ts": _now_iso(), "what": "grew", "node": nid, "title": nxt[:60]})


def _ledger_by_name(ledger: Optional[str]) -> Dict[str, Dict[str, float]]:
    """name -> {log: cost} from spend.jsonl; a log counts once."""
    out: Dict[str, Dict[str, float]] = {}
    if not ledger or not os.path.exists(ledger):
        return out
    try:
        with open(ledger, errors="replace") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("name") and r.get("log"):
                    out.setdefault(r["name"], {})[r["log"]] = float(r.get("cost_usd") or 0)
    except OSError:
        pass
    return out


def _metrics(g: Dict[str, Any], now: float, ledger: Optional[str] = None) -> Dict[str, Any]:
    # ideas are proposals, not steps: they do not count as nodes, pending
    # or per-goal totals until accepted. The status line read "0 of 25
    # done" over 13 steps the day ideas landed.
    ideas = [n for n in g["nodes"] if n["status"] == "idea"]
    ns = [n for n in g["nodes"] if n["status"] != "idea"]
    human_waiting = [n for n in ns if n.get("kind") == "human" and n["status"] == "waiting"]
    by = {}
    for n in ns:
        by[n["status"]] = by.get(n["status"], 0) + 1
    fin = [n for n in ns if n.get("result")]
    # The ledger (spend.jsonl, keyed by the node's name, one row per log)
    # outranks what the node remembers: the daemon that absorbed the first
    # shipped node ran code older than the sum-of-runs fix and kept only
    # its last run's $3.83 of $14.10. Never less than the node's own figure.
    led = _ledger_by_name(ledger)
    spent = 0.0
    for n in ns:
        own = n.get("spent_usd")
        if own is None:
            own = n["result"]["cost_usd"] if n.get("result") else 0.0
        spent += max(float(own or 0), sum(led.get(n.get("name") or "", {}).values()))
    spent = round(spent, 4)
    per_goal: Dict[str, Dict[str, int]] = {}
    for n in ns:
        pg = per_goal.setdefault(n["goal"], {"title": n["goal_title"], "done": 0, "total": 0,
                                             "blocked": 0, "running": 0})
        pg["total"] += 1
        if n["status"] == "done":
            pg["done"] += 1
        elif n["status"] == "blocked":
            pg["blocked"] += 1
        elif n["status"] == "running":
            pg["running"] += 1
    hours = (now - g["armed_epoch"]) / 3600.0 if g.get("armed_epoch") else 0.0
    return {"nodes": len(ns), "done": by.get("done", 0), "running": by.get("running", 0),
            "blocked": by.get("blocked", 0), "failed": by.get("failed", 0),
            "pending": by.get("pending", 0), "ready": len(ready(g)),
            "stuck": sum(1 for n in ns if n.get("stuck")),
            "spent_usd": spent, "est_usd": g["totals"]["est_usd"],
            "burn_usd_per_h": round(spent / hours, 2) if hours > 0.05 else None,
            "pushed": sum(1 for n in fin if n["result"]["pushed"]),
            "verified_commits": sum(len(n["result"]["verified_commits"]) for n in fin),
            "claimed_commits": sum(len(n["result"]["commits"]) for n in fin),
            "milestones_ticked": sum(1 for n in fin if n["result"]["milestone_ticked"]),
            "denials": sum(n["result"]["denials"] for n in fin),
            "grown": sum(1 for n in ns if n.get("grown")),
            "steers": sum(len(n.get("steers") or []) for n in ns),
            "ideas": len(ideas), "human": len(human_waiting),
            "per_goal": per_goal, "hours": round(hours, 2)}


def tick(meditation_dir: str = MEDITATION_DIR, dispatch: Optional[Callable] = None,
         read_result: Optional[Callable] = None, log_mtime: Optional[Callable] = None,
         now: Optional[Callable[[], float]] = None,
         max_parallel: Optional[int] = None, death: Optional[Callable] = None,
         kill: Optional[Callable] = None, medians: Optional[Dict[str, float]] = None,
         mailer: Optional[Callable] = None) -> Dict[str, Any]:
    with _locked(meditation_dir):
        return _tick(meditation_dir=meditation_dir, dispatch=dispatch, read_result=read_result, log_mtime=log_mtime, now=now, max_parallel=max_parallel, death=death, kill=kill, medians=medians, mailer=mailer)


def _tick(meditation_dir: str = MEDITATION_DIR, dispatch: Optional[Callable] = None,
         read_result: Optional[Callable] = None, log_mtime: Optional[Callable] = None,
         now: Optional[Callable[[], float]] = None,
         max_parallel: Optional[int] = None, death: Optional[Callable] = None,
         kill: Optional[Callable] = None, medians: Optional[Dict[str, float]] = None,
         mailer: Optional[Callable] = None) -> Dict[str, Any]:
    """Advance the campaign one step. The heartbeat calls this every pass."""
    g = load(meditation_dir)
    if not g:
        return {"armed": False, "why": "no campaign", "metrics": {}}
    now_f = now or time.time
    read_result = read_result or read_result_real
    log_mtime = log_mtime or _log_mtime_real
    death = death or _death_line_real
    kill = kill or _kill_real
    ledger = os.path.join(meditation_dir, "spend.jsonl")
    if medians is None:
        medians = median_duration_by_kind(ledger)
    t = now_f()
    for n in g["nodes"]:
        if n["status"] != "running":
            continue
        res = read_result(n.get("log", ""))
        if res:
            _absorb(n, res, g, now=t)
            continue
        dead = death(n.get("log", ""))
        if dead:
            _stop_run(n, g, "died at start: " + dead, keep_session=False)
            continue
        mt = log_mtime(n.get("log", "")) if n.get("log") else None
        last = mt if mt else n.get("started_epoch") or t
        elapsed = t - float(n.get("started_epoch") or t)
        bound = wall_bound_s(n.get("kind") or "goal", medians)
        if elapsed > bound or (t - last) > STALL_S:
            why = ("ran %d min, the bound for a %s run is %d min" % (elapsed // 60, n.get("kind") or "goal", bound // 60)
                   if elapsed > bound else "log unmoved for %d min" % ((t - last) // 60))
            gone = kill(n)
            _stop_run(n, g, why + (" (stopped)" if gone else " (process already gone)"))
            continue
        n["stuck"] = False
    sent: List[str] = []
    deadline = float(g.get("until_epoch") or 0)
    held = float(g.get("hold_until") or 0) > t
    past = bool(deadline) and t >= deadline
    if g.get("armed") and not g.get("paused_why") and not held and not past:
        sent = _dispatch_ready(g, max_parallel or g.get("max_parallel") or DEFAULT_PARALLEL,
                               dispatch or dispatch_real, now=t)
    if g.get("armed") and past:
        if any(n["status"] == "running" for n in g["nodes"]):
            if not g.get("closing"):
                g["closing"] = True
                g["events"].append({"ts": _now_iso(), "what": "deadline", "why": "nothing new; waiting for running agents"})
        else:
            close_out(g, meditation_dir, t, mailer=mailer)
    g["metrics"] = _metrics(g, t, ledger)
    g["last_tick"] = _now_iso()
    save(g, meditation_dir)
    return {"armed": bool(g.get("armed")), "dispatched": sent, "metrics": g["metrics"],
            "held": held, "past_deadline": past}


def steer(node_id: str, message: str, meditation_dir: str = MEDITATION_DIR,
          continue_fn: Optional[Callable] = None) -> Dict[str, Any]:
    with _locked(meditation_dir):
        return _steer(node_id=node_id, message=message, meditation_dir=meditation_dir, continue_fn=continue_fn)


def _steer(node_id: str, message: str, meditation_dir: str = MEDITATION_DIR,
          continue_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """A correction mid-flight: continue the node's own session with the
    owner's message. Works on running, blocked, failed and done nodes."""
    g = load(meditation_dir)
    if not g:
        return {"ok": False, "why": "no campaign"}
    n = next((m for m in g["nodes"] if m["id"] == node_id or m["name"] == node_id), None)
    if not n:
        return {"ok": False, "why": "no node %s" % node_id}
    if continue_fn is None:
        import go as _go
        cap = float((n.get("agent") or {}).get("budget_usd") or 0)
        continue_fn = lambda name, msg: _go.continue_agent(name, msg, budget_usd=cap)
    r = continue_fn(n["name"], message) or {}
    if not r.get("started"):
        return {"ok": False, "why": r.get("why", "could not continue")}
    n.setdefault("steers", []).append({"ts": _now_iso(), "message": message, "log": r.get("log", "")})
    n["status"] = "running"
    n["stuck"] = False
    n["log"] = r.get("log") or n.get("log", "")
    n["started_epoch"] = time.time()
    g["events"].append({"ts": _now_iso(), "what": "steered", "node": n["id"], "message": message[:80]})
    save(g, meditation_dir)
    return {"ok": True, "node": n["id"], "log": n["log"]}


def done(node_id: str, meditation_dir: str = MEDITATION_DIR, note: str = "",
         goals_dir: Optional[str] = None, tick_fn: Optional[Callable] = None) -> Dict[str, Any]:
    with _locked(meditation_dir):
        return _done(node_id=node_id, meditation_dir=meditation_dir, note=note, goals_dir=goals_dir, tick_fn=tick_fn)


def _done(node_id: str, meditation_dir: str = MEDITATION_DIR, note: str = "",
         goals_dir: Optional[str] = None, tick_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """The owner did the thing. The node is done by him, the goal file's
    checkbox ticks when the node IS a milestone, and every node that was
    waiting on it gets the fact as its resume message."""
    g = load(meditation_dir)
    if not g:
        return {"ok": False, "why": "no campaign"}
    n = next((m for m in g["nodes"] if m["id"] == node_id or m["name"] == node_id), None)
    if not n:
        return {"ok": False, "why": "no node %s" % node_id}
    if n.get("kind") != "human":
        return {"ok": False, "why": "%s is an agent's step, not yours — steer it instead" % node_id}
    n["status"] = "done"
    n["done_by"] = "owner"
    n["note"] = note
    n["finished"] = _now_iso()
    ticked = None
    if n["title"] == n["milestone"] and not n.get("from_agent"):
        try:
            import goals as gl
            fn = tick_fn or gl.tick
            r = fn(n["goal"], n["milestone"], goals_dir=goals_dir) if goals_dir else fn(n["goal"], n["milestone"])
            ticked = r.get("closed") if isinstance(r, dict) else None
        except Exception as e:
            ticked = "tick failed: %s" % str(e)[:60]
    for m in g["nodes"]:
        if n["id"] in m["depends_on"] and m.get("session"):
            m["resume_message"] = ("The owner has done this: %s.%s Continue from where you stopped."
                                   % (n["title"], (" Note: " + note) if note else ""))
    g["events"].append({"ts": _now_iso(), "what": "owner did", "node": n["id"],
                        "title": n["title"][:60], "ticked": ticked})
    g["metrics"] = _metrics(g, time.time(), os.path.join(meditation_dir, "spend.jsonl"))
    save(g, meditation_dir)
    return {"ok": True, "node": n["id"], "ticked": ticked,
            "unblocked": [m["id"] for m in g["nodes"] if n["id"] in m["depends_on"]]}


def accept(node_id: str, meditation_dir: str = MEDITATION_DIR) -> Dict[str, Any]:
    with _locked(meditation_dir):
        return _accept(node_id=node_id, meditation_dir=meditation_dir)


def _accept(node_id: str, meditation_dir: str = MEDITATION_DIR) -> Dict[str, Any]:
    """The owner takes an idea: it becomes a pending node and runs after the
    goal's last open milestone. Nothing else in the graph changes."""
    g = load(meditation_dir)
    if not g:
        return {"ok": False, "why": "no campaign"}
    n = next((m for m in g["nodes"] if m["id"] == node_id), None)
    if not n:
        return {"ok": False, "why": "no node %s" % node_id}
    if n["status"] != "idea":
        return {"ok": False, "why": "%s is not an idea (status %s)" % (node_id, n["status"])}
    n["status"] = "pending"
    n["accepted"] = _now_iso()
    g["totals"]["nodes"] = len([m for m in g["nodes"] if m["status"] != "idea"])
    g["totals"]["ideas"] = len([m for m in g["nodes"] if m["status"] == "idea"])
    g["totals"]["est_usd"] = round(sum(m["agent"]["budget_usd"] for m in g["nodes"]
                                       if m["status"] != "idea"), 2)
    g["events"].append({"ts": _now_iso(), "what": "accepted", "node": n["id"], "title": n["title"][:60]})
    save(g, meditation_dir)
    return {"ok": True, "node": n["id"]}


def accept_goal(name: str, meditation_dir: str = MEDITATION_DIR,
                goals_dir: Optional[str] = None) -> Dict[str, Any]:
    """The owner takes a mined goal: its file is written; the next plan
    picks it up. The campaign records it so the proposal disappears."""
    import goals as gl
    g = load(meditation_dir)
    if not g:
        return {"ok": False, "why": "no campaign"}
    cand = next((c for c in g.get("proposed_goals", []) if c["name"] == name), None)
    if not cand:
        return {"ok": False, "why": "no proposed goal %s" % name}
    r = gl.accept_mined(cand, goals_dir=goals_dir or gl.GOALS_DIR)
    if not r.get("ok"):
        return r
    g["proposed_goals"] = [c for c in g["proposed_goals"] if c["name"] != name]
    g["events"].append({"ts": _now_iso(), "what": "goal accepted", "goal": name, "path": r["path"]})
    g.setdefault("notes", []).append("goal %s written — re-plan to bring its milestones in" % name)
    save(g, meditation_dir)
    return {"ok": True, "path": r["path"], "name": name}


def discard_proposed(name: str, meditation_dir: str = MEDITATION_DIR, reason: str = "") -> Dict[str, Any]:
    """No to a proposed goal: goals.discard_mined moves its memory aside and
    ledgers the name; it leaves the plan and never comes back."""
    import goals as gl
    g = load(meditation_dir)
    if not g:
        return {"ok": False, "why": "no campaign"}
    cand = next((c for c in g.get("proposed_goals", []) if c["name"] == name), None)
    if not cand:
        return {"ok": False, "why": "no proposed goal %s" % name}
    r = gl.discard_mined(cand, reason=reason)
    if not r.get("ok"):
        return r
    g["proposed_goals"] = [c for c in g["proposed_goals"] if c["name"] != name]
    g["events"].append({"ts": _now_iso(), "what": "proposed goal discarded", "goal": name,
                        "moved": r.get("moved", ""), "tombstoned": r.get("tombstoned", 0)})
    save(g, meditation_dir)
    return r


def discard_goal(name: str, meditation_dir: str = MEDITATION_DIR, reason: str = "",
                 goals_dir: Optional[str] = None) -> Dict[str, Any]:
    """No to a goal: its file and the memories it cites move aside; every
    node, idea and human item of that goal leaves the plan."""
    import goals as gl
    r = gl.discard_goal(name, goals_dir=goals_dir or gl.GOALS_DIR, reason=reason)
    if not r.get("ok"):
        return r
    g = load(meditation_dir)
    if g:
        before = len(g["nodes"])
        g["nodes"] = [n for n in g["nodes"] if n["goal"] != name]
        g["totals"]["nodes"] = len([n for n in g["nodes"] if n["status"] != "idea"])
        g["totals"]["ideas"] = len([n for n in g["nodes"] if n["status"] == "idea"])
        g["totals"]["human"] = len([n for n in g["nodes"] if n.get("kind") == "human"])
        g["totals"]["goals"] = len({n["goal"] for n in g["nodes"]})
        g["totals"]["est_usd"] = round(sum(n["agent"]["budget_usd"] for n in g["nodes"]
                                           if n["status"] not in ("idea", "waiting")), 2)
        g["events"].append({"ts": _now_iso(), "what": "goal discarded", "goal": name,
                            "nodes_removed": before - len(g["nodes"]),
                            "memories_moved": len(r.get("memories_moved", []))})
        g["metrics"] = _metrics(g, time.time(), os.path.join(meditation_dir, "spend.jsonl"))
        save(g, meditation_dir)
        r["nodes_removed"] = before - len(g["nodes"])
    return r


def status(meditation_dir: str = MEDITATION_DIR) -> Dict[str, Any]:
    g = load(meditation_dir)
    if not g:
        return {"id": "", "armed": False, "metrics": {}, "nodes": [], "why": "no campaign"}
    return {"id": g["id"], "armed": g.get("armed", False), "paused_why": g.get("paused_why", ""),
            "until": g.get("until", ""), "until_epoch": g.get("until_epoch"), "hold_until": g.get("hold_until") or 0,
            "closing": bool(g.get("closing")), "summary_at": g.get("summary_at", ""),
            "created": g.get("created"), "armed_at": g.get("armed_at", ""),
            "metrics": _metrics(g, time.time(), os.path.join(meditation_dir, "spend.jsonl")), "notes": g.get("notes", []),
            "proposed_goals": g.get("proposed_goals", []),
            "goals": g.get("goals", []),
            "predictions": load_predictions(meditation_dir),
            "nodes": [{k: n.get(k) for k in ("id", "goal", "goal_title", "title", "kind", "status",
                                              "depends_on", "agent", "blocked_on", "stuck",
                                              "result", "steers", "grown", "name", "log",
                                              "idea", "accepted", "why", "check", "done_by",
                                              "note", "from_agent", "milestone")}
                      for n in g["nodes"]],
            "events": g.get("events", [])[-30:]}


# ---------------------------------------------------------------------------
# the pages
# ---------------------------------------------------------------------------

_GLYPH = {"done": "✓", "running": "▶", "blocked": "■", "failed": "✗", "pending": "·", "idea": "?",
          "waiting": "☐"}


def render(g: Dict[str, Any], predictions: Optional[Dict[str, Any]] = None) -> str:
    """The page the owner reads before saying go. Plain words, every step,
    who runs it, what it costs at most."""
    out = ["ALL GOALS — the plan", ""]
    t = g["totals"]
    out.append("%d steps across %d goals · up to $%.2f · %d at a time · %d ideas proposed"
               % (t["nodes"], t["goals"], t["est_usd"], g.get("max_parallel") or DEFAULT_PARALLEL,
                  t.get("ideas", 0)))
    if g.get("armed"):
        m = g.get("metrics") or {}
        out.append("RUNNING since %s · %d done · %d running · %d blocked · $%.2f spent"
                   % (g.get("armed_at", "")[:16], m.get("done", 0), m.get("running", 0),
                      m.get("blocked", 0), m.get("spent_usd", 0)))
    elif g.get("paused_why"):
        out.append("PAUSED — " + g["paused_why"])
    out.append("")
    yours = waiting_on_you(g)
    if yours:
        out.append("YOUR HANDS — %d thing%s only you can do; nothing behind them moves until you tick them"
                   % (len(yours), "" if len(yours) == 1 else "s"))
        for n in yours:
            out.append("  ☐ %s   (%s)" % (n["title"][:100], n["goal_title"][:40]))
            if n.get("why"):
                out.append("      " + n["why"][:110])
            out.append("      done? — campaign done %s" % n["id"])
        out.append("")
    goals_seen: List[str] = []
    for n in g["nodes"]:
        if n["goal"] not in goals_seen:
            goals_seen.append(n["goal"])
    for goal in goals_seen:
        ns = [n for n in g["nodes"] if n["goal"] == goal and n["status"] != "idea"]
        ideas = [n for n in g["nodes"] if n["goal"] == goal and n["status"] == "idea"]
        out.append((ns or ideas)[0]["goal_title"] + ("" if ns else "  (every milestone done)"))
        for n in ns:
            a = n["agent"]
            dep = ""
            if n["depends_on"]:
                titles = [m["title"] for m in g["nodes"] if m["id"] in n["depends_on"]]
                dep = " — after: " + "; ".join(x[:30] for x in titles[:3])
            line = "  %s %s%s" % (_GLYPH.get(n["status"], "·"), n["title"][:90], dep)
            out.append(line)
            out.append("      %s at %s [%s] · cap $%.2f · %s" % (
                a["model"], a["effort"], a["basis"], a["budget_usd"], n["kind"]))
            if n.get("check"):
                out.append("      done means: " + n["check"][:100])
            if n["status"] == "blocked":
                out.append("      BLOCKED: " + (n.get("blocked_on") or "")[:120])
            if n.get("stuck"):
                out.append("      STUCK: no result and the log has not moved in %d minutes" % (STALL_S // 60))
            if n.get("result") and n["result"].get("did"):
                out.append("      did: " + "; ".join(str(x)[:60] for x in n["result"]["did"][:3]))
        for n in ideas:
            out.append("  ? IDEA: %s" % n["title"][:90])
            if n.get("why"):
                out.append("      why: " + n["why"][:110])
            if n.get("check"):
                out.append("      done means: " + n["check"][:100])
            out.append("      not in the plan until you accept it — accept with: campaign accept %s" % n["id"])
        out.append("")
    pr = predictions if predictions is not None else {}
    if any(v.get("milestones") for v in pr.values()):
        out.append("PREDICTED MILESTONES — what each project needs next, read from its own repo; "
                   "not a step until you accept it")
        for proj, ent in sorted(pr.items()):
            if not ent.get("milestones"):
                continue
            out.append("  %s%s (at %s)" % (proj, (" → goal " + ent["goal"]) if ent.get("goal") else "",
                                           (ent.get("sha") or "")[:9]))
            for m in ent["milestones"]:
                out.append("    ? [%s] %s" % (m.get("size", "M"), m["title"][:90]))
                if m.get("why"):
                    out.append("        why: " + m["why"][:100])
            out.append("        accept with: campaign accept-predicted %s \"<title>\"" % proj)
        out.append("")
    if g.get("proposed_goals"):
        out.append("PROPOSED GOALS — work your memories carry that no goal file does")
        for c in g["proposed_goals"]:
            ev = c.get("evidence") or {}
            out.append("  ? %s" % c["title"][:90])
            out.append("      from %s · %d sessions in 30 days%s%s" % (
                os.path.basename(ev.get("memory", "?")), int(ev.get("sessions_30d") or 0),
                (" · last " + ev["last_active"]) if ev.get("last_active") else "",
                " · open" if ev.get("open_signal") else ""))
            for m_ in (c.get("suggested_milestones") or [])[:3]:
                out.append("      - [ ] " + m_[:100])
            out.append("      accept with: campaign accept-goal %s" % c["name"])
        out.append("")
    for note in g.get("notes", []):
        out.append("note — " + note)
    if not g.get("armed"):
        out.append("")
        out.append("Nothing runs until you say go. Then each ready step is sent under its cap, "
                   "every finished step names the next one, and this page updates each pass.")
    return "\n".join(out)


def render_status(s: Dict[str, Any]) -> str:
    m = s.get("metrics") or {}
    if not m:
        return "no campaign planned"
    lines = ["campaign %s · %s" % (s.get("id", ""), "ARMED" if s.get("armed") else
                                   ("PAUSED — " + s["paused_why"]) if s.get("paused_why") else "waiting for go"),
             "%d of %d done · %d running · %d ready · %d blocked · %d failed · %d stuck"
             % (m["done"], m["nodes"], m["running"], m["ready"], m["blocked"], m["failed"], m["stuck"]),
             "$%.2f spent of up to $%.2f%s · %d pushed · %d commits verified (%d claimed) · %d milestones ticked"
             % (m["spent_usd"], m["est_usd"],
                (" · $%.2f/h" % m["burn_usd_per_h"]) if m.get("burn_usd_per_h") is not None else "",
                m["pushed"], m["verified_commits"], m["claimed_commits"], m["milestones_ticked"]),
             "%d steps grown from results · %d steers · %d denials · %d ideas waiting for you · %d things only you can do"
             % (m["grown"], m["steers"], m["denials"], m.get("ideas", 0), m.get("human", 0))]
    for gname, pg in (m.get("per_goal") or {}).items():
        lines.append("  %-40s %d/%d%s%s" % (pg["title"][:40], pg["done"], pg["total"],
                                            (" · %d blocked" % pg["blocked"]) if pg["blocked"] else "",
                                            (" · %d running" % pg["running"]) if pg["running"] else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate campaign", description=__doc__.split("\n")[0])
    ap.add_argument("verb", choices=["plan", "replan", "show", "go", "tick", "status", "pause", "steer", "accept",
                                     "accept-goal", "done", "discard", "discard-goal", "restore",
                                     "predict", "accept-predicted", "discard-predicted", "summary"])
    ap.add_argument("--until", default="", help="go: deadline HH:MM (local) — nothing new after it; summary when the last run ends")
    ap.add_argument("--all", action="store_true", help="predict: every repo, not only those touched in 30 days")
    ap.add_argument("--fresh", action="store_true", help="predict: ignore the commit cache")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--max", type=int, default=None, help="agents at a time")
    ap.add_argument("--no-elaborate", action="store_true", help="milestones only, no planner calls")
    ap.add_argument("--force", action="store_true", help="re-plan even if the current campaign is armed")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    md = MEDITATION_DIR       # read at call time: defaults bind at def time,
                              # and a test that reassigned the global still
                              # wrote a plan built from its fixture goals into
                              # the owner's real campaign.json (2026-09-04)
    if a.verb == "plan":
        cur = load(md)
        if cur and cur.get("armed") and not a.force:
            # A re-plan archives the current campaign. Done to an ARMED one
            # it silently threw away the owner's go — it happened on
            # 2026-09-03: armed 06:38, re-planned 06:55, nobody told.
            print("refusing: the current campaign is ARMED (go given %s). "
                  "pause it first, or --force to archive it." % cur.get("armed_at", "?"))
            return 1
        g = build(elaborate=not a.no_elaborate, meditation_dir=md)
        save(g, md)
        if a.json:
            print(json.dumps({"ok": True, "data": {"id": g["id"], "totals": g["totals"],
                                                   "notes": g["notes"]}}))
        else:
            print(render(g))
            print("\nwritten: %s" % os.path.join(md, PAGE_NAME))
        return 0
    if a.verb == "show":
        g = load(md)
        print(render(g, predictions=load_predictions(md)) if g else "no campaign planned — run: meditate campaign plan")
        return 0 if g else 1
    if a.verb == "go":
        until = parse_until(a.until) if a.until else None
        if a.until and not until:
            print("--until wants HH:MM, got %r" % a.until)
            return 2
        r = go(meditation_dir=md, max_parallel=a.max, until=until)
        print(json.dumps(r) if a.json else
              ("armed%s — dispatched %d: %s" % ((" until " + r["until"]) if r.get("until") else "",
                                                len(r.get("dispatched", [])), ", ".join(r.get("dispatched", [])))
               if r.get("armed") else "not armed: %s" % r.get("why")))
        return 0 if r.get("armed") else 1
    if a.verb == "replan":
        r = replan(meditation_dir=md, elaborator=None if not a.no_elaborate else (lambda goal, ms: []))
        print(json.dumps(r) if a.json else "re-planned %s — %d nodes carried, %d new%s"
              % (r.get("id"), r.get("carried", 0), r.get("new", 0),
                 ("\n  " + "\n  ".join(r["notes"])) if r.get("notes") else ""))
        return 0
    if a.verb == "summary":
        g = load(md)
        if not g:
            print("no campaign")
            return 1
        print(summarize(g, os.path.join(md, "spend.jsonl")))
        return 0
    if a.verb == "tick":
        r = tick(meditation_dir=md, max_parallel=a.max)
        print(json.dumps(r) if a.json else render_status(status(md)))
        return 0
    if a.verb == "status":
        s = status(md)
        print(json.dumps({"ok": True, "data": s}) if a.json else render_status(s))
        return 0
    if a.verb == "pause":
        r = pause(meditation_dir=md, why=" ".join(a.args) or "paused by owner")
        print(json.dumps(r) if a.json else "paused — " + r.get("why", ""))
        return 0
    if a.verb == "accept":
        if not a.args:
            print("usage: campaign accept <node-id>")
            return 2
        r = accept(a.args[0], meditation_dir=md)
        print(json.dumps(r) if a.json else ("accepted %s" % r["node"] if r.get("ok") else "could not accept: " + r.get("why", "")))
        return 0 if r.get("ok") else 1
    if a.verb == "predict":
        r = predict(meditation_dir=md, fresh=a.fresh, active_days=None if a.all else 30)
        print(json.dumps(r) if a.json else "predicted %d · cached %d · skipped %d · failed %d\n%s" % (
            len(r["predicted"]), len(r["cached"]), len(r["skipped"]), len(r["failed"]),
            "\n".join("  " + x for x in r["failed"][:8])))
        return 0
    if a.verb in ("accept-predicted", "discard-predicted"):
        if len(a.args) < 2:
            print("usage: campaign %s <project> \"<title>\"" % a.verb)
            return 2
        fn = accept_predicted if a.verb == "accept-predicted" else discard_predicted
        r = fn(a.args[0], " ".join(a.args[1:]), meditation_dir=md)
        print(json.dumps(r) if a.json else (("ok — " + json.dumps({k: v for k, v in r.items() if k != "ok"})) if r.get("ok") else "could not: " + r.get("why", "")))
        return 0 if r.get("ok") else 1
    if a.verb in ("discard", "discard-goal", "restore"):
        if not a.args:
            print("usage: campaign %s <name> [reason]" % a.verb)
            return 2
        if a.verb == "discard":
            r = discard_proposed(a.args[0], meditation_dir=md, reason=" ".join(a.args[1:]))
        elif a.verb == "discard-goal":
            r = discard_goal(a.args[0], meditation_dir=md, reason=" ".join(a.args[1:]))
        else:
            import goals as gl
            r = gl.restore_discarded(a.args[0])
        print(json.dumps(r) if a.json else (("ok — " + json.dumps({k: v for k, v in r.items() if k != "ok"})) if r.get("ok") else "could not: " + r.get("why", "")))
        return 0 if r.get("ok") else 1
    if a.verb == "done":
        if not a.args:
            print("usage: campaign done <node-id> [note]")
            return 2
        r = done(a.args[0], meditation_dir=md, note=" ".join(a.args[1:]))
        print(json.dumps(r) if a.json else ("done — %d step(s) can move" % len(r.get("unblocked", [])) if r.get("ok") else "could not: " + r.get("why", "")))
        return 0 if r.get("ok") else 1
    if a.verb == "accept-goal":
        if not a.args:
            print("usage: campaign accept-goal <name>")
            return 2
        r = accept_goal(a.args[0], meditation_dir=md)
        print(json.dumps(r) if a.json else ("written %s — re-plan to bring it in" % r["path"] if r.get("ok") else "could not accept: " + r.get("why", "")))
        return 0 if r.get("ok") else 1
    if a.verb == "steer":
        if len(a.args) < 2:
            print("usage: campaign steer <node-id> \"message\"")
            return 2
        r = steer(a.args[0], " ".join(a.args[1:]), meditation_dir=md)
        print(json.dumps(r) if a.json else
              ("steering %s — log %s" % (r["node"], r["log"]) if r.get("ok") else "could not steer: " + r.get("why", "")))
        return 0 if r.get("ok") else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
