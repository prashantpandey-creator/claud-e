"""Tests for the mascot's routing guarantees, exercised through the REAL binary.

The no-ship refusal and the command-offer lane are behaviour guarantees. They
used to live only in converse.py (the page's route) while the mascot sent
everything to the LLM — same sentence, two behaviours, and the hard line was
a sentence in a prompt on the lane people actually talk to. Now one
routeDecision() serves both, and `casper --hear` drives it headless so the
guarantee is checkable without talking at a live window.

Contract:
  - "push/deploy/ship it/release/merge" -> REFUSED, by code, before any model
  - command words -> an OFFER (nothing runs without Yes)
  - speech not addressed to him -> silence
  - the built app exists wherever `meditate casper` would run it

Skips (passes) when the binary has not been built on this machine — builds
are gitignored, so CI has no mascot to interrogate.

Run: python3 ~/.claude/skills/meditate/test_mascot_route.py
"""
from __future__ import annotations

import os
import subprocess
import sys

SKILL = os.path.dirname(os.path.abspath(__file__))

# Prefer the BARE binary that build.sh leaves beside the bundle.
#
# Running mascot/Casper.app/Contents/MacOS/casper checks the process in with
# LaunchServices as a second instance of com.meditate.casper — and macOS
# answers that by invalidating the workspace connection of the instance the
# owner is actually using and SIGTERMing it. So running this suite killed the
# live Casper, once per test, and from his side the mascot "closed by itself".
# Observed 2026-08-25: three check-in/death pairs inside 150ms at 14:42:29,
# with the owner's pid 90910 SIGTERMed in the middle of them.
#
# The bare copy has no bundle, so it takes no app slot and disturbs nothing.
# It is the same executable — build.sh copies it from inside the bundle.
_BARE = os.path.join(SKILL, "mascot", "casper")
_BUNDLED = os.path.join(SKILL, "mascot", "Casper.app", "Contents", "MacOS",
                        "casper")
BIN = _BARE if os.access(_BARE, os.X_OK) else _BUNDLED


def _hear(said: str) -> str:
    r = subprocess.run([BIN, "--hear", said], capture_output=True, text=True,
                       timeout=30)
    return r.stdout


def _built() -> bool:
    return os.access(BIN, os.X_OK)


def _require_built() -> None:
    """A missing binary is a FAILURE, not a skip.

    These three tests were written as the only check that the real Casper
    refuses to push/deploy by voice. All three used to print "(skipped:
    mascot not built here)" and return success when the binary was absent —
    and mascot/Casper.app/ is gitignored, so it is absent on every fresh
    checkout, which is every CI run. The guarantee has therefore never once
    been exercised in CI.

    Proven by mutation on 2026-08-25, not by reading: delete the
    push/deploy/ship/release/merge refusal block from routeDecision, compile
    with swiftc, and the mutant answers "OFFER meditate fix" to "Casper, push
    the fix" where the real binary answers "REFUSED (code, not prompt)". The
    whole Python suite stayed green through that mutation. On a clean checkout
    this file printed "3/3 passed" while testing nothing at all.

    A test that reports success without checking anything is worse than no
    test: it occupies the slot where a real check would have gone. CI now
    builds the mascot before the suite (see .github/workflows/test.yml); if
    that build ever breaks, this fails loudly instead of going quiet.
    """
    if not _built():
        raise AssertionError(
            "mascot binary missing at %s — build it with `bash mascot/build.sh`. "
            "This is a FAILURE, not a skip: these tests are the only check that "
            "Casper refuses to push/deploy by voice, and silently passing here "
            "is how that guarantee went untested in CI since it was written." % BIN
        )


def test_voice_never_ships_through_the_mascot():
    """The falsifier for the old hole: this exact lane used to reach the LLM."""
    _require_built()
    for utter in ("Casper, push it to production",
                  "Casper, deploy the backend now",
                  "Casper, merge it and release"):
        out = _hear(utter)
        assert "REFUSED (code, not prompt)" in out, (utter, out)
        assert "won't push or deploy by voice" in out, (utter, out)


def test_commands_become_offers_not_executions():
    _require_built()
    out = _hear("Casper, run the fleet please")
    assert "OFFER meditate go" in out, out
    assert "nothing runs without Yes" in out, out
    out = _hear("Casper, fix what broke")
    assert "OFFER meditate fix" in out, out


def test_overheard_talk_stays_silent():
    _require_built()
    out = _hear("so anyway I told her to fix the deploy pipeline")
    assert "NOT ADDRESSED" in out, out
    assert "REFUSED" not in out and "OFFER" not in out, \
        "overheard speech must produce nothing at all"


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
