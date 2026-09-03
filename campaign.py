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
DEFAULT_PARALLEL = 3         # the RAM law: 6+9+8 < 30 GB, from the outage
ELABORATE_BUDGET_USD = 0.35  # planning is cheap; execution is not
ELABORATE_TIMEOUT_S = 300

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
                "kind": {"type": "string", "enum": ["goal", "thread", "repair", "revive", "assess"]},
                "check": {"type": "string"},
            },
            "required": ["id", "title", "why", "depends_on", "kind", "check"]}}},
    "required": ["steps"],
}


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
    argv = ["claude", "-p", prompt, "--model", "sonnet", "--output-format", "json",
            "--permission-mode", "dontAsk", "--max-budget-usd", str(ELABORATE_BUDGET_USD),
            "--json-schema", json.dumps(IDEAS_SCHEMA),
            "--disallowedTools", "Edit Write NotebookEdit Bash(git commit:*) Bash(git push:*) Bash(rm:*)"]
    r = subprocess.run(argv, cwd=cwd if cwd and os.path.isdir(cwd) else os.path.expanduser("~"),
                       capture_output=True, text=True, timeout=ELABORATE_TIMEOUT_S,
                       stdin=subprocess.DEVNULL)
    for ln in reversed(r.stdout.strip().splitlines()):
        if ln.startswith("{") and '"type"' in ln:
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            so = d.get("structured_output") or {}
            return [x for x in (so.get("ideas") or []) if isinstance(x, dict) and x.get("title")]
    raise RuntimeError("planner returned no ideas object")


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
    argv = ["claude", "-p", prompt, "--model", "sonnet", "--output-format", "json",
            "--permission-mode", "dontAsk", "--max-budget-usd", str(ELABORATE_BUDGET_USD),
            "--json-schema", json.dumps(STEPS_SCHEMA),
            "--disallowedTools", "Edit Write NotebookEdit Bash(git commit:*) Bash(git push:*) Bash(rm:*)"]
    r = subprocess.run(argv, cwd=cwd if cwd and os.path.isdir(cwd) else os.path.expanduser("~"),
                       capture_output=True, text=True, timeout=ELABORATE_TIMEOUT_S,
                       stdin=subprocess.DEVNULL)
    for ln in reversed(r.stdout.strip().splitlines()):
        if ln.startswith("{") and '"type"' in ln:
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            so = d.get("structured_output") or {}
            steps = so.get("steps") or []
            return [s for s in steps if isinstance(s, dict) and s.get("id") and s.get("title")]
    raise RuntimeError("planner returned no steps object")


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
        prev_id: Optional[str] = None
        for m in opens:
            text = (m.get("text") or "").strip()
            # the headline, not the raw line: a milestone reads "iOS subs
            # approved — **CORRECTED 2026-08-25: was NOT a queue wait…" in
            # the file, and the note is history, not the step
            head = (m.get("headline") or text).strip() or text
            mid = _nid(g["name"], text)
            node = {"id": mid, "goal": g["name"], "goal_title": g.get("title") or g["name"],
                    "cwd": g.get("cwd") or "", "milestone": head, "title": head,
                    "why": "an open milestone of " + (g.get("title") or g["name"]),
                    "kind": "goal", "check": "", "depends_on": [prev_id] if prev_id else [],
                    "status": "pending", "agent": agent_for("goal"),
                    "name": "goal-%s-%s" % (g["name"][:16], mid),
                    "steers": [], "log": "", "session": "", "result": None}
            steps: List[Dict[str, Any]] = []
            if elaborator is not None:
                try:
                    steps = elaborator(g["name"], text) if elaborator is not elaborate_with_claude \
                        else elaborator(g["name"], head, cwd=g.get("cwd") or "",
                                        title=g.get("title") or "", note=g.get("note") or "")
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
                kind = s.get("kind") if s.get("kind") in ("goal", "thread", "repair", "revive", "assess") else "goal"
                sid = ids[str(s["id"])]
                deps = [ids[d] for d in (s.get("depends_on") or []) if d in ids]
                if prev_id:
                    deps.append(prev_id)
                subs.append({"id": sid, "goal": g["name"], "goal_title": node["goal_title"],
                             "cwd": node["cwd"], "milestone": head, "title": str(s["title"]).strip(),
                             "why": str(s.get("why") or "").strip(), "kind": kind,
                             "check": str(s.get("check") or "").strip(), "depends_on": deps,
                             "status": "pending", "agent": agent_for(kind),
                             "name": "%s-%s-%s" % (kind, g["name"][:16], sid.replace(".", "_")),
                             "steers": [], "log": "", "session": "", "result": None})
            node["depends_on"] = node["depends_on"] + [s["id"] for s in subs]
            nodes.extend(subs)
            nodes.append(node)
            prev_id = node["id"]
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
    est = round(sum(n["agent"]["budget_usd"] for n in nodes if n["status"] != "idea"), 2)
    return {"id": time.strftime("%Y%m%d-%H%M%S", time.gmtime()), "created": _now_iso(),
            "armed": False, "armed_at": "", "paused_why": "", "max_parallel": DEFAULT_PARALLEL,
            "nodes": nodes, "notes": notes, "proposed_goals": proposed,
            "totals": {"goals": n_goals, "nodes": len([n for n in nodes if n["status"] != "idea"]),
                       "ideas": n_ideas, "est_usd": est},
            "events": [{"ts": _now_iso(), "what": "planned", "nodes": len(nodes)}],
            "metrics": {}}


def ready(g: Dict[str, Any]) -> List[Dict[str, Any]]:
    done = {n["id"] for n in g["nodes"] if n["status"] == "done"}
    return [n for n in g["nodes"] if n["status"] == "pending"
            and all(d in done for d in n["depends_on"])]


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def _state_path(meditation_dir: str) -> str:
    return os.path.join(meditation_dir, STATE_NAME)


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
    return "\n".join(lines)


def dispatch_real(n: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    import go
    a = n["agent"]
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


def _log_mtime_real(log: str) -> Optional[float]:
    try:
        return os.path.getmtime(log)
    except OSError:
        return None


def _dispatch_ready(g: Dict[str, Any], max_parallel: int,
                    dispatch: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> List[str]:
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
        n["session"] = r.get("session", "")
        n["worktree"] = r.get("worktree", "")
        n["started"] = _now_iso()
        n["started_epoch"] = time.time()
        running += 1
        sent.append(n["id"])
        g["events"].append({"ts": _now_iso(), "what": "dispatched", "node": n["id"],
                            "title": n["title"][:60], "model": n["agent"]["model"]})
    return sent


def go(meditation_dir: str = MEDITATION_DIR, max_parallel: Optional[int] = None,
       dispatch: Optional[Callable] = None) -> Dict[str, Any]:
    """The owner's go. Arms the campaign and sends the first ready wave."""
    g = load(meditation_dir)
    if not g:
        return {"armed": False, "why": "no campaign planned — run `campaign plan` first"}
    g["armed"] = True
    g["armed_at"] = _now_iso()
    g["armed_epoch"] = time.time()
    g["paused_why"] = ""
    if max_parallel:
        g["max_parallel"] = int(max_parallel)
    g["events"].append({"ts": _now_iso(), "what": "go"})
    sent = _dispatch_ready(g, g.get("max_parallel") or DEFAULT_PARALLEL,
                           dispatch or dispatch_real)
    g["metrics"] = _metrics(g, time.time())
    save(g, meditation_dir)
    return {"armed": True, "dispatched": sent, "metrics": g["metrics"]}


def pause(meditation_dir: str = MEDITATION_DIR, why: str = "") -> Dict[str, Any]:
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


def _absorb(n: Dict[str, Any], res: Dict[str, Any], g: Dict[str, Any]) -> None:
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
    if res.get("is_error") or (res.get("subtype") not in (None, "success") and not so):
        n["status"] = "failed"
        n["why_failed"] = str(res.get("subtype") or "error")
        g["events"].append({"ts": _now_iso(), "what": "failed", "node": n["id"], "why": n["why_failed"]})
        return
    if so.get("blocked_on"):
        n["status"] = "blocked"
        n["blocked_on"] = str(so["blocked_on"])[:300]
        g["events"].append({"ts": _now_iso(), "what": "blocked", "node": n["id"], "why": n["blocked_on"][:80]})
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


def _metrics(g: Dict[str, Any], now: float) -> Dict[str, Any]:
    # ideas are proposals, not steps: they do not count as nodes, pending
    # or per-goal totals until accepted. The status line read "0 of 25
    # done" over 13 steps the day ideas landed.
    ideas = [n for n in g["nodes"] if n["status"] == "idea"]
    ns = [n for n in g["nodes"] if n["status"] != "idea"]
    by = {}
    for n in ns:
        by[n["status"]] = by.get(n["status"], 0) + 1
    fin = [n for n in ns if n.get("result")]
    spent = round(sum(n["result"]["cost_usd"] for n in fin), 4)
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
            "ideas": len(ideas),
            "per_goal": per_goal, "hours": round(hours, 2)}


def tick(meditation_dir: str = MEDITATION_DIR, dispatch: Optional[Callable] = None,
         read_result: Optional[Callable] = None, log_mtime: Optional[Callable] = None,
         now: Optional[Callable[[], float]] = None,
         max_parallel: Optional[int] = None) -> Dict[str, Any]:
    """Advance the campaign one step. The heartbeat calls this every pass."""
    g = load(meditation_dir)
    if not g:
        return {"armed": False, "why": "no campaign", "metrics": {}}
    now_f = now or time.time
    read_result = read_result or read_result_real
    log_mtime = log_mtime or _log_mtime_real
    t = now_f()
    for n in g["nodes"]:
        if n["status"] != "running":
            continue
        res = read_result(n.get("log", ""))
        if res:
            _absorb(n, res, g)
            continue
        mt = log_mtime(n.get("log", "")) if n.get("log") else None
        last = mt if mt else n.get("started_epoch") or t
        n["stuck"] = (t - last) > STALL_S
    sent: List[str] = []
    if g.get("armed") and not g.get("paused_why"):
        sent = _dispatch_ready(g, max_parallel or g.get("max_parallel") or DEFAULT_PARALLEL,
                               dispatch or dispatch_real)
    g["metrics"] = _metrics(g, t)
    g["last_tick"] = _now_iso()
    save(g, meditation_dir)
    return {"armed": bool(g.get("armed")), "dispatched": sent, "metrics": g["metrics"]}


def steer(node_id: str, message: str, meditation_dir: str = MEDITATION_DIR,
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
        continue_fn = _go.continue_agent
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


def accept(node_id: str, meditation_dir: str = MEDITATION_DIR) -> Dict[str, Any]:
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


def status(meditation_dir: str = MEDITATION_DIR) -> Dict[str, Any]:
    g = load(meditation_dir)
    if not g:
        return {"id": "", "armed": False, "metrics": {}, "nodes": [], "why": "no campaign"}
    return {"id": g["id"], "armed": g.get("armed", False), "paused_why": g.get("paused_why", ""),
            "created": g.get("created"), "armed_at": g.get("armed_at", ""),
            "metrics": _metrics(g, time.time()), "notes": g.get("notes", []),
            "proposed_goals": g.get("proposed_goals", []),
            "nodes": [{k: n.get(k) for k in ("id", "goal", "goal_title", "title", "kind", "status",
                                              "depends_on", "agent", "blocked_on", "stuck",
                                              "result", "steers", "grown", "name", "log",
                                              "idea", "accepted", "why", "check")}
                      for n in g["nodes"]],
            "events": g.get("events", [])[-30:]}


# ---------------------------------------------------------------------------
# the pages
# ---------------------------------------------------------------------------

_GLYPH = {"done": "✓", "running": "▶", "blocked": "■", "failed": "✗", "pending": "·", "idea": "?"}


def render(g: Dict[str, Any]) -> str:
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
             "%d steps grown from results · %d steers · %d denials · %d ideas waiting for you"
             % (m["grown"], m["steers"], m["denials"], m.get("ideas", 0))]
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
    ap.add_argument("verb", choices=["plan", "show", "go", "tick", "status", "pause", "steer", "accept", "accept-goal"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--max", type=int, default=None, help="agents at a time")
    ap.add_argument("--no-elaborate", action="store_true", help="milestones only, no planner calls")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.verb == "plan":
        g = build(elaborate=not a.no_elaborate)
        save(g)
        if a.json:
            print(json.dumps({"ok": True, "data": {"id": g["id"], "totals": g["totals"],
                                                   "notes": g["notes"]}}))
        else:
            print(render(g))
            print("\nwritten: %s" % os.path.join(MEDITATION_DIR, PAGE_NAME))
        return 0
    if a.verb == "show":
        g = load()
        print(render(g) if g else "no campaign planned — run: meditate campaign plan")
        return 0 if g else 1
    if a.verb == "go":
        r = go(max_parallel=a.max)
        print(json.dumps(r) if a.json else
              ("armed — dispatched %d: %s" % (len(r.get("dispatched", [])), ", ".join(r.get("dispatched", [])))
               if r.get("armed") else "not armed: %s" % r.get("why")))
        return 0 if r.get("armed") else 1
    if a.verb == "tick":
        r = tick(max_parallel=a.max)
        print(json.dumps(r) if a.json else render_status(status()))
        return 0
    if a.verb == "status":
        s = status()
        print(json.dumps({"ok": True, "data": s}) if a.json else render_status(s))
        return 0
    if a.verb == "pause":
        r = pause(why=" ".join(a.args) or "paused by owner")
        print(json.dumps(r) if a.json else "paused — " + r.get("why", ""))
        return 0
    if a.verb == "accept":
        if not a.args:
            print("usage: campaign accept <node-id>")
            return 2
        r = accept(a.args[0])
        print(json.dumps(r) if a.json else ("accepted %s" % r["node"] if r.get("ok") else "could not accept: " + r.get("why", "")))
        return 0 if r.get("ok") else 1
    if a.verb == "accept-goal":
        if not a.args:
            print("usage: campaign accept-goal <name>")
            return 2
        r = accept_goal(a.args[0])
        print(json.dumps(r) if a.json else ("written %s — re-plan to bring it in" % r["path"] if r.get("ok") else "could not accept: " + r.get("why", "")))
        return 0 if r.get("ok") else 1
    if a.verb == "steer":
        if len(a.args) < 2:
            print("usage: campaign steer <node-id> \"message\"")
            return 2
        r = steer(a.args[0], " ".join(a.args[1:]))
        print(json.dumps(r) if a.json else
              ("steering %s — log %s" % (r["node"], r["log"]) if r.get("ok") else "could not steer: " + r.get("why", "")))
        return 0 if r.get("ok") else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
