"""Tests for the mascot's VOICE guarantees, exercised through the real binary.

Specifically: can you interrupt him, and does his own voice interrupt him by
mistake. Those are two failure modes of one decision, and only one of them is
obvious — a guard that fires on everything looks like it works, right up until
he cuts himself off mid-sentence every time he speaks.

The decision lives in BargeGuard (mascot/Voice.swift) as a plain struct with
no audio in it, so `casper --barge <case>` can drive it with a made-up
loudness trace at 100 buffers a second. The alternative is shouting at a live
window and calling whatever happens a result — which is not a test, and is
also unrunnable in CI, where there is no microphone and no speaker.

The other voice harnesses, `--saytwice` and `--saycancel`, are deliberately
NOT here: they play real audio out of the speakers. They are run by hand.

Run: python3 ~/.claude/skills/meditate/test_mascot_voice.py
"""
from __future__ import annotations

import os
import subprocess
import sys

SKILL = os.path.dirname(os.path.abspath(__file__))
# The bare binary, never the bundle's — running the bundle's executable checks
# in with LaunchServices as a second instance of com.meditate.casper and macOS
# kills the live Casper to make room. Same reasoning as test_mascot_route.py.
_BARE = os.path.join(SKILL, "mascot", "casper")
_BUNDLED = os.path.join(SKILL, "mascot", "Casper.app", "Contents", "MacOS",
                        "casper")
BIN = _BARE if os.access(_BARE, os.X_OK) else _BUNDLED


def _require_built() -> None:
    if not os.access(BIN, os.X_OK):
        raise AssertionError(
            "mascot binary missing at %s — build it with `bash mascot/build.sh`. "
            "A FAILURE, not a skip: silently passing when the binary is absent "
            "is how the no-ship guarantee went untested in CI for months." % BIN)


def _barge(case: str):
    r = subprocess.run([BIN, "--barge", case], capture_output=True, text=True,
                       timeout=30)
    return r.returncode, r.stdout.strip()


def test_you_can_cut_him_off():
    """The whole point: a companion you have to sit through is worse."""
    _require_built()
    code, out = _barge("over")
    assert code == 0, out
    assert "fired=yes" in out, out


def test_his_own_voice_never_cuts_him_off():
    """The failure mode a naive guard has, and the reason for the echo model.

    There is one microphone and it carries both voices. A guard that just
    watches for loudness fires on HIS OWN speech coming back through the
    speakers, so he interrupts himself mid-sentence, every sentence.
    """
    _require_built()
    for case in ("echo", "early"):
        code, out = _barge(case)
        assert code == 0, (case, out)
        assert "fired=no" in out, (case, out)


def test_a_single_spike_is_not_an_interruption():
    """A door, a cough, one syllable of echo. Held for 0.25s or it is noise."""
    _require_built()
    code, out = _barge("blip")
    assert code == 0, out
    assert "fired=no" in out, out


def test_a_full_stop_is_not_always_the_end_of_a_sentence():
    """Version numbers and domain names both carry one that is not.

    Heard out loud before it was fixed: "the iOS app v1." / "2 build 338 is
    waiting", and "swapsafe." / "store!" — each spoken as two sentences with
    the mouth's pause in the middle, which is how a version number turns into
    nonsense. The Python splitter in advisor.py had the same bug and was
    fixed the same day; this is the Swift one the mouth actually uses.
    """
    _require_built()
    r = subprocess.run([BIN, "--sentences"], capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0, r.stdout
    assert "SENTENCE SPLIT CORRECT" in r.stdout, r.stdout


def test_the_apple_lane_says_why_it_cannot_run():
    """A lane that fails must say WHICH wall it hit, not just fail.

    This is the answer to "will it work on all the devices" — no, and the
    three ways it does not are different problems with different fixes:
    an Intel Mac cannot be fixed, Apple Intelligence being off is one switch,
    and a model still downloading fixes itself. Collapsing them into a bare
    failure would leave a user with nothing to do about it.

    Deliberately does NOT assert the model is available: on CI, on an Intel
    Mac, or with the feature switched off it correctly is not, and the point
    is that those all still produce a usable sentence.
    """
    _require_built()
    r = subprocess.run([BIN, "--afm-check"], capture_output=True, text=True,
                       timeout=60)
    out = r.stdout.strip()
    known = ("available", "needs macOS 26 or newer",
             "this Mac cannot run it (Apple silicon only)",
             "Apple Intelligence is switched off in System Settings",
             "the model is still downloading", "built without FoundationModels")
    assert out in known, "unhelpful answer: %r" % out
    assert (r.returncode == 0) == (out == "available"), \
        "exit status must match the verdict, or callers cannot branch on it"


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
