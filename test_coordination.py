"""Tests for coordination.py — the sangama (confluence) layer.

Contract under test:
  - presence: every edit records (session, cwd, file, ts); stale sessions ignored
  - collision: session B editing a file session A touched recently gets ONE calm
    warning naming A and the age; same-session re-edits warn nothing
  - facts: editing a path with machine_checked claims serves them ONCE per
    session (capped), never unverified claims
  - session-start: census + drift (journal downgrades) + live-session summary
  - hook-edit must ALWAYS print valid JSON and exit 0, even on garbage stdin

Run: python3 ~/.claude/skills/meditate/test_coordination.py
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
import coordination as co


def _env(tmp):
    """Isolated dirs for one test."""
    coord = os.path.join(tmp, "coord")
    store = os.path.join(tmp, "store")
    os.makedirs(coord, exist_ok=True)
    os.makedirs(store, exist_ok=True)
    return coord, store


def _payload(sid, path, cwd="/repo", event="PreToolUse", tool="Edit"):
    return {"session_id": sid, "cwd": cwd, "hook_event_name": event,
            "tool_name": tool, "tool_input": {"file_path": path}}


# ---- presence ---------------------------------------------------------------

def test_edit_records_presence():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-a", "/repo/x.py"), coord_dir=coord, store_dir=store)
        p = co.load_presence("sid-a", coord_dir=coord)
        assert p["cwd"] == "/repo"
        assert "/repo/x.py" in p["files"]


def test_stale_session_ignored():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-old", "/repo/x.py"), coord_dir=coord, store_dir=store)
        # age the file beyond the live window
        f = os.path.join(coord, "sid-old.json")
        old = time.time() - co.LIVE_WINDOW - 60
        os.utime(f, (old, old))
        msg = co.hook_edit(_payload("sid-b", "/repo/x.py"), coord_dir=coord, store_dir=store)
        assert "sid-old" not in msg, "stale session must not trigger a collision"


# ---- collision --------------------------------------------------------------

def test_collision_warns_second_session():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-a", "/repo/shared.py"), coord_dir=coord, store_dir=store)
        msg = co.hook_edit(_payload("sid-b", "/repo/shared.py"), coord_dir=coord, store_dir=store)
        assert "sid-a"[:8] in msg and "shared.py" in msg, f"no collision warning: {msg!r}"


def test_same_session_no_collision():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-a", "/repo/x.py"), coord_dir=coord, store_dir=store)
        msg = co.hook_edit(_payload("sid-a", "/repo/x.py"), coord_dir=coord, store_dir=store)
        assert "touched" not in msg, "a session must not collide with itself"


def test_collision_warns_only_once():
    """Repeat edits of a contested file must not re-warn — that is pressure."""
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-a", "/repo/shared.py"), coord_dir=coord, store_dir=store)
        first = co.hook_edit(_payload("sid-b", "/repo/shared.py"), coord_dir=coord, store_dir=store)
        second = co.hook_edit(_payload("sid-b", "/repo/shared.py"), coord_dir=coord, store_dir=store)
        assert "SANGAMA" in first
        assert "SANGAMA" not in second, f"collision re-warned: {second!r}"


def test_different_files_no_collision():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-a", "/repo/a.py"), coord_dir=coord, store_dir=store)
        msg = co.hook_edit(_payload("sid-b", "/repo/b.py"), coord_dir=coord, store_dir=store)
        assert "touched" not in msg


# ---- fact serving -----------------------------------------------------------

def _write_index(store, path, entries):
    with open(os.path.join(store, "path_index.json"), "w") as f:
        json.dump({path: entries}, f)


def test_facts_served_for_known_path():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        _write_index(store, "/repo/main.py",
                     [{"statement": "PROMPTS registry lives in main.py", "status": "machine_checked"}])
        msg = co.hook_edit(_payload("sid-a", "/repo/main.py"), coord_dir=coord, store_dir=store)
        assert "PROMPTS registry" in msg, f"fact not served: {msg!r}"


def test_facts_served_once_per_session():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        _write_index(store, "/repo/main.py",
                     [{"statement": "PROMPTS registry lives in main.py", "status": "machine_checked"}])
        co.hook_edit(_payload("sid-a", "/repo/main.py"), coord_dir=coord, store_dir=store)
        msg2 = co.hook_edit(_payload("sid-a", "/repo/main.py"), coord_dir=coord, store_dir=store)
        assert "PROMPTS registry" not in msg2, "fact must serve once per session per file"


def test_unverified_facts_never_served():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        _write_index(store, "/repo/main.py",
                     [{"statement": "stale claim", "status": "unverified"}])
        msg = co.hook_edit(_payload("sid-a", "/repo/main.py"), coord_dir=coord, store_dir=store)
        assert "stale claim" not in msg


def test_facts_capped():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        _write_index(store, "/repo/main.py",
                     [{"statement": f"fact {i}", "status": "machine_checked"} for i in range(9)])
        msg = co.hook_edit(_payload("sid-a", "/repo/main.py"), coord_dir=coord, store_dir=store)
        assert sum(1 for i in range(9) if f"fact {i}" in msg) <= co.FACT_CAP


def test_events_logged_durably():
    """Serves and warns must land in events.jsonl — the report reads it."""
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        _write_index(store, "/repo/main.py",
                     [{"statement": "a graded fact", "status": "machine_checked"}])
        co.hook_edit(_payload("sid-a", "/repo/main.py"), coord_dir=coord, store_dir=store)
        co.hook_edit(_payload("sid-b", "/repo/main.py"), coord_dir=coord, store_dir=store)
        ev_path = os.path.join(os.path.dirname(coord.rstrip("/")), "events.jsonl")
        assert os.path.exists(ev_path), "events.jsonl not written"
        types = [json.loads(l)["type"] for l in open(ev_path)]
        assert "fact_served" in types and "collision_warned" in types, types


# ---- guard rules (moved from bash) ------------------------------------------

def test_pipeline_rule_fires():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        msg = co.hook_edit(_payload("s", "/r/backend/main.py"), coord_dir=coord, store_dir=store)
        assert "chat pipeline" in msg


def test_native_rule_fires_case_insensitive():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        for p in ("/x/App.swift", "/x/Foo.SWIFT", "/x/ios/Thing.m"):
            msg = co.hook_edit(_payload("s", p), coord_dir=coord, store_dir=store)
            assert "web app" in msg, p


# ---- session-start ----------------------------------------------------------

def test_session_start_reports_drift():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        now = co._iso(time.time())
        with open(os.path.join(store, "journal.jsonl"), "w") as f:
            f.write(json.dumps({"event": "sleep.regraded", "id": "mem_x",
                                "detail": "machine_checked -> unverified (path missing: /gone.py)",
                                "ts": now}) + "\n")
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            f.write(json.dumps({"id": "mem_x", "active": True, "statement": "s",
                                "epistemic": {"evidence_status": "unverified"}}) + "\n")
        out = co.session_start({"session_id": "s", "cwd": "/repo"},
                               coord_dir=coord, store_dir=store)
        assert "drift" in out.lower() and "mem_x" in out, f"drift not reported: {out!r}"


def test_session_start_reports_live_sessions():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        co.hook_edit(_payload("sid-other", "/repo/x.py", cwd="/repo"), coord_dir=coord, store_dir=store)
        out = co.session_start({"session_id": "sid-me", "cwd": "/repo"},
                               coord_dir=coord, store_dir=store)
        assert "1 other live session" in out, f"presence not reported: {out!r}"


def test_session_start_nudges_repair_queue():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        # queue lives beside the store dir (parent = meditation dir)
        with open(os.path.join(os.path.dirname(store), "repair-queue.md"), "w") as f:
            f.write("# Repair queue\n")
        out = co.session_start({"session_id": "s", "cwd": "/repo"},
                               coord_dir=coord, store_dir=store)
        assert "Repair queue" in out, f"queue nudge missing: {out!r}"


def test_done_digest_reports_silent_work():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        now = co._iso(time.time())
        with open(os.path.join(store, "journal.jsonl"), "w") as f:
            f.write(json.dumps({"event": "sleep.completed", "actions": 3,
                                "contested": 0, "ts": now}) + "\n")
            f.write(json.dumps({"event": "formation.commit_facts", "formed": 5,
                                "ts": now}) + "\n")
        out = co.session_start({"session_id": "s", "cwd": "/repo"},
                               coord_dir=coord, store_dir=store)
        assert "Done silently" in out and "formed 5" in out and "graded 1x" in out, out


def test_done_digest_empty_day_is_silent():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        assert co.done_digest(store_dir=store, coord_dir=coord) == ""


def test_session_start_quiet_when_alone_and_clean():
    with tempfile.TemporaryDirectory() as t:
        coord, store = _env(t)
        out = co.session_start({"session_id": "s", "cwd": "/repo"},
                               coord_dir=coord, store_dir=store)
        assert out == "", f"expected silence, got {out!r}"


# ---- CLI robustness ---------------------------------------------------------

def test_cli_hook_edit_survives_garbage():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "coordination.py"), "hook-edit"],
                       input="not json", capture_output=True, text=True, timeout=15)
    assert r.returncode == 0
    json.loads(r.stdout.strip())  # must be valid JSON


def test_cli_drift_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "coordination.py"), "drift", "--json"],
                       capture_output=True, text=True, timeout=15)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env


def test_cli_who_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "coordination.py"), "who", "--json"],
                       capture_output=True, text=True, timeout=15)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    assert env["success"] is True


def test_session_start_registers_its_own_presence():
    """A session that only runs shell commands must still exist. Presence was
    created on the first Write/Edit only, so such a session was invisible."""
    import tempfile
    d = tempfile.mkdtemp(prefix="coord-ss-") + "/sessions"
    os.makedirs(d, exist_ok=True)
    sid = "brand-new-session-1234"
    assert not os.path.exists(os.path.join(d, sid + ".json"))
    co.session_start({"session_id": sid, "cwd": "/tmp"}, coord_dir=d,
                     store_dir=co.STORE_DIR)
    p = os.path.join(d, sid + ".json")
    assert os.path.exists(p), "session_start did not register presence"
    assert json.load(open(p))["cwd"] == "/tmp"


def test_never_checked_is_not_drift():
    """"I have not checked this yet" is not "this is broken".

    Every newly written memory is `unverified` until its first review comes
    due, so reporting unverified-as-drift put every new memory a user writes
    into their repair queue for a day — and the repair queue dispatches
    agents. Same two-valued mistake as the extractor, one layer up.
    """
    import tempfile, json as _j
    with tempfile.TemporaryDirectory() as d:
        real = os.path.join(d, "real.txt")
        open(real, "w").write("x")
        rows = [
          # never checked, but every claim resolves -> NOT drift
          {"id": "m_new", "active": True, "statement": "fresh memory",
           "epistemic": {"evidence_status": "unverified"},
           "evidence": [{"locator": "path:" + real, "source": real, "excerpt": "e"}]},
          # never checked AND a claim is genuinely gone -> IS drift
          {"id": "m_bad", "active": True, "statement": "stale memory",
           "epistemic": {"evidence_status": "unverified"},
           "evidence": [{"locator": "path:" + d + "/gone", "source": real, "excerpt": "e"}]},
        ]
        with open(os.path.join(d, "memories.jsonl"), "w") as f:
            for r in rows: f.write(_j.dumps(r) + "\n")
        rep = co.drift_report(d)
        ids = [m["id"] for m in rep["memories"]]
        assert "m_bad" in ids, "real drift must still be reported: %s" % ids
        assert "m_new" not in ids, \
            "unchecked-but-intact memory reported as drift: %s" % ids



def _usage_row(ctx):
    import json as _j
    return _j.dumps({"type": "assistant", "message": {"usage": {
        "input_tokens": 2, "cache_read_input_tokens": ctx - 1002,
        "cache_creation_input_tokens": 1000, "output_tokens": 50}}})


def test_ceiling_silent_under_threshold_and_when_unmeasurable():
    """Not-checkable is not over-ceiling — the three-valued rule again."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tp = os.path.join(d, "s.jsonl")
        open(tp, "w").write(_usage_row(120_000) + "\n")
        me = {}
        assert co.ceiling_check({"transcript_path": tp, "session_id": "s1"}, me) == ""
        assert co.ceiling_check({"session_id": "s1"}, me) == ""            # no path
        assert co.ceiling_check({"transcript_path": d + "/nope", "session_id": "s1"}, me) == ""


def test_ceiling_warns_in_the_200k_band():
    """THE case the first version missed. Measured: 40 of 56 real compactions
    (71%) happened at ~160k — standard-window models — far below a fixed 700k
    line. The warn must fire in the 145k-200k band too."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tp = os.path.join(d, "s.jsonl")
        open(tp, "w").write(_usage_row(155_000) + "\n")
        me = {}
        w = co.ceiling_check({"transcript_path": tp, "session_id": "abcd1234x"}, me)
        assert "155k" in w and "200k" in w and "meditate" in w, w
        # nag control inside the band: +10k is silent, +25k fires again
        open(tp, "a").write(_usage_row(165_000) + "\n")
        assert co.ceiling_check({"transcript_path": tp, "session_id": "abcd1234x"}, me) == ""
        open(tp, "a").write(_usage_row(181_000) + "\n")
        assert co.ceiling_check({"transcript_path": tp, "session_id": "abcd1234x"}, me) != ""


def test_ceiling_silent_between_bands():
    """ctx=300k PROVES the window is bigger than 200k (it did not compact),
    and 1M pressure has not started — silence, even with a prior band-A warn
    on the record (the generic restep must not leak across bands)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tp = os.path.join(d, "s.jsonl")
        me = {"ceiling_warned": 155_000}
        open(tp, "w").write(_usage_row(300_000) + "\n")
        assert co.ceiling_check({"transcript_path": tp, "session_id": "s1"}, me) == ""


def test_ceiling_warns_once_then_again_each_step():
    """1M band: fire at the 600k floor, not on every edit after, +100k steps.

    Floor is 600k not 700k because the deduped data shows a real wall at
    645k (48/50 events covered at 600k, 42/50 at 700k)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d2:
        tp2 = os.path.join(d2, "s.jsonl")
        open(tp2, "w").write(_usage_row(645_000) + "\n")
        assert "645k" in co.ceiling_check({"transcript_path": tp2, "session_id": "s1"}, {})
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tp = os.path.join(d, "s.jsonl")
        me = {}
        open(tp, "w").write(_usage_row(710_000) + "\n")
        w1 = co.ceiling_check({"transcript_path": tp, "session_id": "abcd1234x"}, me)
        assert "710k" in w1 and "meditate" in w1 and "abcd1234" in w1, w1
        assert co.ceiling_check({"transcript_path": tp, "session_id": "abcd1234x"}, me) == ""
        open(tp, "a").write(_usage_row(790_000) + "\n")
        assert co.ceiling_check({"transcript_path": tp, "session_id": "abcd1234x"}, me) == ""
        open(tp, "a").write(_usage_row(815_000) + "\n")
        w2 = co.ceiling_check({"transcript_path": tp, "session_id": "abcd1234x"}, me)
        assert "815k" in w2, w2


def test_ceiling_resets_after_a_compaction():
    """After a compaction the context collapses. The old high-water mark must
    not gag the NEXT climb — the second cycle deserves its warning too."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tp = os.path.join(d, "s.jsonl")
        me = {"ceiling_warned": 815_000}
        open(tp, "w").write(_usage_row(160_000) + "\n")       # collapsed + climbing
        w = co.ceiling_check({"transcript_path": tp, "session_id": "s1"}, me)
        assert w != "", "post-compaction climb was gagged by the stale mark"


def test_ceiling_reads_the_tail_of_a_fat_transcript():
    """Transcripts reach 25MB; the check must stay O(tail), not O(file),
    and the LAST usage row is the live context — not the first."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tp = os.path.join(d, "s.jsonl")
        with open(tp, "w") as f:
            f.write(_usage_row(900_000) + "\n")          # stale old row
            for _ in range(3000):
                f.write('{"type": "user", "message": {"content": "' + "x" * 100 + '"}}\n')
            f.write(_usage_row(720_000) + "\n")          # the live one
        me = {}
        w = co.ceiling_check({"transcript_path": tp, "session_id": "s1"}, me)
        assert "720k" in w, w


def test_squiggle_catches_a_name_that_was_never_imported():
    """The red squiggly. This is the exact bug shipped today.

    `brain.py` gained `paths.goals_dir()` without `import paths`. Running
    ast.parse on it printed "parses OK" -- syntax was fine -- and the failure
    surfaced much later as a NameError at import time, in a different turn,
    after the file had already been committed. A checker that runs at the
    moment of the edit turns a later archaeology problem into a one-line
    correction."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "brain.py")
        with open(f, "w") as fh:
            fh.write("import os\nLIVE = [os.path.expanduser('~/x'), paths.goals_dir()]\n")
        msg = co.check_edit(f)
        assert "paths" in msg, msg
        assert "2" in msg, "should name the line: %s" % msg


def test_squiggle_sees_INSIDE_functions_when_ruff_is_present():
    """The reason a dependency is worth taking here.

    The stdlib fallback can only judge module scope: inside a function a name
    may be defined later or come from a scope a cheap pass cannot see, so it
    stays silent — and that is where most undefined-name bugs actually live.
    ruff's F821 sees them. Zero dependencies is the floor for someone who has
    not installed anything, not a reason to ship the weaker check to everyone.
    """
    import shutil, tempfile
    if not shutil.which("ruff"):
        return                                    # fallback path is tested below
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "deep.py")
        with open(f, "w") as fh:
            fh.write("import os\ndef f():\n    return never_defined(os.sep)\n")
        msg = co.check_edit(f)
        assert "never_defined" in msg, msg
        assert "deep.py" in msg, "must name the file: %s" % msg


def test_squiggle_stdlib_fallback_works_without_ruff(monkeypatch=None):
    """It must still catch the real bug on a machine with nothing installed."""
    import tempfile
    real = co.shutil.which
    co.shutil.which = lambda n: None              # pretend ruff is absent
    try:
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "brain.py")
            with open(f, "w") as fh:
                fh.write("import os\nLIVE = [os.sep, paths.goals_dir()]\n")
            msg = co.check_edit(f)
            assert "paths" in msg, msg
    finally:
        co.shutil.which = real


def test_squiggle_catches_a_syntax_error():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "x.py")
        with open(f, "w") as fh:
            fh.write("def broken(:\n    pass\n")
        msg = co.check_edit(f)
        assert msg and ("syntax" in msg.lower() or "invalid" in msg.lower()), msg


def test_squiggle_is_SILENT_on_correct_code():
    """A checker that cries wolf gets ignored, and then it is worse than
    nothing. Precision before recall -- the whole session's lesson."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "ok.py")
        with open(f, "w") as fh:
            fh.write(
                "import os\n"
                "from collections import Counter\n"
                "TOP = os.sep\n"
                "def f(a, b=1):\n"
                "    inner = a + b\n"
                "    return inner, Counter(), TOP\n"
                "class K:\n"
                "    attr = 1\n"
                "    def m(self):\n"
                "        return self.attr, K\n"
                "for i in range(3):\n"
                "    print(i, len('x'), __file__)\n"
                "try:\n"
                "    import json as _j\n"
                "except ImportError:\n"
                "    _j = None\n"
                "with open(os.devnull) as fh:\n"
                "    data = fh.read()\n"
                "[x for x in range(2)]\n"
                "lam = lambda q: q + 1\n")
            fh.flush()
        assert co.check_edit(f) == "", co.check_edit(f)


def test_squiggle_ignores_non_python_and_missing_files():
    """Claim scope = check scope. It only knows Python; it must say nothing
    about anything else rather than guess."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "notes.md")
        with open(f, "w") as fh:
            fh.write("paths.goals_dir() in prose is not a bug\n")
        assert co.check_edit(f) == ""
        assert co.check_edit(os.path.join(d, "gone.py")) == ""


def test_squiggle_never_raises_on_a_pathological_file():
    """It runs on EVERY edit. It must fail silent, never break the loop."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "weird.py")
        with open(f, "wb") as fh:
            fh.write(b"\xff\xfe\x00 not utf-8 at all \x00")
        assert isinstance(co.check_edit(f), str)


def test_command_squiggle_catches_a_suite_that_ran_NOTHING():
    """The invisible failure: exit 0, output present, nothing proved.

    A failing test is already visible to the model -- it reads the traceback.
    A suite that ran ZERO tests is not: it exits 0, prints something
    reassuring, and the run gets counted as green. Both happened today. Six
    of seven nidra test files reported "NO TESTS RAN" under unittest and were
    read as fine, and a mutation check reported OK because the replacement
    string never matched, so the mutation was never applied. Green that
    proves nothing is worse than red."""
    for out in ("Ran 0 tests in 0.000s\n\nNO TESTS RAN",
                "collected 0 items\n\nno tests ran in 0.01s",
                "Ran 0 tests in 0.000s\n\nOK"):
        msg = co.check_command("python3 -m pytest tests/", out)
        assert msg, "silent no-op not caught: %r" % out
        assert "0" in msg or "no test" in msg.lower(), msg


def test_command_squiggle_is_silent_when_tests_actually_RAN():
    for out in ("Ran 37 tests in 1.2s\n\nOK", "37 passed in 0.55s",
                "35/35 passed", "collected 12 items\n12 passed"):
        assert co.check_command("pytest -q", out) == "", out


def test_command_squiggle_is_silent_on_honest_failures():
    """A real failure is already legible. Do not double-report it."""
    out = "Ran 12 tests in 0.3s\n\nFAILED (failures=2)"
    assert co.check_command("python3 -m unittest x", out) == "", out


def test_command_squiggle_only_judges_test_commands():
    """Claim scope = check scope. 'Ran 0 tests' inside unrelated output of a
    non-test command is not this tool's business."""
    assert co.check_command("cat notes.txt", "Ran 0 tests in 0.000s") == ""
    assert co.check_command("echo hi", "") == ""


def test_command_squiggle_never_raises():
    for cmd, out in (("", ""), (None, None), ("pytest", None), ("pytest", 12345)):
        assert isinstance(co.check_command(cmd, out), str)


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
