"""The all-goals run: plan the whole graph, get one go, execute, watch, steer.

The owner's ask (2026-09-03): a mode where the twin reads everything it
has, plans the completion of EVERY present goal — elaborated as far as it
can into a graph, because solving one step is how you see the next — says
who will do each step, shows it in plain words, waits for one "go", and
then runs it. And because it is the biggest run the tool will ever make,
it must be watched with real metrics and steered mid-flight.

What this file pins, and the failure each one is written against:
  - the graph is BUILT from the goal files and the elaborator, never typed
  - a step is ready only when everything it depends on is done
  - nothing dispatches before the owner's go, and go dispatches only what
    is ready and under the caps
  - a finished step's RESULT.next becomes the next node — the graph grows
    as it is solved
  - a blocked step stops its own branch and nothing else
  - the monitor's numbers come from the ledger and the logs, not from the
    plan; "stuck" is measured against the log, not assumed
  - steering is `--continue` on the node's own session

Every external effect (the elaborator, the dispatcher, the log reader, the
clock) is injectable, so this runs with no agent and no money.

Run: python3 ~/.claude/skills/meditate/test_campaign.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

import campaign as cp  # noqa: E402

GOAL_A = """---
name: a-live
title: A live
project: a
cwd: %s
status: active
---
## Milestones
- [x] done already
- [ ] first open
- [ ] second open
"""
GOAL_B = """---
name: b-ship
title: B ship
project: b
cwd: %s
status: active
---
## Milestones
- [ ] only open
"""


def _world(t, elaborate=True):
    gdir = os.path.join(t, "goals"); os.makedirs(gdir)
    open(os.path.join(gdir, "a.md"), "w").write(GOAL_A % t)
    open(os.path.join(gdir, "b.md"), "w").write(GOAL_B % t)
    med = os.path.join(t, "med"); os.makedirs(med)
    return gdir, med


def _elab(goal, milestone):
    """A fake elaborator: two ordered sub-steps for 'first open', none else."""
    if milestone == "first open":
        return [{"id": "s1", "title": "s1", "why": "see", "depends_on": [],
                 "kind": "assess", "check": ""},
                {"id": "s2", "title": "s2", "why": "act", "depends_on": ["s1"],
                 "kind": "goal", "check": "git log"}]
    return []


# ---------------------------------------------------------------------------
# building the graph
# ---------------------------------------------------------------------------

def test_the_graph_is_BUILT_from_the_goal_files():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        names = {n["id"]: n for n in g["nodes"]}
        # 3 open milestones + 2 elaborated steps under 'first open'
        assert len(g["nodes"]) == 5, [n["title"] for n in g["nodes"]]
        assert g["totals"]["goals"] == 2 and g["totals"]["nodes"] == 5
        assert g["armed"] is False and g["id"]
        # the done milestone is not a node
        assert not any(n["title"] == "done already" for n in g["nodes"])


def test_milestones_within_a_goal_run_in_FILE_ORDER():
    """The goal author's order is a dependency. 'second open' waits for
    'first open'; 'first open' waits for its own sub-steps."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        by = {n["title"]: n for n in g["nodes"]}
        assert by["first open"]["id"] in by["second open"]["depends_on"]
        assert by["s2"]["id"] in by["first open"]["depends_on"]
        assert by["s1"]["id"] in by["s2"]["depends_on"]
        # goals do not depend on each other
        assert not by["only open"]["depends_on"]


def test_ready_means_every_dependency_is_DONE():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        r = {n["title"] for n in cp.ready(g)}
        assert r == {"s1", "only open"}, r
        by = {n["title"]: n for n in g["nodes"]}
        by["s1"]["status"] = "done"
        r = {n["title"] for n in cp.ready(g)}
        assert r == {"s2", "only open"}, r


def test_every_node_names_its_AGENT_and_its_cap():
    """Who, at what effort, under what dollar cap, and on what basis — the
    plan the owner reads must say it, not imply it."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        for n in g["nodes"]:
            a = n["agent"]
            assert a["model"] and a["effort"] and a["basis"], n
            assert a["budget_usd"] > 0, n
        assert g["totals"]["est_usd"] > 0


def test_the_human_page_is_PLAIN_and_names_the_go():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        page = cp.render(g)
        assert "A live" in page and "B ship" in page
        assert "first open" in page and "only open" in page
        assert "$" in page, "no cost on the page"
        assert "go" in page.lower()
        for jargon in ("DAG", "topological", "orchestrat"):
            assert jargon not in page, jargon


def test_the_elaborator_failing_leaves_the_MILESTONE_as_the_node():
    """A planner that cannot elaborate must not lose the milestone. The
    milestone itself is always a node; sub-steps are a refinement."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)

        def boom(goal, milestone):
            raise RuntimeError("no planner")
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=boom)
        titles = {n["title"] for n in g["nodes"]}
        assert titles == {"first open", "second open", "only open"}, titles
        assert any("elaborat" in n.lower() for n in g.get("notes", [])), g.get("notes")


# ---------------------------------------------------------------------------
# the go, and what it launches
# ---------------------------------------------------------------------------

def test_NOTHING_dispatches_before_the_go():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        sent = []
        cp.save(g, med)
        out = cp.tick(meditation_dir=med, dispatch=lambda n: sent.append(n) or {"log": "x", "session": "s"})
        assert not sent, sent
        assert out["armed"] is False


def test_go_dispatches_only_READY_nodes_within_the_cap():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        sent = []
        out = cp.go(meditation_dir=med, max_parallel=1,
                    dispatch=lambda n: sent.append(n["title"]) or {"log": "l-" + n["id"], "session": "s"})
        assert out["armed"] is True
        assert len(sent) == 1, sent
        assert sent[0] in ("s1", "only open")
        g2 = cp.load(med)
        running = [n for n in g2["nodes"] if n["status"] == "running"]
        assert len(running) == 1 and running[0]["log"] == "l-" + running[0]["id"]


def test_the_name_carries_the_KIND_so_the_ledger_can_attribute_it():
    """Every ledger splits the log name on '-' for the kind. A campaign node
    named 'campaign-…' would file every run under a kind no role has."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        for n in g["nodes"]:
            assert n["name"].split("-")[0] == n["kind"], n["name"]


# ---------------------------------------------------------------------------
# the monitor: the graph grows as it is solved
# ---------------------------------------------------------------------------

def _finished(next_step="", blocked=None, pushed=True, commits=("abc",)):
    return {"type": "result", "subtype": "success", "is_error": False,
            "total_cost_usd": 0.4, "num_turns": 5, "duration_ms": 1000,
            "structured_output": {"did": ["x"], "commits": list(commits),
                                  "pushed": pushed, "milestone_ticked": None,
                                  "tests": None, "blocked_on": blocked,
                                  "next": next_step}}


def test_a_finished_node_is_DONE_and_its_next_becomes_a_node():
    """Solve one step, see the next. RESULT.next is the elaboration the
    agent did with its hands on the work; it joins the graph as a child."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=2,
              dispatch=lambda n: {"log": "l-" + n["id"], "session": "s-" + n["id"]})
        g2 = cp.load(med)
        running = [n for n in g2["nodes"] if n["status"] == "running"]
        results = {n["log"]: _finished(next_step="then wire the button") for n in running}
        out = cp.tick(meditation_dir=med, dispatch=lambda n: {"log": "z", "session": "z"},
                      read_result=lambda log: results.get(log))
        g3 = cp.load(med)
        done = [n for n in g3["nodes"] if n["status"] == "done"]
        assert len(done) == 2, [(n["title"], n["status"]) for n in g3["nodes"]]
        grown = [n for n in g3["nodes"] if n["title"] == "then wire the button"]
        assert grown, "RESULT.next did not become a node"
        assert done[0]["id"] in grown[0]["depends_on"]
        assert out["metrics"]["done"] == 2


def test_a_BLOCKED_node_stops_its_branch_and_nothing_else():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=2,
              dispatch=lambda n: {"log": "l-" + n["id"], "session": "s-" + n["id"]})
        g2 = cp.load(med)
        s1 = [n for n in g2["nodes"] if n["title"] == "s1"][0]
        only = [n for n in g2["nodes"] if n["title"] == "only open"][0]
        results = {s1["log"]: _finished(blocked="needs the App Store password", pushed=False, commits=()),
                   only["log"]: _finished()}
        cp.tick(meditation_dir=med, dispatch=lambda n: {"log": "z", "session": "z"},
                read_result=lambda log: results.get(log))
        g3 = cp.load(med)
        by = {n["title"]: n for n in g3["nodes"]}
        # the wall becomes the owner's; s1 waits on it, keeps its session,
        # and nothing else in the graph is touched
        wall = by["needs the App Store password"]
        assert wall["kind"] == "human" and wall["status"] == "waiting"
        assert by["s1"]["status"] == "pending" and wall["id"] in by["s1"]["depends_on"]
        assert "password" in by["s1"]["blocked_on"]
        assert by["s2"]["status"] == "pending", by["s2"]
        assert by["only open"]["status"] == "done"
        page = cp.render(g3)
        assert "YOUR HANDS" in page and "needs the App Store password" in page, "the wall is not on the page"


def test_the_monitor_reads_MONEY_from_the_result_not_the_plan():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=2,
              dispatch=lambda n: {"log": "l-" + n["id"], "session": "s"})
        g2 = cp.load(med)
        results = {n["log"]: _finished() for n in g2["nodes"] if n["status"] == "running"}
        out = cp.tick(meditation_dir=med, dispatch=lambda n: {"log": "z", "session": "z"},
                      read_result=lambda log: results.get(log))
        m = out["metrics"]
        assert abs(m["spent_usd"] - 0.8) < 1e-9, m
        assert m["verified_commits"] == 2 or m["claimed_commits"] == 2, m
        assert m["pushed"] == 2, m


def test_a_running_node_with_a_SILENT_log_is_called_stuck_not_working():
    """'Running' from the plan's point of view and 'doing something' are
    different claims. Stuck is measured: no result and no log growth for
    longer than the stall window."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=1,
              dispatch=lambda n: {"log": "l-" + n["id"], "session": "s"})
        clock = [1000.0]
        out = cp.tick(meditation_dir=med, dispatch=lambda n: {"log": "z", "session": "z"},
                      read_result=lambda log: None,
                      log_mtime=lambda log: 1000.0, now=lambda: clock[0])
        assert out["metrics"]["stuck"] == 0
        clock[0] = 1000.0 + cp.STALL_S + 1
        out = cp.tick(meditation_dir=med, dispatch=lambda n: {"log": "z", "session": "z"},
                      read_result=lambda log: None,
                      log_mtime=lambda log: 1000.0, now=lambda: clock[0])
        assert out["metrics"]["stuck"] == 1, out["metrics"]


def test_tick_KEEPS_dispatching_ready_work_while_armed_within_the_cap():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        sent = []
        cp.go(meditation_dir=med, max_parallel=1,
              dispatch=lambda n: sent.append(n["title"]) or {"log": "l-" + n["id"], "session": "s"})
        g2 = cp.load(med)
        first = [n for n in g2["nodes"] if n["status"] == "running"][0]
        cp.tick(meditation_dir=med, max_parallel=1,
                dispatch=lambda n: sent.append(n["title"]) or {"log": "l-" + n["id"], "session": "s"},
                read_result=lambda log: _finished() if log == first["log"] else None)
        assert len(sent) == 2, sent


def test_pause_STOPS_new_dispatch_and_says_so():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=1,
              dispatch=lambda n: {"log": "l", "session": "s"})
        cp.pause(meditation_dir=med, why="owner said hold")
        sent = []
        out = cp.tick(meditation_dir=med, dispatch=lambda n: sent.append(1) or {"log": "l", "session": "s"},
                      read_result=lambda log: _finished())
        assert not sent and out["armed"] is False
        assert "owner said hold" in json.dumps(cp.load(med))


def test_steer_CONTINUES_the_nodes_own_session():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=1,
              dispatch=lambda n: {"log": "l", "session": "sess-1"})
        g2 = cp.load(med)
        node = [n for n in g2["nodes"] if n["status"] == "running"][0]
        seen = []
        r = cp.steer(node["id"], "use the sandbox account", meditation_dir=med,
                     continue_fn=lambda name, msg: seen.append((name, msg)) or {"started": True, "log": "l2", "session": "sess-1"})
        assert r["ok"], r
        assert seen and seen[0][0] == node["name"] and "sandbox" in seen[0][1]
        g3 = cp.load(med)
        n3 = [n for n in g3["nodes"] if n["id"] == node["id"]][0]
        assert n3["steers"] and n3["steers"][-1]["message"].startswith("use the")
        assert n3["status"] == "running"


def test_status_is_ONE_screen_with_the_numbers_that_decide():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        s = cp.status(meditation_dir=med)
        for k in ("nodes", "done", "running", "blocked", "ready", "stuck",
                  "spent_usd", "est_usd", "pushed", "verified_commits"):
            assert k in s["metrics"], k
        text = cp.render_status(s)
        assert "0 of 5" in text or "0/5" in text, text


# ---------------------------------------------------------------------------
# ideas: the plan proposes, the owner accepts, only then does it run
# ---------------------------------------------------------------------------

def _ideas(goal, done, opens):
    if goal == "b-ship":
        return [{"title": "add a smoke test on deploy", "why": "no deploy is checked",
                 "check": "curl / returns 200 after deploy", "kind": "goal"}]
    return []


def test_the_plan_PROPOSES_new_milestones_per_goal():
    """The first cut only expanded what was already written — "it's not
    generating new ideas". An idea is a proposed milestone the goal file
    does not have, with a why and a check, marked as an idea."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab, ideator=_ideas)
        ideas = [n for n in g["nodes"] if n["status"] == "idea"]
        assert len(ideas) == 1 and ideas[0]["goal"] == "b-ship", ideas
        assert ideas[0]["title"] == "add a smoke test on deploy"
        assert g["totals"]["ideas"] == 1
        assert "smoke test" in cp.render(g) and "idea" in cp.render(g).lower()


def test_an_idea_is_NEVER_dispatched_until_accepted():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab, ideator=_ideas)
        cp.save(g, med)
        sent = []
        cp.go(meditation_dir=med, max_parallel=9,
              dispatch=lambda n: sent.append(n["title"]) or {"log": "l-" + n["id"], "session": "s"})
        assert "add a smoke test on deploy" not in sent, sent
        idea = [n for n in cp.load(med)["nodes"] if n["status"] == "idea"][0]
        r = cp.accept(idea["id"], meditation_dir=med)
        assert r["ok"], r
        g2 = cp.load(med)
        n = [x for x in g2["nodes"] if x["id"] == idea["id"]][0]
        assert n["status"] == "pending" and n.get("accepted"), n
        # it depends on the goal's last open milestone, so it runs after it
        last = [x for x in g2["nodes"] if x["goal"] == "b-ship" and x["title"] == "only open"][0]
        assert last["id"] in n["depends_on"]


def test_the_ideator_failing_costs_NOTHING_but_a_note():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)

        def boom(goal, done, opens):
            raise RuntimeError("no ideas today")
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab, ideator=boom)
        assert not [n for n in g["nodes"] if n["status"] == "idea"]
        assert any("idea" in n.lower() for n in g["notes"]), g["notes"]


def test_ideas_are_proposed_for_DONE_goals_too():
    """A goal at 100% is exactly where the next milestones are missing."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        open(os.path.join(gdir, "c.md"), "w").write(
            "---\nname: c-done\ntitle: C done\nproject: c\ncwd: %s\nstatus: active\n---\n"
            "## Milestones\n- [x] shipped\n" % t)
        seen = []

        def ideator(goal, done, opens):
            seen.append(goal)
            return [{"title": "next thing for " + goal, "why": "w", "check": "c", "kind": "goal"}]
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab, ideator=ideator)
        assert "c-done" in seen, seen
        c = [n for n in g["nodes"] if n["goal"] == "c-done"]
        assert len(c) == 1 and c[0]["status"] == "idea" and not c[0]["depends_on"]
        assert g["totals"]["goals"] == 3


# ---------------------------------------------------------------------------
# your hands: what only the owner can do, kept apart, ticked by him
# ---------------------------------------------------------------------------

GOAL_H = """---
name: h-ads
title: H ads
project: h
cwd: %s
status: active
---
## Milestones
- [ ] Owner supplies the Pixel ID and a System User token (dashboard-only)
- [ ] Pixel activated in the deploy env
"""


def _world_h(t):
    gdir = os.path.join(t, "goals"); os.makedirs(gdir)
    open(os.path.join(gdir, "h.md"), "w").write(GOAL_H % t)
    med = os.path.join(t, "med"); os.makedirs(med)
    return gdir, med


def test_a_step_only_the_owner_can_do_is_a_HUMAN_node():
    """'Owner supplies…', 'approved by Apple', a password, a card — an agent
    sent at these spends its cap to report blocked. They are kept apart."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world_h(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=lambda *a: [])
        by = {n["title"]: n for n in g["nodes"]}
        h = by["Owner supplies the Pixel ID and a System User token (dashboard-only)"]
        assert h["kind"] == "human" and h["status"] == "waiting", h
        assert by["Pixel activated in the deploy env"]["kind"] == "goal"
        assert h["id"] in by["Pixel activated in the deploy env"]["depends_on"]
        assert g["totals"]["human"] == 1


def test_a_human_node_is_NEVER_dispatched():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world_h(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=lambda *a: [])
        cp.save(g, med)
        sent = []
        cp.go(meditation_dir=med, max_parallel=9,
              dispatch=lambda n: sent.append(n["title"]) or {"log": "l", "session": "s"})
        assert not sent, sent      # the agent step waits on the human one
        assert cp.load(med)["metrics"]["human"] == 1


def test_the_owner_ticks_it_DONE_and_the_branch_moves():
    """Done by hand ticks the goal file too — the checkbox is the record."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world_h(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=lambda *a: [])
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=9, dispatch=lambda n: {"log": "l", "session": "s"})
        h = [n for n in cp.load(med)["nodes"] if n["kind"] == "human"][0]
        r = cp.done(h["id"], meditation_dir=med, note="token in vault", goals_dir=gdir)
        assert r["ok"], r
        g2 = cp.load(med)
        h2 = [n for n in g2["nodes"] if n["id"] == h["id"]][0]
        assert h2["status"] == "done" and h2["done_by"] == "owner" and "vault" in h2["note"]
        assert "- [x] Owner supplies" in open(os.path.join(gdir, "h.md")).read()
        sent = []
        cp.tick(meditation_dir=med, max_parallel=9,
                dispatch=lambda n: sent.append(n["title"]) or {"log": "l", "session": "s"},
                read_result=lambda log: None)
        assert sent == ["Pixel activated in the deploy env"], sent


def test_an_agents_BLOCKED_ON_becomes_a_human_node_that_gates_it():
    """The agent found the wall; the wall is the owner's. It waits on him,
    and when he ticks it the same session continues, not a fresh one."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=2,
              dispatch=lambda n: {"log": "l-" + n["id"], "session": "sess-" + n["id"]})
        g2 = cp.load(med)
        only = [n for n in g2["nodes"] if n["title"] == "only open"][0]
        cp.tick(meditation_dir=med, dispatch=lambda n: {"log": "z", "session": "z"},
                read_result=lambda log: _finished(blocked="needs the App Store password", pushed=False, commits=())
                if log == only["log"] else None)
        g3 = cp.load(med)
        by = {n["title"]: n for n in g3["nodes"]}
        wall = by["needs the App Store password"]
        assert wall["kind"] == "human" and wall["status"] == "waiting", wall
        assert by["only open"]["status"] == "pending" and wall["id"] in by["only open"]["depends_on"]
        assert by["only open"]["session"] == only["session"], "session must survive the wait"
        cp.done(wall["id"], meditation_dir=med, note="pasted it")
        sent = []
        cp.tick(meditation_dir=med, max_parallel=9,
                dispatch=lambda n: sent.append(dict(n)) or {"log": "l2", "session": n.get("session") or "s"},
                read_result=lambda log: None)
        assert sent and sent[0]["title"] == "only open"
        assert "pasted it" in (sent[0].get("resume_message") or ""), sent[0]
        again = [n for n in cp.load(med)["nodes"] if n["title"] == "only open"][0]
        assert "pasted it" in again.get("resumed_with", ""), "the resume is not on the record"


def test_your_hands_is_its_OWN_section_on_the_page():
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world_h(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=lambda *a: [])
        page = cp.render(g)
        assert "YOUR HANDS" in page and "Owner supplies" in page
        s = cp.status.__wrapped__(g) if hasattr(cp.status, "__wrapped__") else None
        assert g["totals"]["human"] == 1


def test_every_goal_file_is_LISTED_even_with_no_steps():
    """A finished goal has no row in the graph; without this it could not be
    discarded from the page."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        open(os.path.join(gdir, "c.md"), "w").write(
            "---\nname: c-done\ntitle: C done\nproject: c\ncwd: %s\nstatus: active\n---\n"
            "## Milestones\n- [x] shipped\n" % t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        names = {x["name"]: x for x in g["goals"]}
        assert "c-done" in names and names["c-done"]["done"] == 1 and names["c-done"]["total"] == 1
        assert not any(n["goal"] == "c-done" for n in g["nodes"])
        cp.save(g, med)
        assert [x["name"] for x in cp.status(meditation_dir=med)["goals"]] == [x["name"] for x in g["goals"]]


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok   %s" % fn.__name__)
        except AssertionError as e:
            failed += 1; print("  FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:
            failed += 1; print("  ERR  %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
