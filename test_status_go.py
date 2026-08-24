"""Tests for status.py + go.py — the two-verb porcelain (Rule 0, A).

Contract:
  status:
  - one screen, ends with exactly ONE `next:` line — a decision, not a menu
  - priority: repair queue > dispatchable goals > overdue stilling > rest
  go:
  - executes the same priority: repair agent first when the queue is open,
    then goal agents up to the cap; prints what it DID
  - launcher injectable; repair launch recorded nowhere twice inside cooldown

Run: python3 ~/.claude/skills/meditate/test_status_go.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import go

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import status as st
import go as g

GOAL = """---
name: g-a
title: Ship it
project: p
cwd: /repo/p
status: active
---
## Milestones
- [ ] first open thing
"""


def _world(t, with_goal=True, with_repair=False):
    med = os.path.join(t, "med"); os.makedirs(med, exist_ok=True)
    store = os.path.join(med, "nidra_store"); os.makedirs(store, exist_ok=True)
    gdir = os.path.join(t, "goals"); os.makedirs(gdir, exist_ok=True)
    if with_goal:
        with open(os.path.join(gdir, "g.md"), "w") as f:
            f.write(GOAL)
    if with_repair:
        with open(os.path.join(med, "repair-queue.md"), "w") as f:
            f.write("# Repair queue\n\n## mem_x  [unverified]\n- statement: s\n")
    return med, store, gdir


def test_status_single_next_line():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t)
        out = st.status_text(meditation_dir=med, store_dir=store, goals_dir=gdir,
                             history_path=os.path.join(t, "h.jsonl"),
                             ledger_path=os.path.join(t, "d.jsonl"))
        # exactly one DECISION (marked →); per-goal "next:" lines are context
        assert out.count("\u2192") == 1, out


def test_status_priority_repair_beats_goals():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t, with_repair=True)
        out = st.status_text(meditation_dir=med, store_dir=store, goals_dir=gdir,
                             history_path=os.path.join(t, "h.jsonl"),
                             ledger_path=os.path.join(t, "d.jsonl"))
        dec = [l for l in out.splitlines() if "\u2192" in l]
        assert len(dec) == 1, "there must be exactly ONE decision line: %r" % out
        assert "check" in dec[0].lower() or "know" in dec[0].lower(), dec[0]


def test_status_goals_when_no_repair():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t)
        out = st.status_text(meditation_dir=med, store_dir=store, goals_dir=gdir,
                             history_path=os.path.join(t, "h.jsonl"),
                             ledger_path=os.path.join(t, "d.jsonl"))
        dec = [l for l in out.splitlines() if "\u2192" in l]
        assert len(dec) == 1, "there must be exactly ONE decision line: %r" % out
        assert "meditate go" in dec[0], dec[0]


def test_go_repair_first_then_goals():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t, with_repair=True)
        launched = []
        rep = g.run(n=2, meditation_dir=med, store_dir=store, goals_dir=gdir,
                    history_path=os.path.join(t, "h.jsonl"),
                    ledger_path=os.path.join(t, "d.jsonl"),
                    launcher=lambda cwd, prompt, name, model='': launched.append(name) or True)
        assert rep["repair_launched"] is True
        assert rep["goals_launched"] == 1          # cap 2 = 1 repair + 1 goal
        assert launched[0].startswith("repair"), launched


def test_go_goals_only_when_clean():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t)
        launched = []
        rep = g.run(n=2, meditation_dir=med, store_dir=store, goals_dir=gdir,
                    history_path=os.path.join(t, "h.jsonl"),
                    ledger_path=os.path.join(t, "d.jsonl"),
                    launcher=lambda cwd, prompt, name, model='': launched.append(name) or True)
        assert rep["repair_launched"] is False
        assert rep["goals_launched"] == 1
        assert launched and launched[0].startswith("goal-")


def test_launcher_error_surfaces_not_swallowed():
    """A launcher that raises (signature drift) must land in result['errors'],
    not vanish into a silent goals_launched=0 — that hid a real break for
    several commits until the full doctor caught it."""
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t)
        def boom(*a, **k):
            raise TypeError("launcher signature drifted")
        rep = g.run(n=1, meditation_dir=med, store_dir=store, goals_dir=gdir,
                    history_path=os.path.join(t, "h.jsonl"),
                    ledger_path=os.path.join(t, "d.jsonl"), launcher=boom)
        assert rep["goals_launched"] == 0
        assert rep["errors"] and "drifted" in rep["errors"][0], rep["errors"]


def test_go_zero_is_dry():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t, with_repair=True)
        launched = []
        rep = g.run(n=0, meditation_dir=med, store_dir=store, goals_dir=gdir,
                    history_path=os.path.join(t, "h.jsonl"),
                    ledger_path=os.path.join(t, "d.jsonl"),
                    launcher=lambda *a, **k: launched.append(1) or True)
        assert launched == [] and rep["would"], rep


def test_fix_list_and_scoped_selection():
    """Per-item repair: --list numbers actionable items; fix <n> scopes the
    kickoff to that ONE memory."""
    import go as g2
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t)
        mems = [
            {"id": "mem_aaa", "active": True, "flags": ["drifted"],
             "statement": "first drifted claim about alpha",
             "epistemic": {"evidence_status": "unverified"},
             "evidence": [{"source": "/x"}]},
            {"id": "mem_bbb", "active": True, "flags": ["drifted"],
             "statement": "second drifted claim about beta",
             "epistemic": {"evidence_status": "unverified"},
             "evidence": [{"source": "/y"}]},
        ]
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            for m in mems:
                f.write(json.dumps(m) + "\n")
        items = g2.repair_items(store_dir=store)
        assert [m["id"] for m in items] == ["mem_aaa", "mem_bbb"]
        k = g2._repair_kickoff(med, store_dir=store, select="2")
        assert "mem_bbb" in k["prompt"] and "mem_aaa" not in k["prompt"], k["prompt"][:200]
        k1 = g2._repair_kickoff(med, store_dir=store, select="mem_aaa")
        assert "mem_aaa" in k1["prompt"] and "mem_bbb" not in k1["prompt"]


def test_go_single_goal_by_name():
    import go as g2
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t, with_repair=True)   # repair open but skipped
        launched = []
        rep = g2.run(only_goal="g-a", meditation_dir=med, store_dir=store,
                     goals_dir=gdir, history_path=os.path.join(t, "h.jsonl"),
                     ledger_path=os.path.join(t, "d.jsonl"),
                     launcher=lambda cwd, prompt, name, model='': launched.append(name) or True)
        assert rep["repair_launched"] is False, "named-goal dispatch must not launch repair"
        assert rep["goals_launched"] == 1 and launched == ["goal-g-a"], (rep, launched)


def test_fleet_status_joins_ledger_and_goals():
    import drive as dv2
    with tempfile.TemporaryDirectory() as t:
        gdir = os.path.join(t, "goals"); os.makedirs(gdir)
        with open(os.path.join(gdir, "g.md"), "w") as f:
            f.write(GOAL)
        ledger = os.path.join(t, "d.jsonl")
        with open(ledger, "w") as f:
            f.write(json.dumps({"goal": "g-a", "milestone": "first open thing",
                                "ts_epoch": __import__("time").time() - 600}) + "\n")
        fl = dv2.fleet_status(goals_dir=gdir, ledger_path=ledger,
                              history_path=os.path.join(t, "h.jsonl"))
        assert len(fl["dispatched"]) == 1
        r = fl["dispatched"][0]
        assert r["goal"] == "g-a" and r["dispatched_min"] >= 9
        assert r["milestone_ticked"] is False, "milestone still open must show open"


def test_cli_status_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "status.py"), "--json"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env
    assert env["data"]["next"], "status must always decide a next action"


def test_precision_gate_refuses_to_dispatch_a_lying_queue():
    """A tool must measure its own precision BEFORE spending an agent.

    Measured 2026-08-23 on a real store: 28 of 30 queue items were the grader
    inventing claims. A fleet agent investigated them one by one and burned
    ~44k tokens producing no change. The check that would have caught it is
    an `os.path.exists` per item -- microseconds, zero tokens. Any verifier
    that can dispatch work must gate on its own precision first.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        fake = [{"id": "m1", "statement": "s",
                 "failing": [{"claim": "path:" + d}]},          # exists -> FALSE POSITIVE
                {"id": "m2", "statement": "s",
                 "failing": [{"claim": "path:" + d + "/nope"}]}]  # gone -> real
        checked = go.precheck(fake)
        assert checked["real"] == 1, checked
        assert checked["false_positive"] == 1, checked
        assert checked["precision"] == 0.5, checked

    with tempfile.TemporaryDirectory() as d:
        allfake = [{"id": "m%d" % i, "statement": "s",
                    "failing": [{"claim": "path:" + d}]} for i in range(5)]
        c = go.precheck(allfake)
        assert c["precision"] == 0.0
        assert c["verdict"] == "instrument", c
        assert "memory_files.py" in c["message"], c["message"]


def test_precheck_marks_unresolvable_claims_not_checkable():
    """Third value: 'I cannot check this' is NOT 'this is false'.

    Conflating the two is the single root cause behind all six grader
    defects fixed in nidra a1c1baf.
    """
    c = go.precheck([{"id": "m1", "statement": "s",
                      "failing": [{"claim": "content_anchor"}]}])
    assert c["not_checkable"] == 1, c
    assert c["real"] == 0 and c["false_positive"] == 0, c


def test_kickoff_carries_the_precheck_so_the_agent_need_not_look():
    """Deterministic work belongs in the dispatcher, not in an agent.

    Every tool call an agent makes stays in its context for every later
    turn, so making it re-derive what Python already knows is paid many
    times over. Hand it the verified result instead."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        gone = os.path.join(d, "definitely-gone")
        k = go._repair_kickoff(d, items=[{"id": "m1", "statement": "the thing",
                                          "failing": [{"claim": "path:" + gone}]}])
        assert k, "a real finding must still dispatch"
        assert gone in k["prompt"]
        assert "CONFIRMED GONE" in k["prompt"], k["prompt"][:400]


def test_only_one_agent_per_repo_and_the_rest_are_named():
    """Six agents into one checkout is how you get collisions — this workspace
    logged 8. Different repos run in parallel; the same repo queues, and the
    queued ones are reported rather than silently dropped."""
    import go as g2
    launched = []

    def fake_launcher(cwd, prompt, name, model=""):
        launched.append(cwd)
        return True

    with tempfile.TemporaryDirectory() as t:
        gdir = os.path.join(t, "goals"); os.makedirs(gdir)
        same = os.path.join(t, "repo-a"); os.makedirs(same)
        other = os.path.join(t, "repo-b"); os.makedirs(other)
        # three goals, two of them in the SAME checkout
        for i, cwd in enumerate([same, same, other]):
            with open(os.path.join(gdir, "g%d.md" % i), "w") as f:
                f.write(GOAL.replace("g-a", "g-%d" % i)
                        + "\n\ncwd: %s\n" % cwd)
        res = g2.run(goals_dir=gdir, ledger_path=os.path.join(t, "d.jsonl"),
                     history_path=os.path.join(t, "h.jsonl"),
                     meditation_dir=os.path.join(t, "med"),
                     launcher=fake_launcher)
        # never two into one checkout
        assert len(launched) == len(set(os.path.realpath(c) for c in launched)), \
            launched
        for d in res.get("deferred", []):
            assert d["waiting_on"] and d["why"], d


def test_launch_report_carries_title_milestone_and_place():
    """"Launched 2 agent(s):" followed by slugs was the whole report — nothing
    a person could use. The detail must survive from the launch site."""
    import go as go2
    with tempfile.TemporaryDirectory() as t:
        gdir = os.path.join(t, "goals"); os.makedirs(gdir)
        with open(os.path.join(gdir, "ship-widget.md"), "w") as f:
            f.write("---\nname: ship-widget\ntitle: Ship the widget — checkout"
                    " live\ncwd: %s\n---\n- [ ] wire the payment key\n" % t)
        # Isolate the world. Without meditation_dir/store_dir this reads the
        # REAL repair queue, and `go` does repair before goals — so on any
        # machine with an open queue the goal never launches and this test
        # fails for a reason that has nothing to do with what it checks.
        med = os.path.join(t, "med"); os.makedirs(med)
        store = os.path.join(med, "nidra_store"); os.makedirs(store)
        res = go2.run(launcher=lambda c, p, n, m="": True, goals_dir=gdir,
                      meditation_dir=med, store_dir=store,
                      ledger_path=os.path.join(t, "l.jsonl"))
        d = (res.get("launched") or [{}])[0]
        assert d.get("title") == "Ship the widget", d
        assert d.get("doing") == "wire the payment key", d
        assert d.get("cwd"), "where it runs is part of the report"


def test_continuation_threads_are_DISPATCHABLE_not_pasteable():
    """The whole point, and the thing that was missing.

    /meditate splits a tangled session into per-thread continuation chats,
    each carrying a ready kickoff prompt. `go` dispatched goals and the repair
    queue and had never heard of those chats — two parallel systems that never
    met, so every split ended in "here is a prompt, paste it somewhere". That
    is the manual system, wearing an automation costume.

    A live thread with a kickoff is work the fleet can start on its own."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        sess = os.path.join(d, "sessions", "aaa-thing")
        os.makedirs(sess)
        with open(os.path.join(sess, "INDEX.md"), "w") as f:
            f.write("| # | Thread | vrtti | Status | Memory |\n"
                    "|---|---|---|---|---|\n"
                    "| 1 | Finish the thing | x | 🟢 live | → **finish.md** |\n"
                    "| 2 | Already done | x | ✅ settled | — |\n")
        with open(os.path.join(sess, "finish.md"), "w") as f:
            f.write("# Finish the thing\n\n## Start a fresh chat with\n\n"
                    "```\ncd /tmp\n```\n\nDo the remaining work on the thing.\n")
        got = go.thread_work(os.path.join(d, "sessions"), cwd_for=lambda s: d)
        assert len(got) == 1, got                      # settled row excluded
        assert "remaining work" in got[0]["prompt"], got[0]
        assert got[0]["cwd"], "a thread must carry its own cwd"


def test_a_thread_without_a_kickoff_is_not_dispatched():
    """Half a chat is not work. Dispatching an agent with no instruction
    burns ~35k boot tokens to accomplish nothing."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        sess = os.path.join(d, "sessions", "bbb-empty")
        os.makedirs(sess)
        with open(os.path.join(sess, "INDEX.md"), "w") as f:
            f.write("| # | Thread | v | Status | Memory |\n|---|---|---|---|---|\n"
                    "| 1 | No chat here | x | 🟢 live | [[some-memory]] |\n")
        assert go.thread_work(os.path.join(d, "sessions")) == []


def test_the_same_thread_is_not_dispatched_twice():
    """Without this the heartbeat re-opens the same agent every hour."""
    import tempfile, json as _j, time as _t
    with tempfile.TemporaryDirectory() as d:
        sess = os.path.join(d, "sessions", "ccc-x")
        os.makedirs(sess)
        with open(os.path.join(sess, "INDEX.md"), "w") as f:
            f.write("| # | Thread | v | Status | Memory |\n|---|---|---|---|---|\n"
                    "| 1 | Do it | x | 🟢 live | → **c.md** |\n")
        with open(os.path.join(sess, "c.md"), "w") as f:
            f.write("## Start a fresh chat with\n\ncd /tmp\n\nDo the work.\n")
        led = os.path.join(d, "dispatch.jsonl")
        with open(led, "w") as f:
            f.write(_j.dumps({"name": "thread-ccc-x-c", "ts": _t.time()}) + "\n")
        got = go.thread_work(os.path.join(d, "sessions"), ledger_path=led,
                             cwd_for=lambda s: d)
        assert got == [], "re-dispatched a thread already sent: %s" % got


def test_a_thread_whose_cwd_cannot_be_resolved_is_not_dispatched():
    """Unknown is not a guess — the same rule the grader learned.

    Eight live threads come from July sessions whose transcripts are gone, so
    find_session_cwd cannot resolve them and falls back to a default. Under
    automation that means eight agents opening in a directory nobody chose,
    on threads from a month ago. A thread the tool cannot place is one it must
    not start."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        sess = os.path.join(d, "sessions", "ffffffff-ghost")
        os.makedirs(sess)
        with open(os.path.join(sess, "INDEX.md"), "w") as f:
            f.write("| # | Thread | v | Status | Memory |\n|---|---|---|---|---|\n"
                    "| 1 | Old thread | x | 🟢 live | → **g.md** |\n")
        with open(os.path.join(sess, "g.md"), "w") as f:
            f.write("## Start a fresh chat with\n\ncd /tmp\n\nDo the old work.\n")
        got = go.thread_work(os.path.join(d, "sessions"),
                             ledger_path=os.path.join(d, "l.jsonl"))
        assert got == [], "dispatched a thread with an unresolvable cwd: %s" % got


def test_auto_holds_while_someone_is_at_the_keyboard():
    """Automation must not start agents into files under a live hand.

    The whole tool exists to stop two writers colliding; an unattended fleet
    that dispatches while the owner is typing is that collision, scheduled.
    Idle time comes from attention.py (HIDIdleTime, exact), and if it cannot
    be read we assume PRESENT — unknown is not away."""
    assert go.auto_should_run(idle_s=5.0)["run"] is False
    assert go.auto_should_run(idle_s=None)["run"] is False, "unknown treated as away"
    assert go.auto_should_run(idle_s=3600.0)["run"] is True


def test_auto_caps_the_fleet():
    """A dispatched agent boots at ~35k tokens before reading its first
    instruction. Unbounded overnight dispatch is a bill, not momentum."""
    d = go.auto_should_run(idle_s=3600.0)
    assert 0 < d["budget"] <= go.AUTO_MAX_AGENTS


def test_auto_reports_when_it_holds():
    """A gate that declines silently reads as 'there was nothing to do'."""
    d = go.auto_should_run(idle_s=5.0)
    assert d["why"] and "here" in d["why"].lower(), d


def test_dispatch_falls_back_to_HEADLESS_when_the_window_fails():
    """Measured live: the automation fired, the gate opened correctly (away
    115 min), it selected the right work — and dispatched ZERO.

    Every launch failed inside osascript: the first timed out after 75s, the
    next two exited non-zero. The cause is a conflict built into the design:
    the gate dispatches when the owner is AWAY, away almost always means the
    DISPLAY IS OFF (pmset shows it off 21:26-22:11, exactly when the beat
    fired), and driving Terminal through System Events needs an awake display.
    The two conditions are mutually exclusive.

    No display detector is available here — pmset's IODisplayWrangler probe
    returns "Internal failure" on Apple Silicon — so do not predict. Try the
    window; when it fails, run the agent headless, which needs no GUI at all
    (verified: `claude -p` returns normally with the screen off)."""
    calls = []

    def gui_fails(cwd, prompt, name, model=""):
        calls.append(("gui", name))
        return False

    def headless(cwd, prompt, name, model=""):
        calls.append(("headless", name))
        return True

    ok = go.dispatch_one("/tmp", "do the thing", "probe",
                         gui=gui_fails, headless=headless)
    assert ok is True, "fell back to nothing"
    assert [c[0] for c in calls] == ["gui", "headless"], calls


def test_headless_is_not_used_when_the_window_opens():
    """A watchable window is better when it is available — do not downgrade."""
    calls = []
    ok = go.dispatch_one("/tmp", "p", "probe",
                         gui=lambda *a, **k: calls.append("gui") or True,
                         headless=lambda *a, **k: calls.append("headless") or True)
    assert ok is True and calls == ["gui"], calls


def test_a_raising_launcher_still_falls_back():
    """osascript timing out raises rather than returning False."""
    def boom(*a, **k):
        raise RuntimeError("osascript timed out after 75 seconds")
    assert go.dispatch_one("/tmp", "p", "probe", gui=boom,
                           headless=lambda *a, **k: True) is True


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
