"""The gate that ships WITH the product, runnable by someone who bought it.

`test_packaging.py` guards the working checkout's install story and imports
`doctor` — a companion module. Shipping it would hand a buyer a suite that
errors on a module the product deliberately does not contain, so the product
gets its own gate: this file, which touches nothing outside the 13 shipped
modules.

What it pins is the failure a buyer actually hits: a module that only worked
because it was sitting next to the author's other 35 files, or a path that
only exists on one Mac.

Run: python3 test_product.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Things that exist on exactly one machine. Comments and docs may name them;
# code may not depend on them.
PERSONAL = [r"/Users/[a-z]+/", r"\bbadenath\b", r"expanduser\([\"']~/claude-sync"]


def _shipped():
    return sorted(f for f in os.listdir(HERE)
                  if f.endswith(".py") and not f.startswith("test_"))


def _bare(args):
    """A fresh interpreter that can see this directory and nothing else."""
    return subprocess.run([sys.executable] + args, cwd=HERE, timeout=120,
                          capture_output=True, text=True,
                          env={"PATH": os.environ.get("PATH", ""),
                               "HOME": os.environ.get("HOME", ""),
                               "PYTHONPATH": HERE,
                               "PYTHONDONTWRITEBYTECODE": "1"})


def test_every_shipped_module_imports_alone():
    bad = []
    for f in _shipped():
        r = _bare(["-c", "import " + f[:-3]])
        if r.returncode:
            bad.append((f, (r.stderr.strip().splitlines() or [""])[-1]))
    assert not bad, bad


def test_no_shipped_module_depends_on_one_persons_machine():
    """The defect: five modules once hardcoded the author's own layout, so on
    anybody else's machine they imported nothing and graded nothing — and
    said so in no way the user could see.

    DEPENDS, not mentions. paths.py's docstring quotes all five bad paths on
    purpose, as the record of what was fixed; a first cut of this test read
    that docstring and reported the fix as the bug."""
    from paths import PERSONAL as RULE, EXEMPT_FILES, code_lines
    hits = []
    for f in _shipped():
        if f in EXEMPT_FILES:
            continue
        for line_no, line in enumerate(code_lines(f, HERE), 1):
            for pat in RULE:
                if re.search(pat, line):
                    hits.append("%s:~%d %s" % (f, line_no, line.strip()[:80]))
                    break
    assert not hits, hits


def test_the_packaging_RULE_ships_with_the_code_it_checks():
    """It used to live in test_packaging.py, and coordination.py's live
    squiggle imported it from there. Ship the code without the tests and the
    check returned [] forever — reporting clean because the rule was gone."""
    import paths
    assert paths.PERSONAL and paths.EXEMPT_FILES and paths.code_lines
    src = open(os.path.join(HERE, "coordination.py"), errors="ignore").read()
    assert "from test_packaging import" not in src, \
        "shipped code still reaches into a test file for its rule"


def test_the_command_runs_and_its_help_matches_what_it_dispatches():
    """A help text listing a verb the case statement does not handle is a
    lie the buyer finds first."""
    cli = os.path.join(HERE, "meditate")
    if not os.path.exists(cli):
        return  # running from the working checkout, where the CLI is the companion's
    r = subprocess.run(["bash", cli, "--help"], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr[-300:]
    src = open(cli).read()
    listed = re.findall(r"^\s+meditate (\w+)", r.stdout, re.M)
    assert listed, r.stdout
    for verb in listed:
        assert re.search(r"^\s+%s[)|]" % verb, src, re.M) or \
               re.search(r"\|%s[)|]" % verb, src), \
            "help lists `%s`, the dispatcher does not handle it" % verb


def test_the_product_does_NOT_contain_the_companion_or_the_twin():
    """What makes this a separate product rather than a copy."""
    for gone in ("twin", "brain", "voice", "casper"):
        assert not os.path.exists(os.path.join(HERE, gone + ".py")), gone
    assert not os.path.exists(os.path.join(HERE, "mascot")), "mascot shipped"


# Every place shipped code deletes a file, read and accounted for. A RATCHET:
# a new delete site fails this test until somebody reads it and either
# removes it or adds it here with what it deletes. It may not be widened by
# reflex — that is the whole value of it being a list and not a regex.
DELETE_SITES = {
    # the repair queue this same function wrote, when the queue is empty
    ("ask.py", "os.unlink(path)"),
    # its own stale .json coordination state, older than PRUNE_AGE
    ("coordination.py", "os.unlink(path)"),
}


def test_it_writes_only_where_it_says_it_does():
    """The vow, checked: nothing shipped deletes a transcript or a project
    file. A regex cannot tell WHAT a variable named `path` points at, so
    this does not pretend to — it pins the delete sites that were read, and
    fails on any new one."""
    found = set()
    for f in _shipped():
        src = open(os.path.join(HERE, f), errors="ignore").read()
        for line in src.splitlines():
            s = line.split("#")[0].strip()
            if re.search(r"shutil\.rmtree|os\.removedirs|os\.remove\(|os\.unlink\(", s):
                found.add((f, s))
    new = found - DELETE_SITES
    assert not new, ("unreviewed delete site(s) in shipped code — read each "
                     "and add it to DELETE_SITES with what it deletes: %s"
                     % sorted(new))
    gone = DELETE_SITES - found
    assert not gone, ("DELETE_SITES lists sites that no longer exist, so the "
                      "list is stale: %s" % sorted(gone))


def _main():
    # This gate is the BUILT PRODUCT's. Run from the working checkout it
    # would scan 48 modules instead of 13 and fail on files that were never
    # meant to ship — a red that means nothing, which is worse than no test.
    # It is not skipped quietly: test_release.py builds the product and runs
    # this file inside it on every suite run.
    if os.path.exists(os.path.join(HERE, "twin.py")):
        print("  not applicable here — this is the working checkout, not the")
        print("  built product. test_release.py stages the product and runs")
        print("  this same file inside it.")
        print("\n0/0 passed (not applicable)")
        return 0
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
