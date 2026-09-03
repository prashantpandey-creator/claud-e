"""Tests for heartbeat.sh — the periodic pass that IS the clockwork.

WHY (measured on the real machine 2026-08-29):

The step list lived in three copies and every copy was different:

    install.sh, launchd branch    7 steps
    install.sh, cron branch       5 steps — no repair --apply, no go --auto
    the plist actually on disk    6 steps — no census ping

The cron branch runs on any machine without launchd. It was missing the
self-healing repair pass and `go --auto`, which is the ONLY stage in the
chain that acts — so there, the heartbeat read the world every six hours and
moved nothing, silently, for as long as it ran.

And the chain could not report on itself. `{ a; b; c; } >> log 2>&1` has no
`set -e` and captures no exit code, so a step that started failing left no
trace. Measured: 115 runs and ONE timestamp in 219 KB of log. A record nobody
can read is not a record.

heartbeat.sh owns the list once, stamps each run, prints a loud FAILED line
per nonzero exit, and caps the log.

Run: python3 ~/.claude/skills/meditate/test_heartbeat.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
HB = os.path.join(SKILL_DIR, "heartbeat.sh")



def _steps_span(src):
    """Where the STEPS array really starts and ends.

    Both call sites used src.index(")") and that broke the day a step got a
    comment with a parenthesis in it — "(a secret gist / your own receiver)".
    The slice stopped mid-comment, so the list test read a truncated array
    and reported voice.py as dropped, and the harness spliced an unterminated
    array into a script that then wrote no log at all. Eight red tests, one
    paren. The array ends at a `)` alone on its own line.
    """
    i = src.index("STEPS=(")
    m = re.search(r"^\)\s*$", src[i:], re.M)
    assert m, "STEPS array has no closing paren on its own line"
    return i, i + m.start()


def _fake_run(steps, extra_files=None, log_lines=None):
    """Run heartbeat.sh with a substituted step list, in a scratch dir."""
    d = tempfile.mkdtemp()
    src = open(HB).read()
    i, j = _steps_span(src)
    body = "STEPS=(\n" + "".join('  "%s"\n' % s for s in steps)
    open(os.path.join(d, "heartbeat.sh"), "w").write(src[:i] + body + src[j:])
    os.chmod(os.path.join(d, "heartbeat.sh"), 0o755)
    for name, text in (extra_files or {}).items():
        open(os.path.join(d, name), "w").write(text)
    log = os.path.join(d, "hb.log")
    env = dict(os.environ, MEDITATE_HEARTBEAT_LOG=log)
    if log_lines:
        env["MEDITATE_HEARTBEAT_LOG_LINES"] = str(log_lines)
    r = subprocess.run([os.path.join(d, "heartbeat.sh")], env=env,
                       capture_output=True, text=True, timeout=120)
    return r, (open(log).read() if os.path.exists(log) else ""), log


# ---------------------------------------------------------------------------
# the thing the inline chain could not do
# ---------------------------------------------------------------------------

def test_a_failing_step_is_recorded_LOUDLY():
    """The whole reason this file exists. The old chain swallowed a nonzero
    exit completely — nothing in the log, nothing anywhere."""
    _, log, _ = _fake_run(["boom.py"],
                          {"boom.py": "import sys\nsys.exit(3)\n"})
    assert "FAILED" in log, "a step exited 3 and the log does not say so:\n" + log
    assert "exit=3" in log
    assert "boom.py" in log


def test_a_failing_step_does_NOT_skip_the_rest():
    """FALSIFIER for the fix. `set -e` here would be worse than the bug: one
    flaky step would silently cancel the repair pass, the grade and the
    dashboard for that whole cycle."""
    _, log, _ = _fake_run(["boom.py", "fine.py"],
                          {"boom.py": "import sys\nsys.exit(3)\n",
                           "fine.py": "print('the later step ran')\n"})
    assert "the later step ran" in log, "a failure cancelled the rest of the pass"
    assert "1 failed" in log


def test_every_run_is_STAMPED():
    """115 runs and one timestamp in 219 KB. You could not tell when a pass
    ran, or that one had been skipped."""
    _, log, _ = _fake_run(["fine.py"], {"fine.py": "print('ok')\n"})
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", log), \
        "no timestamp on the run:\n" + log
    assert re.search(r"\d+ steps · \d+ failed", log), "no per-run summary"


def test_the_script_exits_0_even_when_a_step_fails():
    """launchd records the exit code of the whole job. Failing the job for one
    bad step would make `last exit code` useless as a signal AND, with
    KeepAlive semantics elsewhere, invite a restart loop. The FAILED line in
    the log is the report; the exit code is not."""
    r, _, _ = _fake_run(["boom.py"], {"boom.py": "import sys\nsys.exit(9)\n"})
    assert r.returncode == 0, "the pass failed the whole job over one step"


def test_the_log_is_capped():
    """It reached 219 KB, unbounded, because nothing ever trimmed it."""
    noisy = "for i in range(400): print('line %d' % i)\n"
    _, _, log = _fake_run(["noisy.py"], {"noisy.py": noisy}, log_lines=50)
    n = len(open(log).read().splitlines())
    assert n <= 50, "log kept %d lines against a cap of 50" % n


def test_the_newest_lines_are_the_ones_kept():
    """Trimming from the wrong end would keep the oldest run forever and drop
    the one that just failed."""
    noisy = "for i in range(300): print('L%d' % i)\n"
    _, _, log = _fake_run(["noisy.py"], {"noisy.py": noisy}, log_lines=20)
    text = open(log).read()
    assert "L299" in text or "done" in text, "trimmed the newest lines away"
    assert "L0\n" not in text


# ---------------------------------------------------------------------------
# ONE list — the drift that started this
# ---------------------------------------------------------------------------

def test_both_installer_branches_call_THIS_script():
    """launchd and cron spelled the chain out separately and drifted. Neither
    may hold a step list of its own again."""
    src = open(os.path.join(SKILL_DIR, "install.sh")).read()
    assert src.count("heartbeat.sh") >= 2, \
        "an installer branch is not using heartbeat.sh"
    # the old giveaway: a branch listing the python steps itself
    for marker in ('nidra_bridge.py\\" --sleep; ', 'python3 "%s/%s"'):
        assert marker not in src, \
            "an installer branch still spells the chain out: %r" % marker


def test_the_list_holds_the_stage_that_ACTS():
    """`go --auto` was missing from the cron branch. Every other stage is
    read-only, so without it the heartbeat can never move work forward — the
    exact failure the owner reported as the fleet not growing anything."""
    src = open(HB).read()
    i, j = _steps_span(src)
    steps = src[i:j]
    for required in ("repair.py --apply", "go.py --auto", "nidra_bridge.py --sleep",
                     "archive.py --apply", "dashboard.py", "voice.py"):
        assert required in steps, "%s dropped out of the heartbeat" % required


def test_the_installed_plist_matches_the_script():
    """The plist on THIS machine had 6 steps while install.sh generated 7 —
    a generation behind, and nothing said so. If a plist is installed, it must
    point at the script rather than carry its own copy."""
    import plistlib
    p = os.path.expanduser("~/Library/LaunchAgents/com.meditate.rounds.plist")
    if not os.path.exists(p):
        return          # nothing installed here; not a failure
    cmd = plistlib.load(open(p, "rb"))["ProgramArguments"][-1]
    assert "heartbeat.sh" in cmd, \
        "the installed plist still carries its own chain: %s" % cmd[:120]



def test_doctor_is_IN_the_chain():
    """The tool could see its own failures only when someone asked. Nothing
    scheduled the check, so a 45-times-repeated failure waited for a human."""
    src = open(os.path.join(SKILL_DIR, "heartbeat.sh")).read()
    assert "doctor.py --quick" in src, "the periodic pass never checks itself"
    # --quick matters: the full suite is ~200s and 52 processes, hourly.
    # Check the STEPS array, not the whole file — TOLERANT="doctor.py" and
    # the prose above it both mention doctor, and my first cut of this
    # assertion matched its own explanatory comment.
    i, j = _steps_span(src)
    doc = [l for l in src[i:j].splitlines()
           if "doctor" in l and not l.strip().startswith("#")]
    assert doc and all("--quick" in l for l in doc), \
        "the FULL suite is wired into an hourly timer: %r" % doc


def test_a_FINDING_is_not_counted_as_a_broken_step():
    """doctor exits 1 when the install is unhealthy — right for a person,
    wrong for a chain asking "did the step run". It logged `FAILED doctor.py
    exit=1` every hour for two open issues, and an hourly false FAILED is
    what buries a real one."""
    import subprocess, tempfile, textwrap
    d = tempfile.mkdtemp()
    fake = os.path.join(d, "doctor.py")
    open(fake, "w").write("import sys; print('two issues'); sys.exit(1)")
    log = os.path.join(d, "rounds.log")
    r = subprocess.run(["bash", os.path.join(SKILL_DIR, "heartbeat.sh")],
                       env=dict(os.environ, MEDITATE_HEARTBEAT_LOG=log,
                                MEDITATE_TESTING="1"),
                       capture_output=True, text=True, timeout=300)
    body = open(log).read() if os.path.exists(log) else ""
    assert "FAILED  doctor.py" not in body, \
        "a finding is being counted as a broken step:\n" + body[-400:]


def test_a_REAL_breakage_still_counts():
    """FALSIFIER for the tolerance. If nonzero never counted, the chain would
    have no way to say a step actually broke."""
    src = open(os.path.join(SKILL_DIR, "heartbeat.sh")).read()
    assert "TOLERANT=" in src
    tol = src.split("TOLERANT=", 1)[1].splitlines()[0]
    assert "nidra_bridge" not in tol and "go.py" not in tol, \
        "the acting steps were made tolerant too — nothing could report a break"
    assert "failed=$((failed + 1))" in src



def test_a_HUNG_step_cannot_stall_the_whole_pass():
    """Nothing in the chain was bounded. One hung step stalls the pass
    forever — and launchd will not start the next while this one runs, so the
    tool silently stops grading, repairing, dispatching and self-checking with
    no failure anywhere to read. That is the dead-lane shape doctor exists to
    catch, in the thing that RUNS doctor.

    Proven by running the real script with a step that sleeps far past the
    cap: it is killed, named, and the pass completes.
    """
    import subprocess, tempfile, os as _os
    d = tempfile.mkdtemp()
    with open(_os.path.join(d, "sleeper.py"), "w") as f:
        f.write("import time; time.sleep(600)\n")
    log = _os.path.join(d, "rounds.log")
    src = open(_os.path.join(SKILL_DIR, "heartbeat.sh")).read()
    # same script, one step, pointed at the sleeper
    src = src.replace(src[src.index("STEPS=("):src.index(")\n", src.index("STEPS=("))+1],
                      'STEPS=(\n  "sleeper.py"\n)')
    hb = _os.path.join(d, "hb.sh")
    open(hb, "w").write(src)
    r = subprocess.run(["bash", hb],
                       env=dict(_os.environ, MEDITATE_HEARTBEAT_LOG=log,
                                MEDITATE_STEP_TIMEOUT="3", MEDITATE_TESTING="1",
                                MEDITATE_SKILL_DIR=d),
                       capture_output=True, text=True, timeout=90)
    body = open(log).read() if _os.path.exists(log) else ""
    assert "TIMEOUT" in body, "a hung step was not killed:\n" + body[-400:]
    assert "done" in body, "the pass never finished after the kill"


def test_the_verdict_ledger_is_BOUNDED():
    """Appended every rounds pass, forever — 39 KB and climbing when first
    measured, with nothing to trim it. Every other ledger in the tool has a
    bound; this one was born without."""
    src = open(_os_path_join_doctor()).read()
    assert "getsize(VERDICT_LEDGER)" in src, "doctor.jsonl grows without limit again"


def _os_path_join_doctor():
    import os as _os
    return _os.path.join(SKILL_DIR, "doctor.py")


def test_the_rules_survive_a_BROKEN_python3():
    """The hook parsed its own event name with python3, so a broken or missing
    interpreter left it blind: it fell to the catch-all branch and emitted
    `{}` — the session started (fail-open, right) with ZERO rules and no
    complaint. Measured 2026-08-30 with python3 stubbed to exit 127: 2 bytes
    out, 'OPERATING RULES' absent. The rules come from a plain file read and
    must not need an interpreter to be DELIVERED.
    """
    import subprocess, tempfile, json as _json, os as _os
    d = tempfile.mkdtemp()
    stub = _os.path.join(d, "python3")
    open(stub, "w").write("#!/bin/sh\nexit 127\n")
    _os.chmod(stub, 0o755)
    r = subprocess.run(["bash", _os.path.join(SKILL_DIR, "hooks", "meditate-hook.sh")],
                       input=_json.dumps({"session_id": "s", "cwd": "/tmp",
                                          "hook_event_name": "SessionStart"}),
                       env=dict(_os.environ, PATH=d + ":/usr/bin:/bin"),
                       capture_output=True, text=True, timeout=60)
    out = _json.loads(r.stdout)          # must still be valid JSON
    ctx = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
    assert "OPERATING RULES" in ctx, \
        "no rules reach a session when python3 is broken: %r" % ctx[:120]


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
