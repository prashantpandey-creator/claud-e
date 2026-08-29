"""Tests for status.status_text — the screen `meditate` prints with no args.

WHY (measured 2026-08-29, reading the real front page):

Three things were wrong with the first thing anyone sees.

1. EVERY GOAL TITLE WAS CHOPPED MID-WORD. The printer sliced `title[:26]` and
   `next[:66]`, so all six goals on this machine read "Meditate closes its own
   lo", "Production stable — paymen", "Astrology readings instant". A title
   cut mid-word is not shortened, it is unreadable — and four of the six were
   only long because the title carries a file-notation tail that goals._headline
   already knows how to drop.

2. THE REPAIR LINE NAMED NOTHING. "Some of what I know stopped matching
   reality" is a mood; it gives a person nothing to decide with. voice.py had
   already solved this exact problem for the spoken briefing (_idea_of_broken:
   say WHICH thing, in the owner's own words) and the front page was still
   printing the mood.

3. WHAT YOU STARTED AND LEFT WAS ON NO SCREEN. Eight dormant repos, computed
   since 2026-08-26, shown nowhere a person opens.

And one defect introduced while fixing (3), which is why the budget test
below exists: wiring projects.revival_cards() straight in walks 42 repos and
shells out per repo, taking the page from 0.0s to 10.0s. A nicety charging
ten seconds on the one screen that has to be instant. It asks the server now,
which holds it warm, with a 0.4s fuse and no line at all when nothing answers.

Run: python3 ~/.claude/skills/meditate/test_front_page.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import status  # noqa: E402


# ---------------------------------------------------------------------------
# _fit — cut at a word, or not at all
# ---------------------------------------------------------------------------

def test_short_text_is_returned_WHOLE_and_unmarked():
    assert status._fit("Mila unblocked", 40) == "Mila unblocked"
    assert "…" not in status._fit("Mila unblocked", 40)


def test_a_cut_lands_on_a_word_boundary():
    out = status._fit("Meditate closes its own loop and proves it", 26)
    assert not out.rstrip("…").endswith(" ")
    assert out.endswith("…"), "a cut with no mark reads as a title that really ends there"
    assert " lo…" not in out and out.rstrip("…") in "Meditate closes its own loop and proves it"
    words = "Meditate closes its own loop and proves it".split()
    assert out.rstrip("…").split()[-1] in words, "cut mid-word: %r" % out


def test_a_word_LONGER_than_the_width_is_still_cut():
    """FALSIFIER for the word-boundary rule. One 40-character token with no
    space in it must not be returned at full length just because there is no
    space to cut on — that would break the column."""
    out = status._fit("Supercalifragilisticexpialidocious" * 2, 20)
    assert len(out) <= 20, "no word boundary, so it was never cut: %r" % out


def test_the_ellipsis_replaces_trailing_punctuation():
    assert not status._fit("one, two, three, four", 12).endswith(",…")


# ---------------------------------------------------------------------------
# the page itself
# ---------------------------------------------------------------------------

def _page():
    return status.status_text()


def test_no_goal_title_is_chopped_mid_word():
    """The defect, stated as a rule. Read the rendered page back and check
    every title line against the words it came from."""
    import goals
    d = status.gather()
    if not d["goals"]:
        return
    page = _page()
    for g in d["goals"]:
        head = goals._headline(g["title"])
        first = head.split()[0]
        assert first in page, "goal %r does not appear on the page at all" % first
    for line in page.splitlines():
        if " done" not in line or not line.startswith("  "):
            continue
        shown = line.split(" done")[0].rsplit(" ", 2)[0].strip()
        if shown.endswith("…"):
            stem = shown.rstrip("…").strip()
            titles = " ".join(goals._headline(g["title"]) for g in d["goals"])
            assert stem in titles, "a title was cut mid-word: %r" % shown


def test_the_working_on_header_is_present():
    """It was dropped in the same edit that fixed the truncation — a section
    of six lines with no heading, which is how the fix read as a new bug."""
    d = status.gather()
    if d["goals"]:
        assert "What you're working on" in _page()


def test_the_repair_line_NAMES_the_thing():
    """A count is not an idea. If the queue is open the page must say which
    memory stopped being true, in the words it was written in."""
    d = status.gather()
    page = _page()
    if not d.get("repair_open"):
        return
    if "You told me:" in page:
        line = [l for l in page.splitlines() if "You told me:" in l][0]
        assert len(line.split("You told me:")[1].strip()) > 8, \
            "named the idea but the idea is empty"
    else:
        # the fallback is allowed, but only when voice cannot name one
        import voice
        assert not (voice._idea_of_broken(voice.STORE_DIR) or {}).get("idea"), \
            "an idea was available and the page printed the mood instead"


# ---------------------------------------------------------------------------
# the budget — the regression I made while adding the dormant line
# ---------------------------------------------------------------------------

def test_the_front_page_renders_in_under_a_second():
    """Measured: 0.0s before, 10.0s after wiring revival_cards() in directly,
    0.04s after moving it behind the server. This is the screen `meditate`
    with no arguments prints; anything a person waits for on it has to earn
    the wait, and dormancy is the least urgent thing on the page."""
    status.status_text()                    # warm whatever caches exist
    t = time.time()
    status.status_text()
    took = time.time() - t
    assert took < 1.0, "the front page took %.1fs — something expensive got wired in" % took


def test_dormancy_NEVER_computes_locally_on_this_page():
    """The rule, not just the timing: a fast machine could pass the budget
    test while still walking every repo.

    Behavioural. The first version of this grepped status_text's source for
    "revival_cards" and failed on the COMMENT explaining why it must not be
    called — the same mistake that has now been made twice in this repo. Make
    the function explode instead: if the page still renders, it was not
    called.
    """
    import projects
    real = projects.revival_cards

    def boom(*a, **k):
        raise AssertionError("revival_cards was called from the front page")
    projects.revival_cards = boom
    try:
        page = status.status_text()
    finally:
        projects.revival_cards = real
    assert "What you're working on" in page or page.strip(), "the page did not render"


def test_no_server_means_no_dormant_line_and_no_wait():
    """not-reachable is not an error and never a delay. The line is simply
    absent, which is the same rule the rest of the tool follows about a
    question it cannot cheaply answer."""
    import urllib.request
    real = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError("refused"))
    try:
        t = time.time()
        page = status.status_text()
        took = time.time() - t
    finally:
        urllib.request.urlopen = real
    assert "Also sitting" not in page
    assert took < 1.0, "a dead server cost the page %.1fs" % took


def test_the_page_survives_a_HUNG_server():
    """A server that accepts the connection and never answers is the case a
    plain try/except does not cover. Proven against a real black-hole socket:
    0.47s, no line."""
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    threading.Thread(target=lambda: (srv.accept(), time.sleep(10)), daemon=True).start()
    real = status._dormant_from_server

    def probe(timeout_s=0.4):
        import json as _j
        import urllib.request
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/api/state" % port,
                                         headers={"X-Meditate": "1"})
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                return _j.load(r).get("dormant") or []
        except Exception:
            return []
    status._dormant_from_server = probe
    try:
        t = time.time()
        page = status.status_text()
        took = time.time() - t
    finally:
        status._dormant_from_server = real
        srv.close()
    assert "Also sitting" not in page
    assert took < 1.5, "a hung server cost the page %.1fs" % took


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
