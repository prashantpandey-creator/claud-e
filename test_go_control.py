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
    _Popen.calls = []
    old = go.WORKTREE_ROOT
    go.WORKTREE_ROOT = kw.pop("worktree_root", go.WORKTREE_ROOT)
    try:
        ok = go._headless(cwd, "do the thing", name, "sonnet", "high", 1.0,
                          popen=_Popen, **kw)
    finally:
        go.WORKTREE_ROOT = old
    assert ok and _Popen.calls, "nothing launched"
    return _Popen.calls[-1]


def _flag(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


# ---------------------------------------------------------------------------
# the flags
# ---------------------------------------------------------------------------

def test_no_agent_runs_with_permissions_OFF():
    """8 full-permission unattended agents in 24 h was the largest exposure
    the infra audit found. dontAsk is the unattended mode; a denied tool
    ends up in blocked_on, not in a push."""
    with tempfile.TemporaryDirectory() as t:
        c = _launch(_git_repo(t), worktree_root=os.path.join(t, "wt"))
        assert "--dangerously-skip-permissions" not in c["argv"], c["argv"]
        assert _flag(c["argv"], "--permission-mode") == "dontAsk", c["argv"]


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
        assert _flag(argv, "--permission-mode") == "dontAsk"
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
