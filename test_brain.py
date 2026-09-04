"""Tests for brain.py — the live brain server (Rule 0, precondition A).

Contract:
  - state() assembles every organ into one JSON dict: store, goals, fleet,
    live sessions, repair items, queues, wins, digest — no invented numbers
  - the server binds 127.0.0.1 ONLY (the brain never faces a network)
  - GET /api/state -> 200 JSON with all sections; GET / -> 200 self-contained
    HTML (no external http assets) that fetches /api/state
  - a request never crashes the server (bad paths -> 404, state errors -> 500
    JSON, process survives)

Run: python3 ~/.claude/skills/meditate/test_brain.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.request

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import tempfile
# isolate BEFORE importing brain: this suite POSTs real actions, and their
# records must never land in the owner's live activity trail.
os.environ["MEDITATE_COORD_DIR"] = tempfile.mkdtemp(prefix="brain-test-") + "/sessions"
os.makedirs(os.environ["MEDITATE_COORD_DIR"], exist_ok=True)

import brain as br


def test_state_has_all_sections():
    s = br.state()
    for k in ("store", "goals", "live_sessions", "fleet", "repair",
              "queues", "wins", "digest", "generated"):
        assert k in s, f"state missing {k}"
    assert isinstance(s["goals"], list)
    assert isinstance(s["live_sessions"], list)


def test_server_binds_loopback_and_serves():
    srv = br.make_server(port=0)                      # ephemeral port
    assert srv.server_address[0] == "127.0.0.1", "brain must bind loopback ONLY"
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=60) as r:
            assert r.status == 200
            data = json.loads(r.read())
            assert "store" in data and "goals" in data
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=60) as r:
            assert r.status == 200
            page = r.read().decode()
            assert "/api/state" in page, "page must fetch live state"
            assert "http://" not in page.replace("http://127.0.0.1", "") \
                   and "https://" not in page, "page must be self-contained"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=60) as r:
            raise AssertionError("404 expected")
    except urllib.error.HTTPError as e:
        assert e.code == 404
    finally:
        srv.shutdown()


def test_act_requires_header_and_runs_known_actions():
    calls = []
    old = br.ACT_RUNNER
    br.ACT_RUNNER = lambda a, g: calls.append((a, g)) or {"started": True, "output": "Launched 2 agent(s)"}
    try:
        srv = br.make_server(port=0)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        # no header -> 403 (CSRF guard)
        req = urllib.request.Request(base + "/api/act", data=b'{"action":"go"}',
                                     method="POST")
        try:
            urllib.request.urlopen(req, timeout=60)
            raise AssertionError("403 expected without X-Meditate header")
        except urllib.error.HTTPError as e:
            assert e.code == 403
        # with header -> runs
        req = urllib.request.Request(base + "/api/act",
                                     data=json.dumps({"action": "fix", "arg": "2"}).encode(),
                                     headers={"X-Meditate": "1"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read())
            assert j["started"] is True
            assert "Launched" in j["output"], "click must return the REAL output"
        assert calls == [("fix", "2")], calls
        # unknown action -> 400
        req = urllib.request.Request(base + "/api/act",
                                     data=b'{"action":"rm-rf"}',
                                     headers={"X-Meditate": "1"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=60)
            raise AssertionError("400 expected for unknown action")
        except urllib.error.HTTPError as e:
            assert e.code == 400
        srv.shutdown()
    finally:
        br.ACT_RUNNER = old


def test_names_and_guarded_stop():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        old = br.NAMES_PATH
        br.NAMES_PATH = os.path.join(td, "names.json")
        try:
            br.set_name("abcd1234-ffff", "marketplace payments fix")
            assert br._names()["abcd1234-fff"] == "marketplace payments fix"
        finally:
            br.NAMES_PATH = old
    # stop refuses a pid that is not a claude process
    old_check = br._pid_is_claude
    br._pid_is_claude = lambda pid: False
    try:
        import coordination as co
        with tempfile.TemporaryDirectory() as td2:
            cd = os.path.join(td2, "sessions"); os.makedirs(cd)
            with open(os.path.join(cd, "sess-x.json"), "w") as f:
                json.dump({"sid": "sess-x", "cwd": "/r", "files": {}, "pid": 99999}, f)
            oc = co.COORD_DIR
            co.COORD_DIR = cd
            try:
                r = br.stop_session("sess-x", kill=lambda *a: (_ for _ in ()).throw(AssertionError("must not kill")))
                assert r["started"] is False and "refused" in r["output"], r
            finally:
                co.COORD_DIR = oc
    finally:
        br._pid_is_claude = old_check


def test_derive_label_precision():
    """Precision ladder: chapter mark wins, else last user ask, cached 60s."""
    import tempfile, glob
    with tempfile.TemporaryDirectory() as td:
        proj = os.path.join(td, "projects", "-x"); os.makedirs(proj)
        sid = "aaaa1111-bbbb-2222"
        tp = os.path.join(proj, sid + ".jsonl")
        with open(tp, "w") as f:
            f.write('{"type":"user","message":{"content":"fix the payment flow on marketplace"}}\n')
            f.write('{"tool":"mark_chapter","input":"{\\"title\\": \\"Razorpay key restoration\\"}"}\n')
        oldp, oldc = br.PROJECTS_DIR, br.LABELS_CACHE
        br.PROJECTS_DIR = os.path.join(td, "projects")
        br.LABELS_CACHE = os.path.join(td, "labels.json")
        try:
            assert br._derive_label(sid, "/x") == "Razorpay key restoration"
            # no chapters -> last ask
            sid2 = "cccc3333-dddd-4444"
            with open(os.path.join(proj, sid2 + ".jsonl"), "w") as f:
                f.write('{"type":"user","text":"make the goal names precise so i know what is happening"}\n')
            lab = br._derive_label(sid2, "/x")
            assert "precise" in lab, lab
            # cache throttle: mutate file, label stays for 60s
            with open(os.path.join(proj, sid2 + ".jsonl"), "a") as f:
                f.write('{"type":"user","text":"completely different topic now"}\n')
            assert br._derive_label(sid2, "/x") == lab, "must serve cached label inside 60s"
            # tool_result wrapped in a user row must NEVER become the name
            sid3 = "eeee5555-ffff-6666"
            with open(os.path.join(proj, sid3 + ".jsonl"), "w") as f:
                f.write('{"type":"user","message":{"content":[{"type":"tool_result","text":"Exit code 143 Command timed out after 5m"}]}}\n')
            assert br._derive_label(sid3, "/x") == "", "tool-output garbage leaked into the label"
        finally:
            br.PROJECTS_DIR, br.LABELS_CACHE = oldp, oldc


def test_rejects_foreign_host():
    """DNS-rebind defense: a request with a non-loopback Host is refused."""
    import urllib.request, urllib.error, threading
    srv = br.make_server(port=0); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state",
                                     headers={"Host": "evil.example.com"})
        try:
            urllib.request.urlopen(req, timeout=60)
            raise AssertionError("403 expected for foreign Host")
        except urllib.error.HTTPError as e:
            assert e.code == 403, e.code
        # loopback Host still works
        req2 = urllib.request.Request(f"http://127.0.0.1:{port}/api/state",
                                      headers={"Host": f"127.0.0.1:{port}"})
        with urllib.request.urlopen(req2, timeout=60) as r:
            assert r.status == 200
    finally:
        srv.shutdown()


def test_esc_escapes_quotes():
    """esc() must neutralize both quote kinds — the XSS attribute-break class."""
    e = br.PAGE  # esc is JS-side; assert the page no longer builds onclick with
    # the vulnerability was INTERPOLATED data in onclick (act('...${label}...')).
    # static onclick=act('go','') is constant and safe; forbid only interpolation.
    assert not re.search(r'onclick="[^"]*\$\{', e), \
        "no template data may be interpolated into an inline handler"
    assert "j-go" in e and "j-fix" in e and "addEventListener" in e, \
        "per-row interactive elements must use delegated data-attribute handlers"


def test_state_is_json_serializable():
    json.dumps(br.state())


def test_state_survives_a_session_that_has_touched_no_files():
    """/api/state returned HTTP 500 for the whole time any registered session
    had files={} — the third hand-written copy of sorted([])[-1]."""
    d = br.state()
    assert "briefing" in d and "timing" in d
    # and the helper itself, since that is the thing three sites duplicated
    from coordination import last_file
    assert last_file({"files": {}}) is None
    assert last_file(None) is None
    assert last_file({"files": {"/a/late.py": 9, "/a/early.py": 1}}) == "late.py"


def test_console_can_reach_a_named_session():
    """A roster you cannot touch is not a console. `tell <sid> <message>` must
    build a real inbox send, and must not fall apart on a missing message."""
    cmd = br.ACTIONS["tell"]("abc123 the payment key is missing")
    assert cmd[-3:] == ["send", "abc123", "the payment key is missing"], cmd
    # sid with no message: still well-formed, never an IndexError
    bare = br.ACTIONS["tell"]("abc123")
    assert bare[-2] == "abc123" and len(bare) == len(cmd), bare


def test_live_sessions_separate_working_from_merely_recent():
    """Liveness was 'anything inside an hour', which called 74 sessions live
    while 10 claude processes existed. The working tier is the honest one."""
    from coordination import live_sessions, WORKING_S
    for s in live_sessions():
        assert s["_state"] in ("working", "idle"), s
        if s["_state"] == "working":
            assert s["_age_s"] <= WORKING_S, s


def test_projects_view_carries_what_a_TILE_needs_and_marks_dormant():
    """The visual of every project and where it stands. rollup() is 9.5 s
    over 92 projects, so the view is cached; the test injects the rollup."""
    import brain
    rows = [{"project": "shop", "sessions": 12, "last_touched_days": 0.4, "goals": 1,
             "milestones_done": 2, "milestones_total": 5, "pct": 40.0, "commits_recent": 7,
             "facts": 9, "open_tasks": [{"goal": "g", "task": "webhook verified", "pct": 40.0}]},
            {"project": "old-thing", "sessions": 1, "last_touched_days": 61.0, "goals": 0,
             "milestones_done": 0, "milestones_total": 0, "pct": None, "commits_recent": 0,
             "facts": 0, "open_tasks": []}]
    d = brain._projects_cached(rollup_fn=lambda: rows)
    assert [p["project"] for p in d["projects"]] == ["shop", "old-thing"], d
    t = d["projects"][0]
    for k in ("touched_days", "goals", "done", "total", "commits_30d", "open", "dormant", "sessions"):
        assert k in t, k
    assert t["open"] == ["webhook verified"]
    assert d["basis"].startswith("2 projects")


def test_server_REPLACES_ITSELF_when_the_code_on_disk_is_newer():
    """A launchd server keeps the code it booted with: two commits landed
    after the brain started on 2026-09-04 and the shipped sum-of-runs fix
    never ran in it. The rule: newer code on disk, quiet for a minute (not
    mid-edit), and the server execs itself. Same pid slot, same argv."""
    boot = 1000.0
    assert not br._reexec_due(boot, newest=boot, now=boot + 500)
    assert not br._reexec_due(boot, newest=boot + 10, now=boot + 30), "still being edited"
    assert br._reexec_due(boot, newest=boot + 10, now=boot + 10 + 61)
    assert not br._reexec_due(boot, newest=boot + 0.5, now=boot + 500), "same code, mtime jitter"
    # the stamp covers every module the server imports, not five of them
    import inspect
    assert "*.py" in inspect.getsource(br._code_stamp)
    # tests never arm the watcher: make_server must not start it
    assert "_watch_code" not in inspect.getsource(br.make_server)
    assert "_watch_code" in inspect.getsource(br.main)


def test_a_go_that_sent_nothing_says_NOTHING_SENT_and_why():
    """The console's go ran go.py and reported started:true with the CLI's
    'Nothing to move' line truncated to 28 characters on the button. The
    runner reads go's JSON envelope: started is whether an agent was sent,
    and the output is the launch or the named reasons."""
    env = {"data": {"sent": [], "cooling": 0,
                    "skipped": [{"goal": "g-done", "why": "all 6 of 6 milestones done"}]}}
    started, out = br._go_output(env)
    assert started is False and "g-done: all 6 of 6 milestones done" in out, out
    env = {"data": {"sent": ["goal-g-a"], "launched": [{"kind": "goal", "title": "Ship it",
                                                         "milestone": "first open thing"}]}}
    started, out = br._go_output(env)
    assert started is True and "Ship it" in out and "first open thing" in out, out
    started, out = br._go_output({"garbage": 1})
    assert started is False and "did not answer" in out, out
    # the page: a refused click reads 'nothing sent', and go's reasons open the readout
    src = open(os.path.join(SKILL, "twin_console.html")).read()
    assert '"nothing sent"' in src, "button label on refusal"
    assert 'showReadout("go"' in src or "showReadout('go'" in src, "reasons are shown whole"


def test_one_click_is_ONE_activity_row():
    """accept-goal, accept-predicted, discard-proposed and go-all were each
    logged twice per click (a handler-level call plus the generic one at the
    end of do_POST); the ACTIVITY feed showed every click doubled."""
    import inspect
    src = inspect.getsource(br)
    calls = [l for l in src.splitlines() if "_log_brain_action(" in l and "def _log_brain_action" not in l]
    # exactly two remain: the mascot's early-return path, and the generic one
    assert len(calls) == 2, calls


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
