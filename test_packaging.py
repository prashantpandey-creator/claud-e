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
# The rule itself now lives in paths.py, which SHIPS. It used to live here,
# and coordination.py's live squiggle imported it from this test file — so a
# build without tests silently had no rule at all. Re-exported so this file
# and its callers keep working, but there is one definition.
from paths import PERSONAL, EXEMPT_FILES, code_lines as _paths_code_lines


def _shipped_modules():
    for fn in sorted(os.listdir(SKILL)):
        if fn.endswith(".py") and not fn.startswith("test_") \
                and fn not in EXEMPT_FILES:
            yield fn


def _code_lines(path):
    """Kept as this file's name for the shared rule in paths.py."""
    return _paths_code_lines(path, SKILL)


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
    for thing in ("settings.json", "meditate-hook", "com.meditate.rounds",
                  ".local/bin/meditate"):
        assert thing in un, "uninstall leaves %s behind" % thing
    assert "--dry-run" in un, "removal must be inspectable before it runs"


def test_every_service_label_supervises_the_program_it_names():
    """A launchd label is a promise about which program it restarts.

    com.meditate.brain ran `ollama serve` for four days while brain.py itself
    ran unsupervised — so `launchctl kickstart com.meditate.brain` restarted
    the model server, an edit to brain.py never reached the live Pulse, and
    the command reported success the whole time. Checked statically against
    doctor.SERVICES so the installer and the health check cannot drift apart.
    """
    sys.path.insert(0, SKILL)
    import doctor

    inst = open(os.path.join(SKILL, "install.sh")).read()
    calls = re.findall(r"^\s*_svc\s+(\S+)\s+\S+\s+(.*)$", inst, re.M)
    assert calls, "install.sh installs no services — did _svc get renamed?"
    for label, program in calls:
        expects = doctor.SERVICES.get(label)
        assert expects, "install.sh installs %s, doctor does not check it" % label
        # lowercased: the program is usually a shell var ("$OLLAMA") whose name
        # is the upper-case of the thing it runs.
        assert expects in program.lower(), \
            "%s runs %r — it must run %s" % (label, program.strip(), expects)

    un = open(os.path.join(SKILL, "uninstall.sh")).read()
    for label in doctor.SERVICES:
        assert label in un, "uninstall leaves the KeepAlive job %s running" % label


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


def test_no_shell_assignment_DIES_when_its_search_finds_nothing():
    """This bug class has now bitten twice, both times as "absence read as
    failure" under `set -euo pipefail`:

      build.sh:  SIGN_ID=$(security find-identity ... | grep "Apple Dev...")
                 -> no signing identity on a CI runner, grep exits 1,
                    pipefail propagates, set -e kills the build. CI red.
      meditate:  LASTCRASH=$(ls .../casper-*.ips 2>/dev/null | head -1)
                 -> no crash reports, ls exits 1, same chain. The bot was
                    unstartable BECAUSE it had never crashed.

    Any `VAR=$(... | ...)` whose command can legitimately find nothing must
    end in `|| true`, or the script dies looking for something optional."""
    import re as _re
    for name in ("meditate", os.path.join("mascot", "build.sh"), "install.sh", "heartbeat.sh"):
        path = os.path.join(SKILL, name)
        if not os.path.exists(path):
            continue
        src = open(path).read()
        if "set -e" not in src:
            continue
        # Only substitutions that SEARCH. `echo "$V" | cut` cannot find
        # nothing — echo always succeeds — so requiring a guard there would
        # be noise, and a test that cries wolf gets edited out.
        searching = _re.compile(r"\$\(\s*(ls|grep|egrep|rg|find|pgrep|security|awk|comm)\b")
        for m in _re.finditer(r"^\s*[A-Z_][A-Z0-9_]*=\$\((?:[^()]|\\\n)*?\)",
                              src, _re.M | _re.S):
            block = m.group(0)
            if "|" not in block or not searching.search(block):
                continue
            assert "|| true" in block or "|| echo" in block, \
                "%s: `%s` dies under set -e when its search finds nothing" \
                % (name, " ".join(block.split())[:90])


def test_every_inline_python_in_install_sh_COMPILES():
    """CI has been red on every push since at least 2026-09-03 (25 of 25
    runs). One cause: the heartbeat plist block calls os.path.dirname()
    and imports only plistlib, sys, shutil — `NameError: name 'os' is not
    defined` on a fresh machine, so the heartbeat is never installed and
    nothing says so. The local suite could not see it: this machine's
    plist predates the line. Every heredoc that python3 executes gets
    compiled here, with its own names checked."""
    import ast
    src = open(os.path.join(SKILL, "install.sh")).read()
    blocks = re.findall(r"python3 [^\n]*<<'(\w+)'\n(.*?)\n\1\n", src, re.S)
    assert blocks, "no inline python found in install.sh — did the syntax change?"
    for tag, body in blocks:
        compile(body, "install.sh:%s" % tag, "exec")          # syntax
        tree = ast.parse(body)
        import builtins
        bound = set(dir(builtins)) | {"__name__", "__file__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    bound.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    bound.add(a.asname or a.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for t in ([node.target] if hasattr(node, "target") and node.target else getattr(node, "targets", [])):
                    for nn in ast.walk(t):
                        if isinstance(nn, ast.Name):
                            bound.add(nn.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.comprehension):
                for nn in ast.walk(node.target):
                    if isinstance(nn, ast.Name):
                        bound.add(nn.id)
            elif isinstance(node, ast.For):
                for nn in ast.walk(node.target):
                    if isinstance(nn, ast.Name):
                        bound.add(nn.id)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        for nn in ast.walk(item.optional_vars):
                            if isinstance(nn, ast.Name):
                                bound.add(nn.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, ast.Lambda):
                for a in node.args.args:
                    bound.add(a.arg)
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        missing = sorted(used - bound)
        assert not missing, "install.sh:%s uses undefined name(s): %s" % (tag, missing)


def test_the_mascot_build_SURVIVES_a_machine_with_no_signing_certificate():
    """CI's 'Build the mascot' step failed on all 25 runs since 2026-09-03,
    and because it has no `|| true`, the SUITE STEP NEVER RAN — 25 commits
    with no gate at all. Cause: build.sh runs `set -euo pipefail`, and the
    identity lookup is `SIGN_ID=$(security find-identity ... | grep "Apple
    Development" | ...)`. On a runner with no certificate grep matches
    nothing, returns 1, pipefail propagates it, and set -e kills the script
    BEFORE the `else` branch written to sign ad-hoc for exactly that case.
    Proven locally: the same pipeline with a non-matching pattern exits 1.
    This runs the real line out of the real file."""
    import subprocess
    src = open(os.path.join(SKILL, "mascot", "build.sh")).read()
    m = re.search(r"^(\s*SIGN_ID=\$\(security find-identity.*?\)\s*)$", src, re.M | re.S)
    assert m, "the identity lookup in mascot/build.sh no longer looks like this — re-read it"
    line = m.group(1).replace("Apple Development", "NoSuchIdentity-ZZZ")
    r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + line + "\necho REACHED"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "REACHED" in r.stdout, (
        "build.sh dies on a machine with no signing certificate instead of "
        "falling through to ad-hoc signing (rc=%s)" % r.returncode)


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
