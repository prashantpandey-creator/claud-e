"""The three-product split, made executable.

The owner's decision (2026-08-29, restated 2026-09-02): meditate is its own
product and gets marketed on its own; the twin is what he actually uses.
`test_layers.py` already stops the layers bleeding together. This file pins
the next step — that the meditate layer can be LIFTED OUT and shipped to
someone who has none of the rest.

Measured before writing this: the twin's import closure is 36 of 47 modules
and shares 13 of meditate's 16. That number is why "remove meditate" cannot
mean deleting it — the twin stands ON it. What ships separately is a
generated distribution; the working checkout stays the one source.

The load-bearing test is the NEGATIVE one: a staged product where `import
twin` still succeeds has not been separated, it has been copied.

Run: python3 ~/.claude/skills/meditate/test_release.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

import release  # noqa: E402
import test_layers  # noqa: E402


def _staged():
    d = tempfile.mkdtemp(prefix="rel-")
    dest = os.path.join(d, "meditate")
    return release.build(dest), dest


def test_the_product_is_COMPUTED_not_a_hand_list():
    """A hand-written file list goes stale the first time someone adds an
    import, and then the shipped tarball is missing a module nobody notices
    until a stranger runs it. The closure is walked from the entry points."""
    import inspect
    src = inspect.getsource(release.closure)
    assert "ast" in src or "_imports_of" in src, src[:200]
    p = release.plan()
    # sessions.py is an ENTRY; paths.py is reached only by walking imports
    assert "sessions" in p["modules"] and "paths" in p["modules"], p["modules"]


def test_NO_companion_or_twin_module_rides_along():
    """The whole point. If brain/voice/twin end up in the product, the split
    is decorative — the buyer gets the mascot and the owner's dashboard."""
    p = release.plan()
    leaked = set(p["modules"]) & (test_layers.COMPANION | test_layers.TWIN)
    assert not leaked, "companion/twin leaked into the product: %s" % sorted(leaked)


def test_the_layer_sets_are_SHARED_with_the_ratchet_not_retyped():
    """Two copies of 'which module is companion' drift, and the drift is
    silent — one file says brain is companion, the other ships it."""
    import inspect
    src = inspect.getsource(release)
    assert "test_layers" in src, "release.py must read the ratchet's own sets"


def test_a_staged_product_IMPORTS_with_nothing_else_on_the_path():
    """The falsifying case is a module that only works because the dev
    directory is also importable. Each import runs in a fresh interpreter
    whose path is the staging dir alone."""
    rep, dest = _staged()
    try:
        assert rep["ok"], rep
        bad = [m for m in rep["verify"]["imports"] if not m["ok"]]
        assert not bad, bad
    finally:
        shutil.rmtree(os.path.dirname(dest), ignore_errors=True)


def test_importing_the_TWIN_from_the_product_must_FAIL():
    """The negative check that makes the positive one mean anything. A copy
    of the whole skill dir would pass every test above."""
    rep, dest = _staged()
    try:
        for gone in ("twin", "brain", "voice"):
            r = subprocess.run([sys.executable, "-c", "import " + gone],
                               cwd=dest, capture_output=True, text=True,
                               env={"PATH": os.environ.get("PATH", ""),
                                    "PYTHONPATH": dest, "HOME": os.environ["HOME"]})
            assert r.returncode != 0, "%s is still importable from the product" % gone
        assert rep["verify"]["isolated"] is True, rep["verify"]
    finally:
        shutil.rmtree(os.path.dirname(dest), ignore_errors=True)


def test_the_product_ships_its_OWN_tests():
    """A buyer who cannot run the suite cannot trust the tool. Every staged
    module that has a test file gets it."""
    rep, dest = _staged()
    try:
        staged = set(os.listdir(dest))
        for m in release.plan()["modules"]:
            if os.path.exists(os.path.join(SKILL, "test_%s.py" % m)):
                assert "test_%s.py" % m in staged, m
        assert "test_product.py" in staged, "the buyer's own gate must ship"
        # test_packaging.py guards the CHECKOUT and imports doctor, a
        # companion module. Shipping it hands a buyer a suite that errors on
        # a module the product deliberately does not contain.
        assert "test_packaging.py" not in staged, \
            "test_packaging imports doctor — it cannot run in the product"
    finally:
        shutil.rmtree(os.path.dirname(dest), ignore_errors=True)


def test_the_products_OWN_gate_is_run_against_the_built_product():
    """test_product.py cannot run in the working checkout — it would scan 48
    modules instead of 13. So it must be run HERE, against a real build, or
    it is a file nobody ever executes."""
    rep, dest = _staged()
    try:
        assert rep["ok"], rep["why"]
        r = subprocess.run([sys.executable, "test_product.py"], cwd=dest,
                           capture_output=True, text=True, timeout=180)
        tail = (r.stdout.strip().splitlines() or [""])[-1]
        assert r.returncode == 0, r.stdout[-800:]
        assert "not applicable" not in r.stdout, \
            "the gate no-opped against a real build: " + tail
        assert "/0 passed" not in tail, tail
    finally:
        shutil.rmtree(os.path.dirname(dest), ignore_errors=True)


def test_the_install_line_points_at_the_PRODUCT_repo():
    """The worst kind of broken: the README's curl line returned 200 from
    the COMPANION's repo, so the first command a buyer ran installed the
    wrong software — Casper, a local server and three launch agents — and
    reported success. A 404 would have been kinder."""
    rep, dest = _staged()
    try:
        readme = open(os.path.join(dest, "README.md")).read()
        assert release.PRODUCT_REPO in readme, release.PRODUCT_REPO
        # the falsifier: the companion repo path must not appear at all
        assert "/meditate/main/" not in readme, \
            "install line still points at the companion repo"
        assert "get.sh" in readme and "get.sh" in os.listdir(dest), \
            "README offers a one-liner whose script does not ship"
    finally:
        shutil.rmtree(os.path.dirname(dest), ignore_errors=True)


def test_every_command_the_README_lists_is_one_the_CLI_HANDLES():
    """A table of commands is a promise. One that names a verb the
    dispatcher does not have is found by the buyer, not by us."""
    rep, dest = _staged()
    try:
        import re as _re
        readme = open(os.path.join(dest, "README.md")).read()
        cli = open(os.path.join(dest, "meditate")).read()
        verbs = set(_re.findall(r"`meditate (\w+)", readme))
        missing = [v for v in sorted(verbs)
                   if not (_re.search(r"^\s+%s[)|]" % v, cli, _re.M)
                           or _re.search(r"\|%s[)|]" % v, cli))]
        assert not missing, "README promises verbs the CLI lacks: %s" % missing
    finally:
        shutil.rmtree(os.path.dirname(dest), ignore_errors=True)


def test_the_report_says_what_was_LEFT_OUT_not_only_what_shipped():
    """A build report listing 16 shipped files reads as complete. The 31 it
    did not ship are the fact that decides whether the split is right."""
    p = release.plan()
    assert p["excluded"], p
    assert "twin" in p["excluded"] and "brain" in p["excluded"], p["excluded"]
    assert len(p["modules"]) + len(p["excluded"]) == p["all_modules"], p


def test_a_FAILED_verify_refuses_to_publish():
    """Publishing an unverified build is how a broken product reaches a
    buyer. publish() takes the report and must refuse a red one."""
    import inspect
    src = inspect.getsource(release.publish)
    assert "ok" in src, src[:300]
    r = release.publish({"ok": False}, remote="", dry=True)
    assert r["published"] is False and r["why"], r


def test_publish_is_DRY_unless_told_otherwise():
    """Nothing outward-facing happens by accident."""
    import inspect
    sig = inspect.signature(release.publish)
    assert sig.parameters["dry"].default is True


def test_the_cli_emits_JSON():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "release.py"),
                        "--plan", "--json"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]
    d = json.loads(r.stdout)
    assert d["ok"] is True and d["data"]["modules"], d


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
