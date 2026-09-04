"""The swarm's control model — the harness's, given to CLAUD-E.

Measured 2026-09-03 before any of this existed: go.py passed exactly two of
the CLI's control flags (--output-format json, --max-budget-usd); every
unattended agent ran --dangerously-skip-permissions; every log recorded a
session_id nothing read; outcome recorded for 0 of 8 runs; the only outcome
signal (ok ∧ cost>0) was 100% by construction, so pick() had never made a
decision. Two of the six goal cwds were dirty with another session's work
and one was not a git repo at all.

Proven live the same day, one call each: `--json-schema` output lands in
`structured_output`; `--permission-mode dontAsk` with stdin closed does not
hang.

Every test here captures the argv a fake Popen receives — the falsifier for
"the flag is passed" is reading the flag, not trusting the docstring.

Run: python3 ~/.claude/skills/meditate/test_go_control.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

import go  # noqa: E402
import models  # noqa: E402


class _Popen:
    """Records what would have been launched. Nothing runs."""
    calls: list = []

    def __init__(self, argv, **kw):
        _Popen.calls.append({"argv": argv, "cwd": kw.get("cwd")})
        self.pid = 4242


def _git_repo(t):
    d = os.path.join(t, "repo")
    os.makedirs(d)
    subprocess.run(["git", "init", "-q", "-b", "main", d], check=True)
    open(os.path.join(d, "a.txt"), "w").write("a\n")
    subprocess.run(["git", "-C", d, "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    return d


def _launch(cwd, name="goal-x", **kw):
    """Always into a scratch log dir. The first cut wrote fake logs — pid
    4242, no result line — into the owner's real agents/ dir, where the
    live AGENTS panel then listed them as 'died without reporting'."""
    _Popen.calls = []
    old = go.WORKTREE_ROOT
    old_logs = go.HEADLESS_LOG_DIR
    go.WORKTREE_ROOT = kw.pop("worktree_root", go.WORKTREE_ROOT)
    if not os.path.commonpath([go.HEADLESS_LOG_DIR, tempfile.gettempdir()]) == tempfile.gettempdir():
        go.HEADLESS_LOG_DIR = tempfile.mkdtemp(prefix="agents-")
    try:
        ok = go._headless(cwd, "do the thing", name, "sonnet", "high", 1.0,
                          popen=_Popen, **kw)
    finally:
        go.WORKTREE_ROOT = old
        if go.HEADLESS_LOG_DIR != old_logs:
            _launch.last_logs = go.HEADLESS_LOG_DIR
            go.HEADLESS_LOG_DIR = old_logs
    assert ok and _Popen.calls, "nothing launched"
    return _Popen.calls[-1]


def _flag(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


# ---------------------------------------------------------------------------
# the flags
# ---------------------------------------------------------------------------

def test_no_agent_runs_with_permissions_OFF():
    """8 full-permission unattended agents in 24 h was the largest exposure
    the infra audit found. Never bypass. A role that edits runs acceptEdits
    — measured 2026-09-04: under dontAsk, Write is denied even when named in
    --allowedTools, and three real runs ($7.42) designed a fix they could
    not write. A role that only reads stays dontAsk."""
    with tempfile.TemporaryDirectory() as t:
        c = _launch(_git_repo(t), worktree_root=os.path.join(t, "wt"))
        assert "--dangerously-skip-permissions" not in c["argv"], c["argv"]
        assert "bypassPermissions" not in c["argv"]
        assert _flag(c["argv"], "--permission-mode") == "acceptEdits", c["argv"]
        c2 = _launch(os.path.join(t, "repo"), name="revive-look", worktree_root=os.path.join(t, "wt2"))
        assert _flag(c2["argv"], "--permission-mode") == "dontAsk", c2["argv"]


def test_the_run_ends_with_a_TYPED_result():
    with tempfile.TemporaryDirectory() as t:
        c = _launch(_git_repo(t), worktree_root=os.path.join(t, "wt"))
        schema = json.loads(_flag(c["argv"], "--json-schema"))
        for k in ("did", "commits", "pushed", "milestone_ticked", "tests",
                  "blocked_on", "next"):
            assert k in schema["required"], k


def test_the_role_is_passed_and_selected():
    """--agents carries the role; --agent selects it. The kind is the first
    dash-segment of the name, which is what the ledger already keys on."""
    with tempfile.TemporaryDirectory() as t:
        c = _launch(_git_repo(t), name="repair-mem_abc",
                    worktree_root=os.path.join(t, "wt"))
        assert _flag(c["argv"], "--agent") == "repair", c["argv"]
        agents = json.loads(_flag(c["argv"], "--agents"))
        assert "repair" in agents and agents["repair"]["prompt"], agents
        assert "--allowedTools" in c["argv"] and "--disallowedTools" in c["argv"]


def test_revive_cannot_EDIT_or_PUSH():
    """Deny wins, and --allowedTools is additive to the user's 38-rule
    allowlist — so a read-only role must be expressed as denials."""
    with tempfile.TemporaryDirectory() as t:
        c = _launch(_git_repo(t), name="revive-old-thing",
                    worktree_root=os.path.join(t, "wt"))
        i = c["argv"].index("--disallowedTools")
        denied = " ".join(c["argv"][i + 1:i + 2])
        for tool in ("Edit", "Write", "Bash(git push:*)", "Bash(git commit:*)"):
            assert tool in denied, "%s not denied for revive: %s" % (tool, denied)


def test_the_role_prompt_carries_the_OWNERS_rules():
    """The role IS the methodology. If the creed derives, it rides along."""
    with tempfile.TemporaryDirectory() as t:
        c = _launch(_git_repo(t), worktree_root=os.path.join(t, "wt"))
        prompt = json.loads(_flag(c["argv"], "--agents"))["goal"]["prompt"]
        assert "RESULT" in prompt and "blocked_on" in prompt
        try:
            import creed
            block = creed.render("action", budget=2600)
        except Exception:
            block = ""
        if block.strip():
            assert block.strip()[:60] in prompt, "creed not in the role prompt"


# ---------------------------------------------------------------------------
# identity, continuation
# ---------------------------------------------------------------------------

def test_the_session_id_is_CHOSEN_and_written_where_reconcile_reads():
    """Every log already recorded a session_id nothing read. Now it is chosen
    up front, passed as --session-id, and written as a header line — while
    the filename keeps <stamp>-<kind>-<name>.log, because kind attribution
    splits the filename on '-' and a UUID there would file every run under a
    kind no code has."""
    with tempfile.TemporaryDirectory() as t:
        old = go.HEADLESS_LOG_DIR
        go.HEADLESS_LOG_DIR = os.path.join(t, "logs")
        try:
            c = _launch(_git_repo(t), worktree_root=os.path.join(t, "wt"))
            sid = _flag(c["argv"], "--session-id")
            assert sid and len(sid) == 36, sid
            log = go._headless.last["log"]
            head = open(log).read().splitlines()[:8]
            assert any(l == "# session: " + sid for l in head), head
            base = os.path.basename(log)
            assert base.split("-", 2)[-1].startswith("goal-"), base
            assert sid not in base
        finally:
            go.HEADLESS_LOG_DIR = old


def test_continue_RESUMES_the_same_session():
    """SendMessage for the swarm: the id in the log becomes --resume."""
    with tempfile.TemporaryDirectory() as t:
        logs = os.path.join(t, "logs"); os.makedirs(logs)
        sid = "11111111-2222-3333-4444-555555555555"
        open(os.path.join(logs, "20260903-010000-goal-x.log"), "w").write(
            "# goal-x\n# cwd: %s\n# model: sonnet effort: high budget: 1\n"
            "# session: %s\n# worktree: \n# branch: \n\n" % (t, sid))
        old = go.HEADLESS_LOG_DIR
        go.HEADLESS_LOG_DIR = logs
        _Popen.calls = []
        try:
            r = go.continue_agent("goal-x", "also fix the tests", popen=_Popen)
        finally:
            go.HEADLESS_LOG_DIR = old
        assert r["started"], r
        argv = _Popen.calls[-1]["argv"]
        assert _flag(argv, "--resume") == sid, argv
        assert "also fix the tests" in argv
        assert _flag(argv, "--permission-mode") == "acceptEdits"
        assert "--max-budget-usd" not in argv, "no cap asked for, none invented"
        _Popen.calls = []
        go.HEADLESS_LOG_DIR = logs
        try:
            go.continue_agent("goal-x", "more", popen=_Popen, budget_usd=6.0)
        finally:
            go.HEADLESS_LOG_DIR = old
        assert _flag(_Popen.calls[-1]["argv"], "--max-budget-usd") == "6.0", "a resume must carry its cap"
        head = open(r["log"]).read().splitlines()[:8]
        assert "# session: " + sid in head, head
        assert any(l.startswith("# continues: ") for l in head), head


def test_continue_with_NO_such_agent_says_so():
    with tempfile.TemporaryDirectory() as t:
        old = go.HEADLESS_LOG_DIR
        go.HEADLESS_LOG_DIR = t
        try:
            r = go.continue_agent("goal-nope", "hi", popen=_Popen)
        finally:
            go.HEADLESS_LOG_DIR = old
        assert r["started"] is False and "no log" in r["why"], r


# ---------------------------------------------------------------------------
# isolation
# ---------------------------------------------------------------------------

def test_a_repo_cwd_gets_its_OWN_worktree_and_branch():
    with tempfile.TemporaryDirectory() as t:
        repo = _git_repo(t)
        c = _launch(repo, worktree_root=os.path.join(t, "wt"))
        wt = c["cwd"]
        assert wt != repo and wt.startswith(os.path.join(t, "wt")), wt
        assert os.path.isfile(os.path.join(wt, ".git")), "not a worktree"
        branch = subprocess.run(["git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        assert branch.startswith("agent/goal-x-"), branch
        assert go._headless.last["worktree"] == wt


def test_a_NON_repo_cwd_dispatches_in_place_and_SAYS_so():
    """purangpt-mobile-live's cwd is a parent directory, not a repo — 100% of
    then-current dispatches. A worktree step that aborted would have
    dispatched nothing and said nothing."""
    with tempfile.TemporaryDirectory() as t:
        plain = os.path.join(t, "plain"); os.makedirs(plain)
        c = _launch(plain, worktree_root=os.path.join(t, "wt"))
        assert c["cwd"] == plain
        assert "not a repo" in go._headless.last["why"], go._headless.last


def test_unattended_dispatch_SKIPS_a_goal_it_cannot_isolate():
    """The owner's rule: never edit another session's uncommitted work. A
    non-repo cwd cannot be isolated, so unattended mode leaves it alone and
    names the fix."""
    with tempfile.TemporaryDirectory() as t:
        gdir = os.path.join(t, "goals"); os.makedirs(gdir)
        plain = os.path.join(t, "plain"); os.makedirs(plain)
        open(os.path.join(gdir, "g.md"), "w").write(
            "---\nname: g-plain\ntitle: Plain\nproject: p\ncwd: %s\nstatus: active\n---\n"
            "## Milestones\n- [ ] something open\n" % plain)
        med = os.path.join(t, "med"); os.makedirs(os.path.join(med, "nidra_store"))
        launched = []
        res = go.run(n=2, goals_dir=gdir, meditation_dir=med,
                     store_dir=os.path.join(med, "nidra_store"),
                     ledger_path=os.path.join(t, "d.jsonl"),
                     history_path=os.path.join(t, "h.jsonl"),
                     launcher=lambda c, p, n, m="": launched.append(n) or True,
                     unattended=True)
        assert not launched, launched
        sk = [s for s in res.get("skipped", []) if s["goal"] == "g-plain"]
        assert sk and "not a git repo" in sk[0]["why"], res.get("skipped")


def test_two_goals_in_ONE_repo_no_longer_queue_behind_each_other():
    """`taken` was keyed on the cwd, and 4 of 6 goals share 2 cwds — a two-row
    plan in one repo launched one agent. Worktrees make the cwd irrelevant."""
    with tempfile.TemporaryDirectory() as t:
        repo = _git_repo(t)
        gdir = os.path.join(t, "goals"); os.makedirs(gdir)
        for n in ("g-one", "g-two"):
            open(os.path.join(gdir, n + ".md"), "w").write(
                "---\nname: %s\ntitle: %s\nproject: p\ncwd: %s\nstatus: active\n---\n"
                "## Milestones\n- [ ] open\n" % (n, n, repo))
        med = os.path.join(t, "med"); os.makedirs(os.path.join(med, "nidra_store"))
        launched = []
        res = go.run(n=2, goals_dir=gdir, meditation_dir=med,
                     store_dir=os.path.join(med, "nidra_store"),
                     ledger_path=os.path.join(t, "d.jsonl"),
                     history_path=os.path.join(t, "h.jsonl"),
                     launcher=lambda c, p, n, m="": launched.append(n) or True)
        assert len(launched) == 2, (launched, res.get("deferred"))


def test_run_FOLDS_finished_agents_in_before_choosing():
    """8 finished runs sat unrecorded because reconcile() was called from one
    place: the dashboard's /api/spend. go.run() is hit by the heartbeat and
    by every manual go — so it reconciles first, and pick() sees them."""
    calls = []
    real = models.reconcile
    models.reconcile = lambda *a, **k: calls.append(1) or {"added": 0, "pending": 0, "rows": []}
    try:
        with tempfile.TemporaryDirectory() as t:
            med = os.path.join(t, "med"); os.makedirs(os.path.join(med, "nidra_store"))
            gdir = os.path.join(t, "goals"); os.makedirs(gdir)
            go.run(n=1, goals_dir=gdir, meditation_dir=med,
                   store_dir=os.path.join(med, "nidra_store"),
                   ledger_path=os.path.join(t, "d.jsonl"),
                   history_path=os.path.join(t, "h.jsonl"),
                   launcher=lambda *a, **k: True)
    finally:
        models.reconcile = real
    assert calls, "run() did not reconcile first"


# ---------------------------------------------------------------------------
# the ledger learns what happened
# ---------------------------------------------------------------------------

def _log(dirp, name, cwd, res, extra_head=""):
    p = os.path.join(dirp, "20260903-020000-%s.log" % name)
    open(p, "w").write("# %s\n# cwd: %s\n# model: opus effort: max budget: 2\n"
                       "# session: abc\n%s\n%s\n" % (name, cwd, extra_head,
                                                    json.dumps(res)))
    return p


def _res(**kw):
    base = {"type": "result", "subtype": "success", "is_error": False,
            "total_cost_usd": 1.0, "num_turns": 3, "duration_ms": 10,
            "session_id": "abc", "permission_denials": [],
            "modelUsage": {"claude-opus-4-8": {"costUSD": 1.0, "outputTokens": 10}}}
    base.update(kw)
    return base


def test_reconcile_records_the_TYPED_result_as_produced():
    with tempfile.TemporaryDirectory() as t:
        repo = _git_repo(t)
        sha = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        logs = os.path.join(t, "logs"); os.makedirs(logs)
        _log(logs, "goal-a", repo, _res(structured_output={
            "did": ["fixed it"], "commits": [sha[:9]], "pushed": False,
            "milestone_ticked": None, "tests": {"ran": 3, "green": True},
            "blocked_on": None, "next": "push"}))
        row = models.reconcile(log_dir=logs, ledger=os.path.join(t, "s.jsonl"))["rows"][0]
        assert row["produced"]["did"] == ["fixed it"], row
        assert row["verified_commits"] == [sha[:9]], row
        assert row["session"] == "abc"


def test_a_CLAIMED_commit_that_does_not_exist_is_not_verified():
    """The agent's RESULT is a claim. `git cat-file -e` is the check."""
    with tempfile.TemporaryDirectory() as t:
        repo = _git_repo(t)
        logs = os.path.join(t, "logs"); os.makedirs(logs)
        _log(logs, "goal-b", repo, _res(structured_output={
            "did": ["x"], "commits": ["deadbeef1"], "pushed": True,
            "milestone_ticked": None, "tests": None, "blocked_on": None, "next": ""}))
        row = models.reconcile(log_dir=logs, ledger=os.path.join(t, "s.jsonl"))["rows"][0]
        assert row["verified_commits"] == [], row
        assert row["produced"]["commits"] == ["deadbeef1"], "the claim itself is kept"


def test_a_run_with_NO_structured_output_records_None_not_a_verdict():
    with tempfile.TemporaryDirectory() as t:
        logs = os.path.join(t, "logs"); os.makedirs(logs)
        _log(logs, "goal-c", t, _res())
        row = models.reconcile(log_dir=logs, ledger=os.path.join(t, "s.jsonl"))["rows"][0]
        assert row["produced"] is None and row["verified_commits"] == [], row


def test_evidence_counts_SHIPPED_not_exited_zero():
    """ok ∧ cost>0 was 8/8 — 100% by construction. A run that verified a
    commit or ticked a milestone shipped; one that did neither did not."""
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "s.jsonl")
        rows = [
            {"name": "goal-a", "model": "m1", "ok": True, "cost_usd": 1,
             "verified_commits": ["abc"], "produced": {"milestone_ticked": None}},
            {"name": "goal-b", "model": "m1", "ok": True, "cost_usd": 1,
             "verified_commits": [], "produced": {"milestone_ticked": None}},
            {"name": "goal-c", "model": "m2", "ok": True, "cost_usd": 1,
             "verified_commits": [], "produced": {"milestone_ticked": "did the thing"}},
            {"name": "goal-d", "model": "m2", "ok": True, "cost_usd": 1,
             "verified_commits": [], "produced": None},
        ]
        open(p, "w").write("".join(json.dumps(r) + "\n" for r in rows))
        old = models.SPEND_LEDGER
        models.SPEND_LEDGER = p
        try:
            ev = models.evidence_for("goal")
        finally:
            models.SPEND_LEDGER = old
        assert ev["by_model"]["m1"] == {"dispatched": 2, "produced": 1}, ev
        assert ev["by_model"]["m2"] == {"dispatched": 2, "produced": 1}, ev


def test_interrupts_are_counted_when_the_message_is_a_LIST():
    """66 transcripts carry `[Request interrupted by user]` and the counter
    read 0 of 15,185 turns: the marker is a list-of-blocks user message and
    only the string branch checked it."""
    with tempfile.TemporaryDirectory() as t:
        pd = os.path.join(t, "projects", "-x"); os.makedirs(pd)
        with open(os.path.join(pd, "s.jsonl"), "w") as f:
            f.write(json.dumps({"type": "assistant", "timestamp": "2026-09-01T00:00:00Z",
                                "message": {"model": "claude-test-1", "content": []}}) + "\n")
            f.write(json.dumps({"type": "user", "timestamp": "2026-09-01T00:00:01Z",
                                "message": {"role": "user", "content": [
                                    {"type": "text", "text": "[Request interrupted by user]"}]}}) + "\n")
        old = models.PROJECTS
        models.PROJECTS = os.path.join(t, "projects")
        try:
            out = models.scan(limit=5)
        finally:
            models.PROJECTS = old
        m = [r for r in out["models"] if r["model"] == "claude-test-1"][0]
        assert m["interrupted"] == 1, m


# ---------------------------------------------------------------------------
# progress you can SEE: the run is a stream, the card reads it
# ---------------------------------------------------------------------------
#
# Verified live 2026-09-03: `--output-format stream-json --verbose` emits one
# event per turn (assistant messages with usage and tool_use blocks, user
# tool results, and the same final `result` object — structured_output and
# total_cost_usd included — so the ledger reads it unchanged). Before this,
# a running agent was a log with a four-line header and nothing else until
# it finished: "running in the background" was a guess, and there was no
# number to put a bar against.

STREAM = "\n".join([
    '{"type":"system","subtype":"init","session_id":"s1"}',
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"looking"}],"usage":{"output_tokens":40}}}',
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Read","input":{}}],"usage":{"output_tokens":20}}}',
    '{"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"x"}]}}',
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"git log"}}],"usage":{"output_tokens":30}}}',
])


def _stream_log(d, name="goal-x", pid=4242, result=False):
    p = os.path.join(d, "20260903-090000-%s.log" % name)
    body = ("# %s\n# cwd: /tmp\n# model: opus effort: max budget: 2\n# session: abc\n"
            "# worktree: /tmp/wt\n# branch: agent/x\n# pid: %d\n\n" % (name, pid)) + STREAM + "\n"
    if result:
        body += json.dumps({"type": "result", "subtype": "success", "is_error": False,
                            "total_cost_usd": 0.5, "num_turns": 3, "structured_output": {"ok": True}}) + "\n"
    open(p, "w").write(body)
    return p


def test_a_dispatch_STREAMS_and_records_its_pid():
    with tempfile.TemporaryDirectory() as t:
        old = go.HEADLESS_LOG_DIR
        go.HEADLESS_LOG_DIR = os.path.join(t, "logs")
        try:
            c = _launch(_git_repo(t), worktree_root=os.path.join(t, "wt"))
            assert _flag(c["argv"], "--output-format") == "stream-json", c["argv"]
            assert "--verbose" in c["argv"], "stream-json needs --verbose or the events are not printed"
            head = [l.rstrip() for l in open(go._headless.last["log"]).read().splitlines()[:9]]
            assert "# pid: 4242" in head, head      # the slot is padded; the value is exact
        finally:
            go.HEADLESS_LOG_DIR = old


def test_live_agents_READ_progress_from_the_stream():
    with tempfile.TemporaryDirectory() as t:
        _stream_log(t)
        rows = go.live_agents(log_dir=t, alive=lambda pid: pid == 4242, now=lambda: time.time())
        assert len(rows) == 1, rows
        a = rows[0]
        assert a["turns"] == 3 and a["tool_calls"] == 2 and a["last_tool"] == "Bash", a
        assert a["alive"] is True and a["name"] == "goal-x" and a["kind"] == "goal"
        assert a["session"] == "abc" and a["worktree"] == "/tmp/wt"
        assert a["out_tokens"] == 90, a


def test_a_FINISHED_agent_is_not_live_and_a_DEAD_one_says_so():
    with tempfile.TemporaryDirectory() as t:
        _stream_log(t, name="goal-done", result=True)
        _stream_log(t, name="goal-dead", pid=1)
        rows = {a["name"]: a for a in go.live_agents(log_dir=t, alive=lambda pid: False)}
        assert "goal-done" not in rows, rows
        assert rows["goal-dead"]["alive"] is False, rows
        assert "died" in rows["goal-dead"]["state"], rows["goal-dead"]


def test_the_bar_is_against_the_KINDS_median_and_overflows_instead_of_lying():
    """A bar pinned at 100% while the run is alive is a lie. Past the median
    it keeps counting, and says so."""
    with tempfile.TemporaryDirectory() as t:
        _stream_log(t)
        rows = go.live_agents(log_dir=t, alive=lambda pid: True, medians={"goal": 2})
        a = rows[0]
        assert a["median_turns"] == 2 and a["progress"] > 1.0, a
        rows = go.live_agents(log_dir=t, alive=lambda pid: True, medians={"goal": 9})
        assert abs(rows[0]["progress"] - 3 / 9) < 1e-9
        rows = go.live_agents(log_dir=t, alive=lambda pid: True, medians={})
        assert rows[0]["median_turns"] is None and rows[0]["progress"] is None


def test_a_log_that_stopped_moving_is_STALLED_not_running():
    with tempfile.TemporaryDirectory() as t:
        p = _stream_log(t)
        old = os.path.getmtime(p)
        rows = go.live_agents(log_dir=t, alive=lambda pid: True, now=lambda: old + go.STALL_AFTER_S + 5)
        assert rows[0]["state"] == "stalled", rows[0]
        rows = go.live_agents(log_dir=t, alive=lambda pid: True, now=lambda: old + 10)
        assert rows[0]["state"] == "running", rows[0]


def test_a_worktree_with_NOTHING_on_it_is_removed_after_the_run():
    """The first live probe — a read-only revive — left its worktree
    forever as 'kept: unpushed (no upstream)'. No commits, clean tree:
    nothing to lose, so it goes; one commit on the branch and it stays."""
    with tempfile.TemporaryDirectory() as t:
        repo = _git_repo(t)
        logs = os.path.join(t, "logs"); os.makedirs(logs)
        old_root = go.WORKTREE_ROOT
        go.WORKTREE_ROOT = os.path.join(t, "wt")    # never the owner's real worktrees dir
        try:
            _worktree_case(repo, logs, t)
        finally:
            go.WORKTREE_ROOT = old_root


def _worktree_case(repo, logs, t):
    if True:
        for name in ("revive-clean", "goal-committed"):
            wt, branch, why = go.make_worktree(repo, name, "20260903-000000")
            assert branch, why
            if name == "goal-committed":
                open(os.path.join(wt, "b.txt"), "w").write("b\n")
                subprocess.run(["git", "-C", wt, "add", "b.txt"], check=True)
                subprocess.run(["git", "-C", wt, "-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-qm", "work"], check=True)
            open(os.path.join(logs, "20260903-000000-%s.log" % name), "w").write(
                "# %s\n# cwd: %s\n# model: sonnet effort: low budget: 1\n# session: s\n"
                "# worktree: %s\n# branch: %s\n\n%s\n" % (name, repo, wt, branch, json.dumps(_res())))
        rows = {r["name"]: r for r in models.reconcile(log_dir=logs, ledger=os.path.join(t, "s.jsonl"))["rows"]}
        assert rows["revive-clean"]["worktree_state"].startswith("removed"), rows["revive-clean"]
        assert not os.path.isdir(rows["revive-clean"]["worktree"])
        assert rows["goal-committed"]["worktree_state"].startswith("kept"), rows["goal-committed"]
        assert os.path.isdir(rows["goal-committed"]["worktree"])


def test_the_claude_binary_is_resolved_to_a_PATH_that_exists():
    """The brain runs under launchd with PATH=/opt/homebrew/bin:/usr/bin:/bin.
    `claude` lives in ~/.local/bin. Every console-driven dispatch died with
    "claude: No such file or directory" — including the one real GO the
    campaign ever got (2026-09-03 06:38 UTC), which nobody saw because the
    log held one line and $0. The argv carries an absolute path now."""
    b = go.claude_bin()
    assert os.path.isabs(b) or b == "claude", b
    if b != "claude":
        assert os.access(b, os.X_OK), b
    with tempfile.TemporaryDirectory() as t:
        c = _launch(_git_repo(t), worktree_root=os.path.join(t, "wt"))
        argv = c["argv"]
        exe = argv[2] if argv[0] == "caffeinate" else argv[0]
        assert exe == b, (exe, b)


def test_continue_RE_ADDS_a_removed_worktree_from_its_branch():
    """Live 2026-09-03: the first console `continue` reached the binary and
    got "No conversation found with session ID" — the probe's worktree had
    been removed, and a session is bound to the directory it ran in."""
    with tempfile.TemporaryDirectory() as t:
        repo = _git_repo(t)
        old_root, old_logs = go.WORKTREE_ROOT, go.HEADLESS_LOG_DIR
        go.WORKTREE_ROOT = os.path.join(t, "wt"); go.HEADLESS_LOG_DIR = os.path.join(t, "logs")
        os.makedirs(go.HEADLESS_LOG_DIR)
        try:
            wt, branch, why = go.make_worktree(repo, "goal-x", "20260903-000001")
            assert branch, why
            open(os.path.join(go.HEADLESS_LOG_DIR, "20260903-000001-goal-x.log"), "w").write(
                "# goal-x\n# cwd: %s\n# model: sonnet effort: high budget: 1\n# session: sid-1\n"
                "# worktree: %s\n# branch: %s\n\n" % (repo, wt, branch))
            subprocess.run(["git", "-C", repo, "worktree", "remove", wt], check=True)
            assert not os.path.isdir(wt)
            _Popen.calls = []
            r = go.continue_agent("goal-x", "carry on", popen=_Popen)
            assert r["started"], r
            assert os.path.isdir(wt) and _Popen.calls[-1]["cwd"] == wt, (r, _Popen.calls)
            # branch gone too -> honest refusal, not a resume from the wrong dir
            subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt], check=True)
            subprocess.run(["git", "-C", repo, "branch", "-D", branch], check=True)
            r2 = go.continue_agent("goal-x", "carry on", popen=_Popen)
            assert r2["started"] is False and "cannot be resumed" in r2["why"], r2
        finally:
            go.WORKTREE_ROOT, go.HEADLESS_LOG_DIR = old_root, old_logs


def test_a_run_the_CLI_refused_shows_the_refusal_verbatim():
    """Two real ones: "claude: No such file or directory" and "No
    conversation found with session ID …". The panel read them as
    'no pid recorded' — a state about the header, not about what happened."""
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "20260903-000002-goal-y.log")
        open(p, "w").write("# goal-y\n# cwd: /tmp\n# model: sonnet effort: low budget: 1\n# session: s\n"
                           "# worktree: \n# branch: \n\nclaude: No such file or directory\n")
        rows = go.live_agents(log_dir=t, alive=lambda pid: False)
        assert rows and rows[0]["state"].startswith("died: claude: No such file"), rows


def test_continue_records_its_PID_too():
    with tempfile.TemporaryDirectory() as t:
        logs = os.path.join(t, "logs"); os.makedirs(logs)
        open(os.path.join(logs, "20260903-010000-goal-z.log"), "w").write(
            "# goal-z\n# cwd: %s\n# model: sonnet effort: high budget: 1\n# session: sid-z\n# worktree: \n# branch: \n\n" % t)
        old = go.HEADLESS_LOG_DIR; go.HEADLESS_LOG_DIR = logs
        try:
            r = go.continue_agent("goal-z", "more", popen=_Popen)
        finally:
            go.HEADLESS_LOG_DIR = old
        head = [l.rstrip() for l in open(r["log"]).read().splitlines()[:10]]
        assert "# pid: 4242" in head, head


def test_a_worktree_is_cut_from_ORIGIN_when_the_checkout_is_stale():
    """Standing rule (owner, 2026-09-03): both PuranGPT checkouts are far
    behind origin/main; anything grounded on the working tree is wrong.
    A worktree from local HEAD would hand the agent a 283-commit-old base.
    When origin has the branch, the worktree starts from origin's tip."""
    with tempfile.TemporaryDirectory() as t:
        origin = os.path.join(t, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], check=True)
        repo = _git_repo(t)
        subprocess.run(["git", "-C", repo, "remote", "add", "origin", origin], check=True)
        subprocess.run(["git", "-C", repo, "push", "-q", "origin", "main"], check=True)
        # origin moves on; the local checkout does not
        other = os.path.join(t, "other")
        subprocess.run(["git", "clone", "-q", origin, other], check=True)
        open(os.path.join(other, "new.txt"), "w").write("n\n")
        subprocess.run(["git", "-C", other, "add", "new.txt"], check=True)
        subprocess.run(["git", "-C", other, "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "newer"], check=True)
        subprocess.run(["git", "-C", other, "push", "-q", "origin", "main"], check=True)
        old_root = go.WORKTREE_ROOT; go.WORKTREE_ROOT = os.path.join(t, "wt")
        try:
            wt, branch, why = go.make_worktree(repo, "goal-s", "20260904-000000")
            assert branch, why
            assert os.path.exists(os.path.join(wt, "new.txt")), "worktree was cut from the stale local HEAD"
            assert "origin/main" in why, why
        finally:
            go.WORKTREE_ROOT = old_root


def test_elapsed_counts_from_the_DISPATCH_not_the_last_write():
    """ctime on macOS moves on every write, so a busy agent read 'elapsed
    1s' for its whole life. The filename carries the dispatch stamp."""
    with tempfile.TemporaryDirectory() as t:
        _stream_log(t)      # 20260903-090000
        import calendar
        t0 = calendar.timegm(time.strptime("20260903-090000", "%Y%m%d-%H%M%S"))
        rows = go.live_agents(log_dir=t, alive=lambda pid: True, now=lambda: t0 + 125, max_age_h=1e9)
        assert rows[0]["elapsed_s"] == 125, rows[0]["elapsed_s"]


def test_a_DEAD_log_is_ledgered_as_a_failure_and_leaves_the_live_list():
    """'died: claude: No such file or directory' sat on the panel for a day.
    A run the CLI refused is a failed run: reconcile records it (cost 0,
    subtype died) and the live list skips what the ledger holds."""
    with tempfile.TemporaryDirectory() as t:
        logs = os.path.join(t, "logs"); os.makedirs(logs)
        p = os.path.join(logs, "20260903-000002-goal-y.log")
        open(p, "w").write("# goal-y\n# cwd: /tmp\n# model: sonnet effort: low budget: 1\n# session: s\n"
                           "# worktree: \n# branch: \n\nclaude: No such file or directory\n")
        old = os.path.getmtime(p); os.utime(p, (old - 3600, old - 3600))
        led = os.path.join(t, "s.jsonl")
        r = models.reconcile(log_dir=logs, ledger=led)
        row = [x for x in r["rows"] if x["name"] == "goal-y"]
        assert row and row[0]["ok"] is False and row[0]["subtype"] == "died" and row[0]["cost_usd"] == 0, r
        assert "No such file" in row[0]["died"], row[0]
        assert go.live_agents(log_dir=logs, alive=lambda pid: False, ledger=led) == []
        # a young log that has not started yet is NOT a death
        p2 = os.path.join(logs, "20260903-000003-goal-z.log")
        open(p2, "w").write("# goal-z\n# cwd: /tmp\n# model: sonnet effort: low budget: 1\n# session: s\n# worktree: \n# branch: \n\n")
        r2 = models.reconcile(log_dir=logs, ledger=led)
        assert not [x for x in r2["rows"] if x["name"] == "goal-z"], r2


def test_a_goal_agent_can_actually_WORK_under_dontAsk():
    """The first real campaign agent (2026-09-04, opus, $1.16, 10 turns, 4
    denials): 'Bash tool is denied session-wide'. The allowlist named git
    subcommands one by one, so `git status --short` and every `cd … && …`
    were refused, and npm/tsc/gradlew were not there at all. Programs are
    allowed; the dangerous forms are denied by name; deny wins."""
    with tempfile.TemporaryDirectory() as t:
        c = _launch(_git_repo(t), worktree_root=os.path.join(t, "wt"))
        allowed = c["argv"][c["argv"].index("--allowedTools") + 1]
        denied = c["argv"][c["argv"].index("--disallowedTools") + 1]
        for need in ("Bash(git:*)", "Bash(cd:*)", "Bash(npm:*)", "Bash(python3:*)", "Bash(./gradlew:*)",
                     # second real run: Bash unblocked, then Write/Edit/`cat >` denied — the fix was
                     # designed and could not be written. dontAsk denies every tool not NAMED.
                     "Edit", "Write", "MultiEdit", "Read", "Glob", "Grep"):
            assert need in allowed, need
        for bad in ("Bash(git push --force:*)", "Bash(rm -rf:*)", "Bash(sudo:*)", "Bash(git reset --hard:*)"):
            assert bad in denied, bad


def test_a_RESUMED_run_says_its_progress_is_not_streamed():
    """A --resume run's stream carried only the final result event (measured
    2026-09-04: Counter({'result': 1}) after 30 turns and 577 s). The card
    read 'running · 0 turns' for ten minutes. A resumed log says so."""
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "20260904-000004-goal-r.log")
        open(p, "w").write("# goal-r\n# cwd: /tmp\n# model: opus effort: max budget: 2\n# session: s\n"
                           "# worktree: \n# branch: \n# continues: 20260904-000001-goal-r.log\n# pid: 4242\n\n")
        rows = go.live_agents(log_dir=t, alive=lambda pid: True)
        assert rows[0]["resumed"] is True and "not streamed" in rows[0]["state"], rows[0]


def test_worktrees_live_OUTSIDE_the_claude_config_tree():
    """Probe 2026-09-04 under acceptEdits: Write DENIED — 'rejected as a
    sensitive file' — because the worktree sat under ~/.claude, which
    Claude Code guards. Headless, a guard that asks is a guard that denies."""
    assert not go.WORKTREE_ROOT.startswith(os.path.expanduser("~/.claude")), go.WORKTREE_ROOT


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
