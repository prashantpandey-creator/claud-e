"""Tests for doctor.py (Rule 0, precondition A).

Run: python3 ~/.claude/skills/meditate/test_doctor.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doctor


def test_envelope_shape():
    env = doctor.run(run_tests=False)
    assert isinstance(env, dict), "envelope must be dict"
    assert "success" in env, "missing success"
    assert "data" in env, "missing data"
    assert "metadata" in env, "missing metadata"
    assert "errors" in env, "missing errors"
    assert env["success"] is True, "doctor.run should always succeed (it reports issues, not fails)"


def test_data_fields():
    d = doctor.run(run_tests=False)["data"]
    for key in ("version", "healthy", "issues", "prereqs", "tests", "hook", "stillness", "output"):
        assert key in d, f"missing data.{key}"
    assert isinstance(d["version"], str), "version must be string"
    assert isinstance(d["healthy"], bool), "healthy must be bool"
    assert isinstance(d["issues"], list), "issues must be list"


def test_prereqs_include_python_and_claude():
    d = doctor.run(run_tests=False)["data"]
    names = [p["name"] for p in d["prereqs"]]
    assert "python3" in names, "must check python3"
    assert "claude_code" in names, "must check claude_code"
    python_check = [p for p in d["prereqs"] if p["name"] == "python3"][0]
    assert python_check["ok"] is True, "we are running on python3"


def test_stillness_fields():
    d = doctor.run(run_tests=False)["data"]
    s = d["stillness"]
    assert "exists" in s, "missing exists"
    assert "overdue" in s, "missing overdue"
    if s["exists"]:
        assert "age_days" in s, "if exists, age_days must be present"
        assert isinstance(s["age_days"], (int, float)), "age_days must be numeric"


def test_json_serializable():
    env = doctor.run(run_tests=False)
    serialized = json.dumps(env)
    assert len(serialized) > 100, "envelope too small"


def test_discovers_every_suite_without_running_them():
    """Cheap proof that discovery is real: every test_*.py on disk is in the
    list (minus self-referential ones). Actually EXECUTING them is what
    `meditate doctor` does — duplicating that here just doubled the cost."""
    import os as _os
    on_disk = {f for f in _os.listdir(doctor.SKILL_DIR)
               if f.startswith("test_") and f.endswith(".py")}
    listed = set(doctor.TEST_FILES)
    missing = on_disk - listed - doctor.SELF_REFERENTIAL
    assert not missing, "suites invisible to doctor: %s" % sorted(missing)
    assert len(listed) >= 20, listed


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


# ---------------------------------------------------------------------------
# who fixes what — added 2026-08-29
#
# The tool could see its own failures and did nothing: two issues sat open for
# four days while every hourly pass printed them again. But "fix everything
# automatically" is the wrong correction — some findings need a judgment only
# the owner has, and a tool that guesses at those is worse than one that nags.
# ---------------------------------------------------------------------------

def test_an_UNCLASSIFIED_finding_falls_to_you_not_to_auto():
    """The rule that keeps the split honest. A problem nothing knows how to
    fix is not an auto-fixable one."""
    c = doctor.classify(["something_nobody_has_seen_before"])
    assert [i["issue"] for i in c["you"]] == ["something_nobody_has_seen_before"]
    assert c["auto"] == [] and c["agent"] == []


def test_mend_NEVER_touches_what_is_yours():
    """FALSIFIER, and the whole value of the split — it has to hold under the
    pressure to be helpful. memory_index_stale is deliberately never
    auto-fixed: this tool does not write your memory unasked."""
    calls = []
    def spy(cmd, **kw):
        calls.append(cmd)
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    doctor.mend(["memory_index_stale", "project_names_unaliased",
                 "prerequisites", "stillness_overdue", "tests"], run=spy)
    assert calls == [], "it ran something for an issue that is not its to fix: %r" % calls


def test_mend_DOES_run_the_mechanical_one():
    """And the other half: if it never fixed anything the split would be
    decoration."""
    calls = []
    def spy(cmd, **kw):
        calls.append(cmd)
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    did = doctor.mend(["memory_dirs_unlinked"], run=spy)
    assert len(calls) == 1, calls
    assert "--link-memory" in " ".join(calls[0])
    assert did[0]["result"] == "fixed"


def test_a_failed_mend_says_so_rather_than_claiming_fixed():
    """`started: true` over nothing, again. A fix is a claim and gets the same
    treatment as any other."""
    def fails(cmd, **kw):
        class R: returncode = 1; stderr = "no such thing"; stdout = ""
        return R()
    did = doctor.mend(["memory_dirs_unlinked"], run=fails)
    assert did[0]["result"].startswith("tried, failed"), did


def test_every_issue_doctor_can_raise_is_CLASSIFIED():
    """An issue added later with no entry silently becomes 'yours' forever and
    nobody notices. Reading the source is the only way to catch that."""
    import re
    src = open(os.path.join(doctor.SKILL_DIR, "doctor.py")).read()
    raised = set(re.findall(r'issues\.append\("([a-z_]+)"\)', src))
    missing = raised - set(doctor.FIXERS)
    assert not missing, "unclassified: %s" % sorted(missing)


def test_the_pass_mends_and_looks_again():
    src = open(os.path.join(doctor.SKILL_DIR, "heartbeat.sh")).read()
    assert "--mend" in src, "the pass reports its findings and fixes none of them"


if __name__ == "__main__":
    sys.exit(_main())
