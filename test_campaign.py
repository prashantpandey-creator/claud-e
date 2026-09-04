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


def test_a_running_node_with_a_SILENT_log_is_STOPPED_not_called_working():
    """'Running' from the plan's point of view and 'doing something' are
    different claims. A log unmoved past the stall window used to set a
    flag the page showed while the process ran on; now the run is stopped
    and resumed once with the fact."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        clock = [1000.0]
        cp.go(meditation_dir=med, max_parallel=1, now=lambda: clock[0],
              dispatch=lambda n: {"log": "l-" + n["id"], "session": "s"})
        killed = []
        out = cp.tick(meditation_dir=med, dispatch=lambda n: {"log": "z", "session": "s"},
                      read_result=lambda log: None, kill=lambda n: killed.append(n["id"]) or True,
                      log_mtime=lambda log: 1000.0, now=lambda: clock[0])
        assert out["metrics"]["stuck"] == 0 and killed == []
        clock[0] = 1000.0 + cp.STALL_S + 1
        out = cp.tick(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "z", "session": "s"},
                      read_result=lambda log: None, kill=lambda n: killed.append(n["id"]) or True,
                      log_mtime=lambda log: 1000.0, now=lambda: clock[0])
        g2 = cp.load(med)
        assert len(killed) == 1, killed
        n = [x for x in g2["nodes"] if x["id"] == killed[0]][0]
        assert n["attempts"] == 1 and n["log"] == "z" and "unmoved" in n.get("resumed_with", ""), n
        assert any(e["what"] == "stopped" for e in g2["events"])


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


def test_a_RE_PLAN_refuses_to_archive_an_ARMED_campaign():
    """It happened: armed 06:38, re-planned 06:55, the go vanished with
    the archive and nothing said so."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l", "session": "s"})
        old = cp.MEDITATION_DIR
        cp.MEDITATION_DIR = med
        try:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cp.main(["plan", "--no-elaborate"])
        finally:
            cp.MEDITATION_DIR = old
        assert rc == 1 and "ARMED" in buf.getvalue(), buf.getvalue()
        assert cp.load(med)["id"] == g["id"], "the armed campaign was replaced"


GOAL_ORDER = """---
name: mob
title: Mobile live
project: m
cwd: %s
status: active
---
## Milestones
- [ ] iOS subscriptions approved
- [ ] Android sign-in repaired
- [ ] Owner supplies the Pixel ID from the Meta dashboard
- [ ] Pixel activated in the deploy env with NEXT_PUBLIC_META_PIXEL_ID
"""


def test_a_human_item_gates_the_next_step_only_when_they_share_a_SUBJECT():
    """Live graph 2026-09-04: the ready set was EMPTY — every agent step sat
    behind a human item, and 'Android sign-in repaired' waited on 'iOS
    subscriptions approved' only because it came next in the file. File
    order is the author's dependency between agent steps; through a human
    item it holds only when the two lines share a subject word ('Pixel')."""
    with tempfile.TemporaryDirectory() as t:
        gdir = os.path.join(t, "goals"); os.makedirs(gdir)
        open(os.path.join(gdir, "m.md"), "w").write(GOAL_ORDER % t)
        med = os.path.join(t, "med"); os.makedirs(med)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=lambda *a: [])
        by = {n["title"]: n for n in g["nodes"]}
        ios, android = by["iOS subscriptions approved"], by["Android sign-in repaired"]
        assert ios["kind"] == "human"
        assert ios["id"] not in android["depends_on"], "a false gate: iOS approval does not gate Android sign-in"
        pix_id, pix_on = by["Owner supplies the Pixel ID from the Meta dashboard"], \
            by["Pixel activated in the deploy env with NEXT_PUBLIC_META_PIXEL_ID"]
        assert pix_id["kind"] == "human" and pix_id["id"] in pix_on["depends_on"], "a real gate: shares 'pixel'"
        # the agent step after the human one still follows the LAST agent step
        assert android["id"] in pix_on["depends_on"]
        assert [n["title"] for n in cp.ready(g)] == ["Android sign-in repaired"]


# ---------------------------------------------------------------------------
# predicted milestones: the twin plans ahead for EVERY project, on its own
# ---------------------------------------------------------------------------
#
# The owner's ask (2026-09-04): "create elaborate future plans and milestones
# for all the projects on its own — predicted milestones." Ideas proposed per
# GOAL; this predicts per PROJECT, goal or not, from the repo itself. A
# prediction is a proposal: accept appends it to the project's goal (or
# writes one), discard ledgers it, and a project that has not moved (same
# HEAD) is never re-predicted — the cache keys on the commit.

def _predictor(project, path, sha, goal):
    return [{"title": "add a smoke test to %s" % project, "why": "nothing checks deploys",
             "check": "curl / after deploy returns 200", "size": "S"},
            {"title": "document the env vars", "why": "install fails on a clean box",
             "check": "README lists every var", "size": "S"}]


def _projects_world(t):
    repo = os.path.join(t, "shop"); os.makedirs(repo)
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    open(os.path.join(repo, "a"), "w").write("a\n")
    subprocess.run(["git", "-C", repo, "add", "a"], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], check=True)
    gdir = os.path.join(t, "goals"); os.makedirs(gdir)
    med = os.path.join(t, "med"); os.makedirs(med)
    return repo, gdir, med


def test_predict_writes_milestones_per_project_keyed_on_its_COMMIT():
    with tempfile.TemporaryDirectory() as t:
        repo, gdir, med = _projects_world(t)
        calls = []
        def pred(project, path, sha, goal):
            calls.append(project); return _predictor(project, path, sha, goal)
        r = cp.predict(repos={"shop": repo}, goals_dir=gdir, meditation_dir=med, predictor=pred)
        assert r["predicted"] == ["shop"] and calls == ["shop"], r
        pr = cp.load_predictions(med)
        assert pr["shop"]["milestones"][0]["title"].startswith("add a smoke test")
        assert pr["shop"]["sha"] and pr["shop"]["ts"]
        # same commit -> cache hit, no second call
        r2 = cp.predict(repos={"shop": repo}, goals_dir=gdir, meditation_dir=med, predictor=pred)
        assert r2["cached"] == ["shop"] and calls == ["shop"], (r2, calls)


def test_a_project_that_MOVED_is_predicted_again():
    with tempfile.TemporaryDirectory() as t:
        repo, gdir, med = _projects_world(t)
        calls = []
        def pred(project, path, sha, goal):
            calls.append(sha); return _predictor(project, path, sha, goal)
        cp.predict(repos={"shop": repo}, goals_dir=gdir, meditation_dir=med, predictor=pred)
        import subprocess
        open(os.path.join(repo, "b"), "w").write("b\n")
        subprocess.run(["git", "-C", repo, "add", "b"], check=True)
        subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "more"], check=True)
        cp.predict(repos={"shop": repo}, goals_dir=gdir, meditation_dir=med, predictor=pred)
        assert len(calls) == 2 and calls[0] != calls[1]


def test_a_prediction_is_NEVER_a_step_until_accepted_and_accept_lands_in_the_goal():
    with tempfile.TemporaryDirectory() as t:
        repo, gdir, med = _projects_world(t)
        open(os.path.join(gdir, "shop-live.md"), "w").write(
            "---\nname: shop-live\ntitle: Shop live\nproject: shop\ncwd: %s\nstatus: active\n---\n"
            "## Milestones\n- [ ] checkout works\n" % repo)
        cp.predict(repos={"shop": repo}, goals_dir=gdir, meditation_dir=med, predictor=_predictor)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=lambda *a: [])
        assert not any("smoke test" in n["title"] for n in g["nodes"]), "a prediction became a step on its own"
        r = cp.accept_predicted("shop", "add a smoke test to shop", meditation_dir=med, goals_dir=gdir)
        assert r["ok"] and r["goal"] == "shop-live", r
        txt = open(os.path.join(gdir, "shop-live.md")).read()
        assert "- [ ] add a smoke test to shop" in txt and "predicted" in txt
        left = cp.load_predictions(med)["shop"]["milestones"]
        assert not any(m["title"] == "add a smoke test to shop" for m in left)
        # a goal-less project gets a goal file written for it (its own repo:
        # one path is one project's goal, so sharing shop's would be shop's)
        solo = os.path.join(t, "solo"); os.makedirs(solo)
        import subprocess as _sp
        _sp.run(["git", "init", "-q", "-b", "main", solo], check=True)
        open(os.path.join(solo, "a"), "w").write("a\n"); _sp.run(["git", "-C", solo, "add", "a"], check=True)
        _sp.run(["git", "-C", solo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"], check=True)
        cp.predict(repos={"solo": solo}, goals_dir=gdir, meditation_dir=med, predictor=_predictor)
        r2 = cp.accept_predicted("solo", "document the env vars", meditation_dir=med, goals_dir=gdir)
        assert r2["ok"] and os.path.exists(os.path.join(gdir, "solo-next.md")), r2
        assert "- [ ] document the env vars" in open(os.path.join(gdir, "solo-next.md")).read()


def test_a_discarded_prediction_stays_GONE_across_re_predictions():
    with tempfile.TemporaryDirectory() as t:
        repo, gdir, med = _projects_world(t)
        cp.predict(repos={"shop": repo}, goals_dir=gdir, meditation_dir=med, predictor=_predictor)
        r = cp.discard_predicted("shop", "document the env vars", meditation_dir=med, ledger=os.path.join(med, "d.jsonl"))
        assert r["ok"], r
        assert not any(m["title"] == "document the env vars" for m in cp.load_predictions(med)["shop"]["milestones"])
        cp.predict(repos={"shop": repo}, goals_dir=gdir, meditation_dir=med, predictor=_predictor, fresh=True,
                   ledger=os.path.join(med, "d.jsonl"))
        assert not any(m["title"] == "document the env vars" for m in cp.load_predictions(med)["shop"]["milestones"])


def test_predictions_are_on_the_PLAN_page_apart_from_the_steps():
    with tempfile.TemporaryDirectory() as t:
        repo, gdir, med = _projects_world(t)
        cp.predict(repos={"shop": repo}, goals_dir=gdir, meditation_dir=med, predictor=_predictor)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=lambda *a: [])
        cp.save(g, med)
        s = cp.status(meditation_dir=med)
        assert s["predictions"]["shop"]["milestones"], s.get("predictions")
        page = cp.render(g, predictions=cp.load_predictions(med))
        assert "PREDICTED" in page and "smoke test" in page


def test_a_node_whose_session_is_in_a_GUARDED_worktree_is_handed_to_a_fresh_agent():
    """The campaign's first node ran three sessions ($7.42) in a worktree
    under ~/.claude, where Claude Code denies writes headless; the session
    cannot move. A fresh agent takes the node WITH the previous findings."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        n = [x for x in g["nodes"] if x["title"] == "only open"][0]
        import subprocess as _sp
        repo = os.path.join(t, "r"); os.makedirs(repo)
        _sp.run(["git", "init", "-q", "-b", "main", repo], check=True)
        open(os.path.join(repo, "a"), "w").write("a\n"); _sp.run(["git", "-C", repo, "add", "a"], check=True)
        _sp.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"], check=True)
        n["cwd"] = repo          # the dispatcher refuses a non-repo cwd first
        n["session"] = "old-sess"; n["resume_message"] = "cleared"
        n["worktree"] = os.path.expanduser("~/.claude/meditation/worktrees/x")
        n["result"] = {"did": ["designed the fix"], "blocked_on": "Write denied", "next": "apply it",
                       "cost_usd": 1, "commits": [], "verified_commits": [], "pushed": False}
        seen = {}
        import go as _go
        real_h = _go._headless
        def fake_h(cwd, prompt, name, model="", effort="", budget_usd=0.0, **kw):
            seen.update(prompt=prompt, name=name)
            fake_h.last = {"log": "l", "session": "new", "worktree": "/tmp/w"}
            return True
        _go._headless = fake_h
        try:
            r = cp.dispatch_real(n)
        finally:
            _go._headless = real_h
        assert r and r["session"] == "new", r
        assert n.get("handed_over") and n["session"] == "" and not n.get("resume_message")
        assert "designed the fix" in seen["prompt"] and "cleared by the owner" in seen["prompt"]


def test_an_EMPTY_prediction_is_not_a_cache_hit():
    with tempfile.TemporaryDirectory() as t:
        repo, gdir, med = _projects_world(t)
        calls = []
        def empty(project, path, sha, goal):
            calls.append(1); return []
        cp.predict(repos={"shop": repo}, goals_dir=gdir, meditation_dir=med, predictor=empty)
        cp.predict(repos={"shop": repo}, goals_dir=gdir, meditation_dir=med, predictor=empty)
        assert len(calls) == 2, "an empty result was cached as an answer"


def test_a_nodes_spend_is_the_SUM_of_its_runs_not_the_last_result():
    """Live 2026-09-04: five runs on one node — three fresh sessions ($1.16,
    $3.31, $2.95) and a fourth session resumed once ($2.86 then $3.83, the
    CLI's total for that session) — and the status line read '$3.83 spent'.
    Every run's total is ITS OWN, not the session's cumulative: a resumed
    run's out_tokens (18,841) and cache_read were smaller than the run it
    continued (33,538) — per-invocation figures, and total_cost_usd is
    computed from the same modelUsage. So runs ADD, same session or not.
    The earlier 'max per session' rule read a $14.10 node as $3.83."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l1", "session": "s1"})
        node = [n for n in cp.load(med)["nodes"] if n["status"] == "running"][0]
        res1 = _finished(blocked="wall", pushed=False, commits=()); res1["total_cost_usd"] = 1.16
        cp.tick(meditation_dir=med, dispatch=lambda n: None, read_result=lambda log: res1 if log == "l1" else None)
        # the owner clears the wall; the same session resumes and reports its cumulative total
        wall = [n for n in cp.load(med)["nodes"] if n.get("from_agent")][0]
        cp.done(wall["id"], meditation_dir=med)
        cp.tick(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l2", "session": "s1"}, read_result=lambda log: None)
        res2 = _finished(); res2["total_cost_usd"] = 3.83
        out = cp.tick(meditation_dir=med, dispatch=lambda n: None, read_result=lambda log: res2 if log == "l2" else None)
        n2 = [n for n in cp.load(med)["nodes"] if n["id"] == node["id"]][0]
        assert abs(n2["spent_usd"] - 4.99) < 1e-9, n2.get("spent_usd")      # same session: runs add
        assert abs(out["metrics"]["spent_usd"] - 4.99) < 1e-9, out["metrics"]
        # a FRESH session on the same node adds
        n2["status"] = "pending"; n2["session"] = ""
        g3 = cp.load(med); [x for x in g3["nodes"] if x["id"] == node["id"]][0].update(status="pending", session="")
        cp.save(g3, med)
        cp.tick(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l3", "session": "s2"}, read_result=lambda log: None)
        res3 = _finished(); res3["total_cost_usd"] = 2.00
        out = cp.tick(meditation_dir=med, dispatch=lambda n: None, read_result=lambda log: res3 if log == "l3" else None)
        n3 = [n for n in cp.load(med)["nodes"] if n["id"] == node["id"]][0]
        assert abs(n3["spent_usd"] - 6.99) < 1e-9, n3.get("spent_usd")
        assert abs(out["metrics"]["spent_usd"] - 6.99) < 1e-9, out["metrics"]


def test_metrics_reads_the_LEDGER_by_name_when_it_knows_more():
    """The daemon that absorbed the first shipped node ran code older than
    the sum-of-runs fix (a launchd server keeps the code it booted with), so
    the node carried only its last run's $3.83 while the ledger held five
    runs. The ledger, keyed by the node's name and deduplicated by log, is
    the source the status line trusts when it says more."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l1", "session": "s1"})
        node = [n for n in cp.load(med)["nodes"] if n["status"] == "running"][0]
        res = _finished(); res["total_cost_usd"] = 3.83
        cp.tick(meditation_dir=med, dispatch=lambda n: None, read_result=lambda log: res if log == "l1" else None)
        rows = [{"log": "a.log", "name": node["name"], "cost_usd": 2.86},
                {"log": "b.log", "name": node["name"], "cost_usd": 3.83},
                {"log": "b.log", "name": node["name"], "cost_usd": 3.83},     # the duplicate row
                {"log": "z.log", "name": "goal-other-zzzz", "cost_usd": 9.0}]
        with open(os.path.join(med, "spend.jsonl"), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        out = cp.tick(meditation_dir=med, dispatch=lambda n: None, read_result=lambda log: None)
        assert abs(out["metrics"]["spent_usd"] - 6.69) < 1e-9, out["metrics"]
        # and never LESS than what the node itself knows
        os.remove(os.path.join(med, "spend.jsonl"))
        out = cp.tick(meditation_dir=med, dispatch=lambda n: None, read_result=lambda log: None)
        assert abs(out["metrics"]["spent_usd"] - 3.83) < 1e-9, out["metrics"]


def _goal_file(gdir, name, title, cwd, opens, dones=()):
    with open(os.path.join(gdir, name + ".md"), "w") as f:
        f.write("---\nname: %s\ntitle: %s\nproject: %s\ncwd: %s\nstatus: active\n---\n## Milestones\n%s%s"
                % (name, title, name, cwd,
                   "".join("- [x] %s\n" % d for d in dones),
                   "".join("- [ ] %s\n" % o for o in opens)))


def test_replan_KEEPS_the_run_by_node_id_and_does_not_re_elaborate():
    """A re-plan used to archive the running campaign and start clean: the
    five-run session on 'Android sign-in repaired', its wall and its spend
    would have been rediscovered from zero. Node ids are sha1(goal,
    milestone), so state carries by id: done stays done with its result,
    an agent-raised wall stays attached to its parent, the campaign id and
    armed flag stay, and a milestone that was already elaborated reuses its
    sub-steps instead of paying the planner again. A milestone ticked in
    the file between plans simply leaves the graph."""
    with tempfile.TemporaryDirectory() as t:
        gdir = os.path.join(t, "goals"); os.makedirs(gdir)
        med = os.path.join(t, "med"); os.makedirs(med)
        _goal_file(gdir, "g", "Goal G", t, opens=["first open", "second open"])
        calls = []
        def elab(goal, milestone):
            calls.append(milestone)
            return ([{"id": "s1", "title": "sub one", "why": "w", "depends_on": [], "kind": "goal", "check": ""}]
                    if milestone == "first open" else [])
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=elab)
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l1", "session": "s1"})
        node = [n for n in cp.load(med)["nodes"] if n["status"] == "running"][0]
        # the sub-step ships; then the milestone node hits a wall
        res = _finished(); res["total_cost_usd"] = 1.5
        cp.tick(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l2", "session": "s2"},
                read_result=lambda log: res if log == "l1" else None)
        cp.tick(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l9", "session": "s9"},
                read_result=lambda log: _finished(blocked="Owner supplies the key") if log == "l2" else None)
        before = cp.load(med)
        wall = [n for n in before["nodes"] if n.get("from_agent")][0]
        parent = [n for n in before["nodes"] if n["id"] == wall["from_agent"]][0]
        assert wall["id"] in parent["depends_on"]
        # the owner ticks nothing, adds a milestone, and re-plans
        _goal_file(gdir, "g", "Goal G", t, opens=["first open", "second open", "third open"])
        calls.clear()
        r = cp.replan(goals_dir=gdir, meditation_dir=med, elaborator=elab)
        after = cp.load(med)
        assert after["id"] == before["id"] and after["armed"] is True, (after["id"], before["id"])
        assert calls == ["third open"], "re-elaborated a milestone it already had: %s" % calls
        by = {n["id"]: n for n in after["nodes"]}
        assert by[node["id"]]["status"] == "done" and by[node["id"]]["result"]["cost_usd"] == 1.5
        assert wall["id"] in by and by[wall["id"]]["status"] == "waiting"
        assert wall["id"] in by[parent["id"]]["depends_on"] and by[parent["id"]]["session"] == "s2"
        assert any(n["title"] == "third open" and n["status"] == "pending" for n in after["nodes"])
        assert r["carried"] >= 2 and r["new"] == 1, r
        # a milestone ticked in the file leaves the graph
        _goal_file(gdir, "g", "Goal G", t, opens=["first open", "third open"], dones=["second open"])
        cp.replan(goals_dir=gdir, meditation_dir=med, elaborator=elab)
        assert not any(n["title"] == "second open" for n in cp.load(med)["nodes"])
        assert len(os.listdir(os.path.join(med, "campaigns"))) == 0 if os.path.isdir(os.path.join(med, "campaigns")) else True


def test_a_deadline_stops_dispatch_and_CLOSES_OUT_with_a_summary():
    """'Keep running till 9 pm and then give me a summary.' Past the
    deadline nothing new is sent; once nothing is running the campaign
    disarms, writes campaign-summary.md, and mails it."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        t0 = 1_000_000.0
        r = cp.go(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l1", "session": "s1"},
                  until=t0 + 100, now=lambda: t0)
        assert r["dispatched"] and cp.load(med)["until_epoch"] == t0 + 100
        mails = []
        # before the deadline: a finished node lets the next one go
        out = cp.tick(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l2", "session": "s2"},
                      read_result=lambda log: _finished() if log == "l1" else None, now=lambda: t0 + 50,
                      mailer=lambda subj, body: mails.append(subj))
        assert out["dispatched"], out
        # past it: nothing new, and the running one is left to finish
        out = cp.tick(meditation_dir=med, max_parallel=3, dispatch=lambda n: {"log": "l3", "session": "s3"},
                      read_result=lambda log: None, now=lambda: t0 + 101, mailer=lambda s, b: mails.append(s))
        assert out["dispatched"] == [] and cp.load(med)["armed"] is True
        # it finishes: close out
        out = cp.tick(meditation_dir=med, dispatch=lambda n: {"log": "l4", "session": "s4"},
                      read_result=lambda log: _finished() if log == "l2" else None, now=lambda: t0 + 200,
                      mailer=lambda s, b: mails.append(s))
        g2 = cp.load(med)
        assert g2["armed"] is False and "deadline" in g2["paused_why"], g2["paused_why"]
        p = os.path.join(med, "campaign-summary.md")
        assert os.path.exists(p)
        text = open(p).read()
        assert "SHIPPED" in text and "commits" in text.lower(), text[:400]
        assert mails and "summary" in mails[-1].lower(), mails


def test_a_run_past_its_bound_is_STOPPED_retried_once_then_handed_to_you():
    """Nothing bounded an agent's wall time: stuck was a flag on the page.
    Now a run past 2x its kind's median (floor 30 min, cap 2 h) or with a
    log unmoved 45 min is stopped and resumed once with the fact; a second
    stop raises a wall for the owner instead of a third run."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        t0 = 2_000_000.0
        cp.go(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l1", "session": "s1"}, now=lambda: t0)
        node = [n for n in cp.load(med)["nodes"] if n["status"] == "running"][0]
        killed = []
        kill = lambda n: killed.append(n["id"]) or True
        # inside the bound: nothing happens
        cp.tick(meditation_dir=med, dispatch=lambda n: None, read_result=lambda log: None,
                log_mtime=lambda log: t0 + 1000, now=lambda: t0 + 1200, kill=kill, medians={node["kind"]: 600.0})
        assert killed == [], killed
        # past it (2 x 600 s = 1200 → floored to 1800): stopped, resumed with the fact
        out = cp.tick(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l2", "session": "s1"},
                      read_result=lambda log: None, log_mtime=lambda log: t0 + 1700, now=lambda: t0 + 1801,
                      kill=kill, medians={node["kind"]: 600.0})
        assert killed == [node["id"]], killed
        n2 = [n for n in cp.load(med)["nodes"] if n["id"] == node["id"]][0]
        assert n2["attempts"] == 1 and n2["status"] == "running" and n2["log"] == "l2", n2
        assert "stopped" in (n2.get("resumed_with") or "").lower(), n2.get("resumed_with")
        # a second overrun: no third run — a wall for the owner
        out = cp.tick(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l3", "session": "s1"},
                      read_result=lambda log: None, log_mtime=lambda log: t0 + 1900, now=lambda: t0 + 1801 + 1801,
                      kill=kill, medians={node["kind"]: 600.0})
        g3 = cp.load(med)
        n3 = [n for n in g3["nodes"] if n["id"] == node["id"]][0]
        assert n3["attempts"] == 2 and n3["status"] == "pending" and n3["log"] != "l3", n3
        walls = [n for n in g3["nodes"] if n.get("from_agent") == node["id"]]
        assert walls and walls[0]["status"] == "waiting" and "twice" in walls[0]["title"], walls
        assert walls[0]["id"] in n3["depends_on"]
        assert [e["what"] for e in g3["events"]].count("stopped") == 2


def test_a_run_that_DIED_at_start_is_retried_fresh():
    """'claude: No such file or directory' as the log's only line was a
    node running forever. It is a death: retried once with a fresh session."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        t0 = 3_000_000.0
        cp.go(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l1", "session": "s1"}, now=lambda: t0)
        node = [n for n in cp.load(med)["nodes"] if n["status"] == "running"][0]
        sent = []
        cp.tick(meditation_dir=med, max_parallel=1, dispatch=lambda n: sent.append(n.get("session")) or {"log": "l2", "session": "s9"},
                read_result=lambda log: None, death=lambda log: "claude: No such file or directory" if log == "l1" else "",
                now=lambda: t0 + 700, kill=lambda n: True)
        n2 = [n for n in cp.load(med)["nodes"] if n["id"] == node["id"]][0]
        assert n2["attempts"] == 1 and n2["status"] == "running" and n2["session"] == "s9", n2
        assert sent == [""], "a death at start must not resume the dead session: %s" % sent


def test_a_usage_limit_HOLDS_the_whole_campaign_not_one_node():
    """A result that is an error about the usage/rate limit is not the
    node's fault: the campaign holds 30 minutes and the node resumes after,
    with its session, at no cost to its attempts."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        t0 = 4_000_000.0
        cp.go(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l1", "session": "s1"}, now=lambda: t0)
        node = [n for n in cp.load(med)["nodes"] if n["status"] == "running"][0]
        limited = {"type": "result", "subtype": "error_during_execution", "is_error": True,
                   "total_cost_usd": 0.02, "num_turns": 1, "result": "You have hit your usage limit. Try again at 6pm."}
        out = cp.tick(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l2", "session": "s1"},
                      read_result=lambda log: limited if log == "l1" else None, now=lambda: t0 + 10)
        g2 = cp.load(med)
        n2 = [n for n in g2["nodes"] if n["id"] == node["id"]][0]
        assert out["dispatched"] == [] and g2["hold_until"] > t0 + 10, (out, g2.get("hold_until"))
        assert n2["status"] == "pending" and n2["session"] == "s1" and not n2.get("attempts"), n2
        assert n2["status"] != "failed"
        # the hold lifts by itself
        out = cp.tick(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l2", "session": "s1"},
                      read_result=lambda log: None, now=lambda: t0 + 10 + cp.HOLD_S + 1)
        assert out["dispatched"] == [node["id"]], out


def test_the_wall_bound_comes_from_the_kind_median_within_limits():
    assert cp.wall_bound_s("goal", {}) == cp.WALL_DEFAULT_S
    assert cp.wall_bound_s("goal", {"goal": 600.0}) == cp.WALL_MIN_S            # 2x600 floored
    assert cp.wall_bound_s("goal", {"goal": 4000.0}) == cp.WALL_MAX_S           # 2x4000 capped
    assert cp.wall_bound_s("goal", {"goal": 2000.0}) == 4000.0
    with tempfile.TemporaryDirectory() as t:
        led = os.path.join(t, "spend.jsonl")
        with open(led, "w") as f:
            for d in (100, 300, 500):
                f.write(json.dumps({"log": "x%d.log" % d, "name": "goal-x", "duration_ms": d * 1000, "subtype": "success"}) + "\n")
            f.write(json.dumps({"log": "y.log", "name": "revive-y", "duration_ms": 9_000_000, "subtype": "died"}) + "\n")
        m = cp.median_duration_by_kind(led)
        assert m == {"goal": 300.0}, m


def test_replan_MERGES_against_the_campaign_as_it_is_when_the_planners_return():
    """The planners take minutes; the brain ticks every five. A re-plan that
    merged against the graph it loaded at the start would overwrite every
    tick in between — a node absorbed, a wall raised. The merge re-loads."""
    with tempfile.TemporaryDirectory() as t:
        gdir = os.path.join(t, "goals"); os.makedirs(gdir)
        med = os.path.join(t, "med"); os.makedirs(med)
        _goal_file(gdir, "g", "Goal G", t, opens=["first open"])
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=lambda goal, ms: [])
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l1", "session": "s1"})
        node = [n for n in cp.load(med)["nodes"] if n["status"] == "running"][0]
        _goal_file(gdir, "g", "Goal G", t, opens=["first open", "second open"])

        def slow_elab(goal, ms):
            # while "the planner runs", a tick lands: the node finishes
            cp.tick(meditation_dir=med, dispatch=lambda n: None,
                    read_result=lambda log: _finished() if log == "l1" else None)
            return []
        cp.replan(goals_dir=gdir, meditation_dir=med, elaborator=slow_elab)
        after = {n["id"]: n for n in cp.load(med)["nodes"]}
        assert after[node["id"]]["status"] == "done", after[node["id"]]["status"]


def test_campaign_writes_are_SERIALISED_by_a_lock():
    """tick (the brain, every 5 min), done/steer (the console) and replan
    all load-mutate-save the same file. Each holds campaign.json.lock for
    its load→save; a second writer waits rather than overwriting."""
    import inspect
    for fn in (cp.tick, cp.go, cp.done, cp.steer, cp.pause, cp.accept):
        assert "_locked(" in inspect.getsource(fn), fn.__name__
    with tempfile.TemporaryDirectory() as t:
        with cp._locked(t):
            assert os.path.exists(os.path.join(t, "campaign.json.lock"))


def test_a_PREDICTED_milestone_is_not_chained_behind_the_line_above_it():
    """File order is the author's dependency between the steps he wrote.
    Predicted milestones are appended by the twin and are independent by
    nature — chaining them left a CI gate waiting on a Russia field test
    (measured 2026-09-04: 30 mila-live nodes, 2 runnable). A predicted line
    depends only on a human line it shares a subject with."""
    with tempfile.TemporaryDirectory() as t:
        gdir = os.path.join(t, "goals"); os.makedirs(gdir)
        med = os.path.join(t, "med"); os.makedirs(med)
        with open(os.path.join(gdir, "g.md"), "w") as f:
            f.write("---\nname: g\ntitle: G\nproject: g\ncwd: %s\nstatus: active\n---\n## Milestones\n"
                    "- [ ] Run the field test in Russia\n"
                    "- [ ] Add a test gate to deploy.yml <!-- predicted 2026-09-04: x; check: y -->\n"
                    "- [ ] Owner supplies the DNS record for the companion domain\n"
                    "- [ ] Verify the companion domain answers <!-- predicted 2026-09-04: x; check: y -->\n" % t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=lambda goal, ms: [])
        by = {n["title"]: n for n in g["nodes"]}
        assert by["Add a test gate to deploy.yml"]["depends_on"] == [], by["Add a test gate to deploy.yml"]["depends_on"]
        assert by["Verify the companion domain answers"]["depends_on"] == [by["Owner supplies the DNS record for the companion domain"]["id"]]
        assert len(cp.ready(g)) == 2, [n["title"] for n in cp.ready(g)]


def test_a_read_only_step_walled_by_its_OWN_permissions_escalates_instead_of_waiting_on_you():
    """Two assess steps walled on 2026-09-04 with 'sandbox denies all
    write/exec Bash ops' and 'gh CLI and git fetch both denied' — the
    twin's role gap, shown to the owner as his item. A permission wall on
    a read-only step re-runs it under the working role, once; any other
    wall on it is a real wall."""
    with tempfile.TemporaryDirectory() as t:
        gdir, med = _world(t)
        g = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        cp.save(g, med)
        cp.go(meditation_dir=med, max_parallel=1, dispatch=lambda n: {"log": "l1", "session": "s1"})
        node = [n for n in cp.load(med)["nodes"] if n["status"] == "running"][0]
        assert node["kind"] == "assess", node["kind"]
        sent = []
        out = cp.tick(meditation_dir=med, max_parallel=1,
                      dispatch=lambda n: sent.append((n["id"], n["kind"], n.get("session"))) or {"log": "l2", "session": "s2"},
                      read_result=lambda log: _finished(blocked="Sandbox denies all write/exec Bash ops in this worktree") if log == "l1" else None)
        g2 = cp.load(med)
        n2 = [n for n in g2["nodes"] if n["id"] == node["id"]][0]
        assert n2["kind"] == "goal" and n2["escalated"]["from"] == "assess" and n2["status"] == "running", n2
        assert sent == [(node["id"], "goal", "")], sent
        assert not any(m.get("from_agent") == node["id"] and m["status"] == "waiting" for m in g2["nodes"])
        assert any(e["what"] == "escalated" for e in g2["events"])
        # walled again, now as goal: a real wall for the owner
        out = cp.tick(meditation_dir=med, dispatch=lambda n: None,
                      read_result=lambda log: _finished(blocked="Sandbox denies git push") if log == "l2" else None)
        g3 = cp.load(med)
        assert any(m.get("from_agent") == node["id"] and m["status"] == "waiting" for m in g3["nodes"])
        # and a non-permission wall on a read-only step was always the owner's
        g4 = cp.build(goals_dir=gdir, meditation_dir=med, elaborator=_elab)
        a = [n for n in g4["nodes"] if n["kind"] == "assess"][0]
        cp._absorb(a, _finished(blocked="Need the App Store build number from the owner"), g4)
        assert a["kind"] == "assess" and any(m.get("from_agent") == a["id"] for m in g4["nodes"])


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
