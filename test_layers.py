"""The owner's separation decision, enforced: core < companion < twin.

Decision (owner, 2026-08-29): meditate core, meditate+Casper, and the digital
twin are THREE products — a progression, kept separable. A repo split during
a week with eight live sessions in one checkout is a coordination event, not
an edit; what CAN be done today is stopping the layers bleeding into each
other, so the split stays possible.

Measured before writing this: 44 modules, FIVE upward imports. That number
is the whole case — the progression is already almost layered, and every new
upward import from here is a step away from the owner's decision.

The allowed-list below is a RATCHET, not a permission: it may only shrink.
A new upward import fails this test; removing a listed one without deleting
its entry here also fails, so the list can never quietly go stale.

Run: python3 ~/.claude/skills/meditate/test_layers.py
"""
from __future__ import annotations

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))

# The three products. tree and dashboard are VIEWS over the brain, so they
# live with the companion even though they feel core — tree.build() reads
# brain.state(), and a core module may never do that.
COMPANION = {"brain", "advisor", "voice", "converse", "tts", "beacon",
             "insights", "brief", "dashboard", "distill_speech", "inbox",
             "tree"}
TWIN = {"twin"}

# The five inversions that existed when the decision was made. Each is a
# small untangle, none is new work invented by this test. Shrink only.
ALLOWED_UPWARD = {
    ("coordination", "inbox"),
    ("fleet", "beacon"),
    ("goals", "beacon"),
    ("status", "voice"),
}


def _modules():
    return {f[:-3] for f in os.listdir(SKILL_DIR)
            if f.endswith(".py") and not f.startswith("test_")}


def _imports_of(mod, mods):
    try:
        src = open(os.path.join(SKILL_DIR, mod + ".py"), errors="ignore").read()
        t = ast.parse(src)
    except Exception:
        return set()
    out = set()
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module.split(".")[0])
    return out & mods


def test_core_never_imports_companion_or_twin():
    mods = _modules()
    core = mods - COMPANION - TWIN
    found = set()
    for m in sorted(core):
        for up in _imports_of(m, mods) & (COMPANION | TWIN):
            found.add((m, up))
    new = found - ALLOWED_UPWARD
    assert not new, (
        "NEW upward import(s) — core reaching into companion/twin breaks the "
        "owner's three-product separation: %s" % sorted(new))


def test_companion_never_NEEDS_the_twin():
    """First run of the blanket version of this test caught the twin PAGE:
    brain._twin_cached imports twin, added the same day. The rule that
    actually protects the split is separability, not purity — the brain is
    the one server delivering all three products on a machine that has them,
    so it MAY host the twin's view, but it must keep working on a machine
    that lacks the twin entirely. So: no module-level import (companion must
    IMPORT clean without twin.py on disk), and any lazy use sits in a
    try/except with a degrade path."""
    mods = _modules()
    for m in sorted(COMPANION & mods):
        src = open(os.path.join(SKILL_DIR, m + ".py"), errors="ignore").read()
        t = ast.parse(src)
        top = {a.name.split(".")[0] for n in t.body if isinstance(n, ast.Import)
               for a in n.names}
        top |= {n.module.split(".")[0] for n in t.body
                if isinstance(n, ast.ImportFrom) and n.module}
        assert not (top & TWIN), \
            "%s (companion) imports twin at MODULE level — it would die " \
            "without it" % m
        # lazy imports must be inside a try so absence degrades, not crashes
        for n in ast.walk(t):
            names = set()
            if isinstance(n, ast.Import):
                names = {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                names = {n.module.split(".")[0]}
            if names & TWIN:
                guarded = any(isinstance(a, ast.Try) and
                              n in [x for b in ast.walk(a) for x in [b]]
                              for a in ast.walk(t) if isinstance(a, ast.Try))
                assert guarded, \
                    "%s lazily imports twin OUTSIDE a try — no degrade path" % m


def test_the_ratchet_only_tightens():
    """A listed inversion that no longer exists must be DELETED from the
    list — otherwise the list rots into folklore and the day someone
    reintroduces the import, this test says nothing."""
    mods = _modules()
    core = mods - COMPANION - TWIN
    live = set()
    for m in sorted(core):
        for up in _imports_of(m, mods) & (COMPANION | TWIN):
            live.add((m, up))
    stale = ALLOWED_UPWARD - live
    assert not stale, (
        "these inversions are FIXED — remove them from ALLOWED_UPWARD so the "
        "ratchet tightens: %s" % sorted(stale))


def test_twin_may_import_both_but_must_exist_alone():
    """The twin is the top of the progression: it may read everything, but a
    machine without the companion still gets a twin — its imports of
    companion modules must all be guarded (inside try or function bodies),
    proven by the stranger test importing it with a bare store."""
    import twin  # noqa: F401  — module-level import must not require brain
    src = open(os.path.join(SKILL_DIR, "twin.py")).read()
    t = ast.parse(src)
    top = {a.name.split(".")[0] for n in t.body if isinstance(n, ast.Import)
           for a in n.names}
    top |= {n.module.split(".")[0] for n in t.body
            if isinstance(n, ast.ImportFrom) and n.module}
    hard = top & (COMPANION | {"brain"})
    assert not hard, "twin hard-imports companion at module level: %s" % sorted(hard)


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
