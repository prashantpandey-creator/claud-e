"""Tests that the tool is installable by someone who is not its author.

The defect these pin: five modules hardcoded the author's own layout —
~/projects/nidra, ~/claude-sync/memory, ~/claude-sync/goals, and literally
"-Users-badenath-projects-vedic-puran" as a DEFAULT VALUE — so on any other
machine nidra failed to import, memory graded nothing, and goals came up
empty. Proved on a clean HOME before the fix:

    {"success": false, "errors": [{"code": "import",
                                   "message": "No module named 'nidra'"}]}

Contract:
  - no shipped module contains the author's username or personal directories
  - every location resolves on a machine that has none of them
  - an existing install does NOT move (conventional dirs win over defaults)
  - uninstall removes only meditate's own wiring
  - install.sh and uninstall.sh are valid shell and are a matched pair

Run: python3 ~/.claude/skills/meditate/test_packaging.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

# Things that only exist on one person's machine. Tests and docs may mention
# them; shipped code may not depend on them.
PERSONAL = [
    r"/Users/[a-z]+/",             # anybody's absolute home
    r"\bbadenath\b",
    r"projects/nidra",
    r"expanduser\([\"']~/claude-sync",   # DEPENDING on the sync folder
]
# "claude-sync" appearing in a blocklist of directory names to ignore is a
# heuristic, not a dependency — it costs nothing to a user who has no such
# folder. Only resolving PATHS from it is the packaging defect.
# paths.py is the ONE place allowed to name conventional locations, because
# naming them is its whole job. Docstrings elsewhere may quote the history.
EXEMPT_FILES = {"paths.py", "test_packaging.py"}


def _shipped_modules():
    for fn in sorted(os.listdir(SKILL)):
        if fn.endswith(".py") and not fn.startswith("test_") \
                and fn not in EXEMPT_FILES:
            yield fn


def _code_lines(path):
    """Source lines with comments and docstrings stripped, roughly — enough to
    tell 'this module DEPENDS on the path' from 'this module MENTIONS it'."""
    out = []
    in_doc = False
    delim = ""
    for line in open(os.path.join(SKILL, path), encoding="utf-8",
                     errors="replace"):
        stripped = line.strip()
        if in_doc:
            if delim in stripped:
                in_doc = False
            continue
        if stripped.startswith(('"""', "'''")):
            delim = stripped[:3]
            if not (stripped.endswith(delim) and len(stripped) > 3):
                in_doc = True
            continue
        code = line.split("#", 1)[0]
        if code.strip():
            out.append(code)
    return out


def test_no_personal_paths_in_shipped_code():
    offenders = []
    for fn in _shipped_modules():
        for i, line in enumerate(_code_lines(fn), 1):
            for pat in PERSONAL:
                if re.search(pat, line):
                    offenders.append("%s:%d %s" % (fn, i, line.strip()[:70]))
    assert not offenders, "the author's machine is baked in:\n  " + \
        "\n  ".join(offenders[:8])


def test_every_location_resolves_on_a_bare_machine():
    import paths
    for name in ("memory_root", "goals_dir", "store_dir"):
        v = getattr(paths, name)()
        assert v and os.path.isabs(v), (name, v)
    # nidra_root may legitimately be None (pip-installed, or simply absent)
    assert paths.nidra_root() is None or os.path.isdir(paths.nidra_root())


def test_a_clean_home_gets_working_defaults_not_the_authors():
    home = tempfile.mkdtemp()
    env = dict(os.environ, HOME=home)
    for k in ("MEDITATE_STORE_DIR", "MEDITATE_COORD_DIR", "MEDITATE_GOALS_DIR",
              "MEDITATE_MEMORY_ROOT", "MEDITATE_NIDRA_ROOT", "MEDITATE_HOME"):
        env.pop(k, None)
    r = subprocess.run([sys.executable, os.path.join(SKILL, "paths.py"), "--json"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)["data"]
    # describe() grew a nested `coverage` dict from another lane; check every
    # string ANYWHERE in the structure rather than assuming a flat shape, or
    # this test breaks on somebody's unrelated addition instead of on a real
    # leak — and a brittle guard gets deleted rather than fixed.
    assert "badenath" not in json.dumps(d), d
    for key, value in d.items():
        if key in ("nidra_root",) or not isinstance(value, str):
            continue
        assert value.startswith(home), \
            "%s escaped the sandbox home: %s" % (key, value)


def test_an_existing_layout_is_not_moved():
    """A conventional directory that EXISTS must beat the fresh default, or
    upgrading the tool silently orphans someone's data."""
    import importlib
    home = tempfile.mkdtemp()
    os.makedirs(os.path.join(home, "claude-sync", "goals"))
    env_home = os.environ.get("HOME")
    try:
        os.environ["HOME"] = home
        os.environ.pop("MEDITATE_GOALS_DIR", None)
        import paths
        importlib.reload(paths)
        got = paths.goals_dir()
        assert got == os.path.join(home, "claude-sync", "goals"), got
    finally:
        if env_home:
            os.environ["HOME"] = env_home
        import paths
        importlib.reload(paths)


def test_project_slug_follows_the_caller_not_a_baked_in_default():
    """The defect was the author's slug as a DEFAULT VALUE, so every machine
    that failed detection silently wrote into his directory. A slug that
    contains the current user's name is correct — that is what a slug is."""
    import paths
    assert paths.project_slug("/Users/someone/code/thing") == \
        "-Users-someone-code-thing"
    assert paths.project_slug("/tmp/x") == "-tmp-x"
    src = open(os.path.join(SKILL, "formation.py")).read()
    assert "-Users-badenath-projects-vedic-puran" not in src, \
        "the author's project is still a fallback slug"


def test_install_and_uninstall_are_a_matched_pair():
    for script in ("install.sh", "uninstall.sh"):
        p = os.path.join(SKILL, script)
        assert os.path.exists(p), "%s is missing — a tool that wires itself " \
                                  "into settings.json must be removable" % script
        r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
        assert r.returncode == 0, "%s: %s" % (script, r.stderr)
    un = open(os.path.join(SKILL, "uninstall.sh")).read()
    for thing in ("settings.json", "meditate-hook", "com.meditate.grade",
                  ".local/bin/meditate"):
        assert thing in un, "uninstall leaves %s behind" % thing
    assert "--dry-run" in un, "removal must be inspectable before it runs"


def test_uninstall_spares_other_tools_hooks():
    """The expensive mistake: taking someone's other hooks out with you."""
    home = tempfile.mkdtemp()
    os.makedirs(os.path.join(home, ".claude", "hooks"))
    settings = os.path.join(home, ".claude", "settings.json")
    with open(settings, "w") as f:
        json.dump({"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": "~/.claude/hooks/meditate-hook.sh"},
            {"type": "command", "command": "~/.claude/hooks/other-tool.sh"}]}]},
            "model": "opus"}, f)
    subprocess.run(["bash", os.path.join(SKILL, "uninstall.sh")],
                   capture_output=True, text=True,
                   env=dict(os.environ, HOME=home), timeout=60)
    cfg = json.load(open(settings))
    left = [h["command"] for h in cfg["hooks"]["SessionStart"][0]["hooks"]]
    assert left == ["~/.claude/hooks/other-tool.sh"], left
    assert cfg.get("model") == "opus", "unrelated settings must survive"


def test_readme_documents_only_commands_that_exist():
    """A landing page that promises a verb the CLI does not have is the first
    thing a new user hits. Checked both ways."""
    import re as _re
    readme = open(os.path.join(SKILL, "README.md")).read()
    cli = open(os.path.join(SKILL, "meditate")).read()
    documented = set(_re.findall(r"^meditate ([a-z]+)", readme, _re.M))
    # A verb works if the CLI has a case arm for it (`  verb)` or
    # `  a|verb|b)`, possibly with flags like `help|--help|-h)`) OR if the
    # passthrough can resolve it to a script of the same name. The check used
    # to demand a case arm, which tested the implementation instead of the
    # behaviour: after the CLI was cut from 35 branches to 8, `meditate goals`
    # still ran fine via passthrough and the test called it missing.
    def works(v):
        if _re.search(r"^\s*[\w|.-]*\b%s\b[\w|.-]*\)" % v, cli, _re.M):
            return True
        return os.path.exists(os.path.join(SKILL, v + ".py"))
    missing = [v for v in sorted(documented) if not works(v)]
    assert not missing, "README promises verbs the CLI lacks: %s" % missing


def test_readme_install_points_at_the_path_claude_code_searches():
    """Claude Code only discovers skills under ~/.claude/skills — a clone one
    directory off installs cleanly and is never found."""
    readme = open(os.path.join(SKILL, "README.md")).read()
    assert ".claude/skills/meditate" in readme
    assert "get.sh" in readme, "the one-liner is the easiest path; document it"
    boot = open(os.path.join(SKILL, "get.sh")).read()
    assert ".claude/skills/meditate" in boot
    assert "status --porcelain" in boot, \
        "the bootstrap must refuse to clobber local edits"


def test_readme_version_matches_VERSION():
    v = open(os.path.join(SKILL, "VERSION")).read().strip()
    readme = open(os.path.join(SKILL, "README.md")).read()
    assert v in readme, "README's file tree still claims an older version"


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1; print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
