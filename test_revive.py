"""Tests for projects.revival_cards — surfacing what was started and left.

WHY (measured on the real machine 2026-08-26):

The ask was for the fleet to grow work in the right direction, including into
old unfinished projects. Direction lives in goal files; there are 6, covering
3 of 76 products. So the obvious move is to DERIVE a goal for the rest.

That was measured before it was built, and it does not work:

  unchecked `- [ ]` boxes found in   3 of 19 resolvable repos
  and two of those three (carrymate, flight postman) carried the SAME 47
  boxes — a copy-pasted README template, not anyone's direction.

A goal synthesised from a template is fiction with a citation on it, which is
the exact failure this tool keeps finding in itself: an absent answer rendered
as a present one.

What survived the same check:

  last commit subject   present and specific in 8 of 8 dormant repos
  README first line     15 of 19, but boilerplate in some (create-next-app)

So a revival card QUOTES and never synthesises. Where the README says nothing
of its own, `what` is None — reported as a gap rather than filled in with the
scaffold's sentence.

Run: python3 ~/.claude/skills/meditate/test_revive.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projects  # noqa: E402


def _repo(root, name, readme=None):
    p = os.path.join(root, name)
    os.makedirs(p, exist_ok=True)
    if readme is not None:
        with open(os.path.join(p, "README.md"), "w") as f:
            f.write(readme)
    return p


# ---------------------------------------------------------------------------
# the README headline — what the repo says it is
# ---------------------------------------------------------------------------

def test_headline_is_the_first_line_of_PROSE_not_the_banner():
    with tempfile.TemporaryDirectory() as d:
        p = _repo(d, "a", "# Bro OS\n\n![badge](x.png)\n\nA private macOS conductor.\n")
        assert projects._readme_headline(p) == "A private macOS conductor."


def test_a_readme_with_only_a_title_falls_back_to_the_title():
    with tempfile.TemporaryDirectory() as d:
        p = _repo(d, "a", "# Fluency Bridge\n")
        assert projects._readme_headline(p) == "Fluency Bridge"


def test_no_readme_is_None_not_a_guess():
    with tempfile.TemporaryDirectory() as d:
        assert projects._readme_headline(_repo(d, "a")) is None


def test_boilerplate_is_DERIVED_from_repetition_not_a_pattern_list():
    """`This is a Next.js project bootstrapped with create-next-app` is what
    the generator wrote. The rule that catches it is the same one that
    separates a third-party checkout from your own work — a line appearing in
    more than one repo came from a template. No list to maintain, and it
    adapts to whatever scaffolds a given machine uses."""
    boiler = "This is a Next.js project bootstrapped with create-next-app."
    with tempfile.TemporaryDirectory() as d:
        dirs = {"one": _repo(d, "one", "# One\n\n%s\n" % boiler),
                "two": _repo(d, "two", "# Two\n\n%s\n" % boiler),
                "real": _repo(d, "real", "# Real\n\nA storefront for hand-made rolling papers.\n")}
        found = projects._boilerplate_headlines(dirs)
        assert boiler in found
        assert "A storefront for hand-made rolling papers." not in found, \
            "a one-off description was called boilerplate"


def test_a_UNIQUE_description_is_never_called_boilerplate():
    """FALSIFIER. Over-flagging would blank out the one useful line."""
    with tempfile.TemporaryDirectory() as d:
        dirs = {"solo": _repo(d, "solo", "# Solo\n\nThe only repo here.\n")}
        assert projects._boilerplate_headlines(dirs) == set()


# ---------------------------------------------------------------------------
# the card itself — quoted, never synthesised
# ---------------------------------------------------------------------------

def test_every_card_carries_the_last_commit_subject():
    """This is the field the measurement actually supported: specific in 8 of
    8 dormant repos, where unchecked boxes were usable in 1."""
    for c in projects.revival_cards():
        assert c["last_commit"], "%s has a card with nothing quoted on it" % c["project"]
        assert c["idle"], "%s has no age — 'dormant' with no measure of how long" % c["project"]


def test_a_name_with_no_repo_on_disk_gets_NO_card():
    """16 of 35 gap entries are transcript-derived names with no repo (`web`,
    `private`, `purangpt-launch-assets`). There is nothing to quote, so there
    is nothing to say — inventing a card for them would be the whole defect
    this tool exists to catch."""
    assert projects.revival_cards(names=["zzz-not-a-real-repo-anywhere"]) == []


def test_boilerplate_readme_leaves_what_EMPTY_rather_than_filled_in():
    """gurugpt-next's README opens with the create-next-app sentence. Better
    to say nothing about what it is than to repeat the scaffold."""
    cards = {c["project"]: c for c in projects.revival_cards()}
    c = cards.get("gurugpt-next")
    if c:
        assert not (c["what"] or "").lower().startswith("this is a next.js"), \
            "the scaffold's sentence is being presented as the project description"


def test_cards_are_ranked_by_how_much_was_built():
    cards = projects.revival_cards()
    counts = [c["commits"] or 0 for c in cards]
    assert counts == sorted(counts, reverse=True)


def test_no_card_claims_a_goal_it_does_not_have():
    """A card is evidence, not a plan. `has_goal` is False on every one until
    a goal file actually exists — the tool must never look like it decided
    the direction."""
    for c in projects.revival_cards():
        assert c["has_goal"] is False


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
