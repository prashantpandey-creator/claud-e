"""Tests for advisor._creed — the half of the twin that was missing.

WHY (measured 2026-08-29):

Casper's prompt was 1,400 characters of pure STATE — goals, counts, projects,
what broke. He knew everything about what was happening and nothing about how
the owner decides. The store holds 41 memories that ARE his judgement
(`type: feedback`, a correction he gave with the reason for it) and who he is
(`type: user`). Not one reached him. Probed the live FACTS block for "never",
"Groq", "worktree", "push", "terse", "subtract", "proof" — all seven absent.

Worse, the hand-written persona did not merely omit them, it CONTRADICTED
one. Its rule 4 read "Never suggest pushing or deploying — that call stays
with them", against his standing instruction since 2026-08-25: push
automatically when the suite is green, do not ask. A rule invented FOR him
was overriding one measured FROM him. That rule is gone.

PROVEN, same prompt to the same local model, only the creed differing:

  with     "Don't use Groq. It's unavailable to you. ... run a test call,
            then show me the actual usage metadata."
  without  "I'd check the service specs for the one that already runs in the
            marketplace..."

Two of his standing rules in one answer (never Groq; verify cost with a test
call first) against generic waffle. The A/B before that one was invalid and
is worth recording: asking the SAME question twice put the previous answer in
the conversation block, and the model replayed it verbatim — which read as
"the creed changes nothing". The prompts differed by 7,222 characters the
whole time. Measure the input, not just the output.

Run: python3 ~/.claude/skills/meditate/test_creed.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import advisor  # noqa: E402


_REAL_CREED_FILES = advisor._creed_files


def _mem(d, name, mtype, description, body="x"):
    p = os.path.join(d, name + ".md")
    with open(p, "w") as f:
        f.write("---\nname: %s\ndescription: %s\nmetadata:\n  type: %s\n---\n\n%s\n"
                % (name, description, mtype, body))
    return p


def _with_dir(d):
    """Point the creed at one scratch directory and clear its cache."""
    advisor._creed_files = lambda: sorted(
        os.path.join(d, f) for f in os.listdir(d) if f.endswith(".md"))
    advisor._CREED_CACHE.update({"key": None, "text": ""})


# ---------------------------------------------------------------------------
# what belongs in it
# ---------------------------------------------------------------------------

def test_ONLY_instructions_he_gave_get_in():
    """`project` and `reference` are facts about the work — they already
    arrive in the FACTS block, and duplicating them here would spend the
    budget on state twice while his rules got dropped."""
    with tempfile.TemporaryDirectory() as d:
        _mem(d, "a-rule", "feedback", "STANDING: never use Groq")
        _mem(d, "c-work", "project", "PuranGPT mobile is at 6 of 8")
        _mem(d, "d-link", "reference", "The dashboard lives at example.com")
        _with_dir(d)
        c = advisor._creed()
        assert "never use Groq" in c
        assert "6 of 8" not in c, "a project fact leaked into the creed"
        assert "example.com" not in c, "a reference leaked into the creed"


def test_observations_ABOUT_him_never_enter_the_creed():
    """`type: user` was in the first cut and was pulled back out.

    It holds observations about him, not instructions from him — and one of
    the two on this machine is a private read of a pricing-anxiety pattern,
    traced to a worry he voiced once about how clients see him. Casper speaks
    ALOUD, unprompted, on a timer, in whatever room the laptop is in. A
    behavioural note about its owner is not a rule for deciding and must not
    be one sampling step from being said out loud. Verified against the live
    store: 'anxiety tell', 'self-worth', 'Indian developer' — all absent.
    """
    with tempfile.TemporaryDirectory() as d:
        _mem(d, "a-rule", "feedback", "STANDING: never use Groq")
        _mem(d, "b-private", "user", "he re-prices upward when anxious")
        _with_dir(d)
        c = advisor._creed()
        assert "never use Groq" in c
        assert "anxious" not in c, "a private observation about him is in the prompt"

    # ...and on the REAL store, by that memory's own distinctive words.
    # _with_dir leaves _creed_files pointed at the (now deleted) temp dir, so
    # it has to be put back before asking the live question.
    advisor._creed_files = _REAL_CREED_FILES
    advisor._CREED_CACHE.update({"key": None, "text": ""})
    live = advisor._creed()
    for private in ("anxiety tell", "self-worth", "Indian developer"):
        assert private.lower() not in live.lower(), \
            "%r reached the speaking prompt" % private


def test_a_memory_with_no_description_is_SKIPPED_not_guessed_at():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "x.md"), "w") as f:
            f.write("---\nname: x\nmetadata:\n  type: feedback\n---\n\nbody only\n")
        _mem(d, "y", "feedback", "a real rule")
        _with_dir(d)
        c = advisor._creed()
        assert "body only" not in c
        assert "a real rule" in c


def test_an_empty_store_yields_an_EMPTY_creed_not_a_placeholder():
    """No rules recorded is a real answer. Inventing a default creed would be
    putting words in his mouth, which is the one thing a twin must not do."""
    with tempfile.TemporaryDirectory() as d:
        _with_dir(d)
        assert advisor._creed() == ""
        assert advisor._system("BASE") == "BASE", \
            "an empty creed still added the precedence header"


# ---------------------------------------------------------------------------
# stability — it leads the prompt, so it has to be byte-identical
# ---------------------------------------------------------------------------

def test_the_creed_is_BYTE_IDENTICAL_between_questions():
    """It sits in front of the volatile facts specifically so the prompt
    prefix stays cached (the KV-cache finding: a stable creed leading the
    prompt ran 76-94% warm). Any per-call variation throws that away."""
    with tempfile.TemporaryDirectory() as d:
        for n in ("m", "a", "z", "k"):
            _mem(d, n, "feedback", "rule " + n)
        _with_dir(d)
        first = advisor._creed()
        advisor._CREED_CACHE.update({"key": None, "text": ""})   # force a re-read
        assert advisor._creed() == first, "the creed reordered between reads"


def test_a_rule_written_TODAY_reaches_the_next_question():
    """FALSIFIER for the cache. A creed that cannot learn a new rule until a
    restart is a twin that stopped listening."""
    with tempfile.TemporaryDirectory() as d:
        _mem(d, "old", "feedback", "the first rule")
        _with_dir(d)
        assert "the first rule" in advisor._creed()
        time.sleep(0.01)
        _mem(d, "new", "feedback", "a rule he just gave")
        c = advisor._creed()
        assert "a rule he just gave" in c, "a new standing rule never reached him"


def test_the_creed_is_BUDGETED():
    """It is a prompt prefix on every question. Unbounded, one long memory
    description could crowd out the facts the answer is supposed to rest on."""
    with tempfile.TemporaryDirectory() as d:
        for i in range(200):
            _mem(d, "r%03d" % i, "feedback", "rule number %d " % i + "x" * 150)
        _with_dir(d)
        c = advisor._creed(max_chars=2000)
        assert len(c) <= 2000, "the budget was ignored: %d chars" % len(c)
        assert c, "the budget emptied it entirely"


def test_one_enormous_description_is_cut_not_dropped():
    with tempfile.TemporaryDirectory() as d:
        _mem(d, "big", "feedback", "y" * 900)
        _with_dir(d)
        line = advisor._creed().splitlines()[0]
        assert len(line) <= 205, "a 900-char description went in whole"
        assert line.endswith("…")


# ---------------------------------------------------------------------------
# precedence — the point of the whole thing
# ---------------------------------------------------------------------------

def test_his_rules_come_AFTER_the_persona_and_are_said_to_outrank_it():
    """Without the precedence line a conflict resolves by whichever is
    phrased more forcefully, and the hand-written persona is phrased very
    forcefully. It has to be said plainly that the measured rule wins."""
    with tempfile.TemporaryDirectory() as d:
        _mem(d, "r", "feedback", "STANDING: push when the suite is green")
        _with_dir(d)
        s = advisor._system("PERSONA TEXT")
        assert s.index("PERSONA TEXT") < s.index("push when the suite is green")
        assert "HIS RULE WINS" in s


def test_the_persona_no_longer_forbids_what_he_told_it_to_do():
    """The contradiction, as a rule. His standing instruction since
    2026-08-25 is to push automatically when the suite is green; the persona
    said never to suggest it."""
    assert "Never suggest pushing or deploying" not in advisor.SYSTEM, \
        "the persona is contradicting the owner's own standing instruction again"


def test_he_is_told_not_to_RECITE_the_rules():
    """A twin acts on its judgement. Reading the rulebook aloud is the
    tell that it is a lookup table wearing a voice."""
    with tempfile.TemporaryDirectory() as d:
        _mem(d, "r", "feedback", "some rule")
        _with_dir(d)
        assert "never recite them back" in advisor._system("BASE").lower()


def test_the_creed_actually_REACHES_the_prompt():
    """Built-not-wired is the failure this repo keeps catching. Measured on
    the live path: _system() called once per question, 8,984 characters,
    HOW HE WORKS present — and the prompt sent to the model was 12,608 chars
    with the creed against 5,386 without."""
    with tempfile.TemporaryDirectory() as d:
        _mem(d, "r", "feedback", "a distinctive standing rule about widgets")
        _with_dir(d)
        s = advisor._system()
        assert "a distinctive standing rule about widgets" in s
        assert "HOW HE WORKS" in s


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    real_files = advisor._creed_files
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok   %s" % fn.__name__)
        except AssertionError as e:
            failed += 1; print("  FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:
            failed += 1; print("  ERR  %s: %s: %s" % (fn.__name__, type(e).__name__, e))
        finally:
            advisor._creed_files = real_files
            advisor._CREED_CACHE.update({"key": None, "text": ""})
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
