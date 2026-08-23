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
BIN = os.path.join(SKILL, "mascot", "Casper.app", "Contents", "MacOS", "casper")


def _hear(said: str) -> str:
    r = subprocess.run([BIN, "--hear", said], capture_output=True, text=True,
                       timeout=30)
    return r.stdout


def _built() -> bool:
    return os.access(BIN, os.X_OK)


def test_voice_never_ships_through_the_mascot():
    """The falsifier for the old hole: this exact lane used to reach the LLM."""
    if not _built():
        print("       (skipped: mascot not built here)")
        return
    for utter in ("Casper, push it to production",
                  "Casper, deploy the backend now",
                  "Casper, merge it and release"):
        out = _hear(utter)
        assert "REFUSED (code, not prompt)" in out, (utter, out)
        assert "won't push or deploy by voice" in out, (utter, out)


def test_commands_become_offers_not_executions():
    if not _built():
        print("       (skipped: mascot not built here)")
        return
    out = _hear("Casper, run the fleet please")
    assert "OFFER meditate go" in out, out
    assert "nothing runs without Yes" in out, out
    out = _hear("Casper, fix what broke")
    assert "OFFER meditate fix" in out, out


def test_overheard_talk_stays_silent():
    if not _built():
        print("       (skipped: mascot not built here)")
        return
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
