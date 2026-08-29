"""Tests for twin — the one entity that knows how the owner works.

The law under test is the same one the tree and the revival cards obey:
NOTHING SYNTHESISED. A personality profile invented by a language model is
astrology with a citation, so every twin line must be his own quoted
sentence, a counted number, or a live switch state — and every section must
name its basis.

The two defects its FIRST live output shipped are pinned here so they cannot
return: 11 fictional goals from test residue in the history ledger, and a
goal that NARROWED labelled "you widened it".

Run: python3 ~/.claude/skills/meditate/test_twin.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twin  # noqa: E402


def test_every_section_names_its_basis():
    """A line with no source is an invented line. The basis is the citation."""
    for s in twin.build():
        assert s.get("basis"), "%s has no basis" % s["title"]
        assert s.get("title") and isinstance(s.get("lines"), list)


def test_goal_history_excludes_TEST_RESIDUE():
    """First live output listed a, b, alpha, gamma, ship-widget — goals that
    exist only in the history ledger, written by suites before the
    MEDITATE_TESTING guard. A twin that reports fictional goals as yours is
    the exact synthesised-profile failure this module exists to refuse."""
    lines = " ".join(twin.how_goals_evolve()["lines"])
    for ghost in ("alpha", "gamma", "ship-widget", "fix-docs", "wrapped"):
        assert ghost not in lines, "%r is test residue reported as a real goal" % ghost


def test_a_narrowed_goal_is_not_called_widened():
    """scope -1 read "you widened it" in the first cut. Direction matters:
    widening is ambition, narrowing is a cut, and swapping them tells the
    owner the opposite of what he did."""
    import json, tempfile
    d = tempfile.mkdtemp()
    hist = os.path.join(d, "goals-history.jsonl")
    with open(hist, "w") as f:
        f.write(json.dumps({"name": "g", "done": 1, "total": 5, "ts": "t0"}) + "\n")
        f.write(json.dumps({"name": "g", "done": 1, "total": 3, "ts": "t1"}) + "\n")
    old_dir = twin.MEDITATION_DIR
    twin.MEDITATION_DIR = d
    try:
        import goals as gl
        real_scan = gl.scan
        gl.scan = lambda *a, **k: [{"name": "g"}]
        try:
            lines = " ".join(twin.how_goals_evolve()["lines"])
        finally:
            gl.scan = real_scan
    finally:
        twin.MEDITATION_DIR = old_dir
    assert "narrowed" in lines and "widened" not in lines, lines


def test_who_you_are_QUOTES_the_creed_not_a_rewrite():
    """Reuses advisor._creed — two derivations of "how he works" would drift
    invisibly until the twin and the mascot disagreed about him."""
    import advisor
    creed_first = [l.lstrip("- ").strip()
                   for l in advisor._creed().splitlines() if l.strip()][:3]
    mine = twin.who_you_are()["lines"]
    for want in creed_first:
        assert any(want.replace('\\"', '"') == m for m in mine), \
            "creed line missing or reworded: %r" % want[:60]


def test_decide_section_is_COUNTED_not_asserted():
    s = twin.how_you_decide()
    assert "recorded interactions" in s["basis"]
    joined = " ".join(s["lines"])
    assert any(ch.isdigit() for ch in joined), \
        "the decide section carries no number — that is a mood, not a finding"


def test_switch_state_says_what_it_CANNOT_tell():
    """Three-valued: LOADED / NOT LOADED / cannot tell. A switch report that
    can only say yes is the started:true lie again."""
    lines = " ".join(twin.switch_state()["lines"])
    assert "rounds timer" in lines and "auto-dispatch gate" in lines
    assert ("LOADED" in lines or "NOT LOADED" in lines or "cannot tell" in lines)


def test_render_is_plain_text_with_every_section():
    out = twin.render()
    for must in ("WHO YOU ARE", "HOW YOU DECIDE", "YOUR SCALE",
                 "YOUR GOALS", "DO BETTER", "THE SWITCH"):
        assert must in out, "%s missing from the rendered twin" % must



def test_a_STRANGERS_twin_tells_no_horoscope():
    """The product falsifier: run the twin as someone who is not the author.

    First run against a synthetic fresh HOME told four lies, every one a
    hand-written sentence asserting itself over an empty record: "you approve
    tersely" on 0 interactions, "you start wide and finish narrow" on 1
    product, a baked "live-probed ... answered YES" printed beside "hook
    MISSING", and "unmoved" for a goal with one snapshot.
    """
    import json, subprocess, tempfile, textwrap
    fake = tempfile.mkdtemp()
    proj = os.path.join(fake, ".claude", "projects", "-home-maya-shop")
    os.makedirs(proj)
    with open(os.path.join(proj, "aaa.jsonl"), "w") as f:
        f.write(json.dumps({"type": "user", "cwd": "/home/maya/shop",
                            "timestamp": "2026-08-20T10:00:00Z",
                            "message": {"role": "user", "content": "add a cart"}}) + "\n")
    gdir = os.path.join(fake, "claude-sync", "goals")
    os.makedirs(gdir)
    with open(os.path.join(gdir, "shop-live.md"), "w") as f:
        f.write(textwrap.dedent("""\
            ---
            name: shop-live
            title: Shop live
            project: shop
            cwd: /home/maya/shop
            status: active
            ---
            ## Milestones
            - [x] cart page
            - [ ] checkout works
            """))
    os.makedirs(os.path.join(fake, ".claude", "meditation"))
    env = dict(os.environ, HOME=fake, MEDITATE_TESTING="1")
    env.pop("MEDITATE_STORE_DIR", None)
    r = subprocess.run([sys.executable,
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "twin.py")],
                       env=env, capture_output=True, text=True, timeout=180)
    out = r.stdout
    assert r.returncode == 0, r.stderr[-300:]
    # no leakage of the author's own work into a stranger's profile
    for his in ("purangpt", "mila", "badenath"):
        assert his not in out.lower(), "%r leaked into a stranger's twin" % his
    # no rule asserted over an empty record
    assert "approve tersely" not in out, "author's voice rule spoken about a stranger"
    assert "start wide and finish narrow" not in out, "flavour asserted on 1 product"
    assert "answered YES" not in out, "the author's probe result baked into every machine"
    assert "unmoved across 1" not in out
    assert "nothing to compare yet" in out
    assert "fills in as you use the tool" in out


def test_on_the_REAL_record_the_claims_still_fire():
    """FALSIFIER for the gating: with 2,000+ interactions and 78 products the
    counted line and the flavour sentence must still appear — honesty about
    thin data must not mute a thick record."""
    d = twin.how_you_decide()
    if "recorded interactions" in d["basis"] and not d["basis"].startswith("0"):
        assert any(ch.isdigit() for ch in " ".join(d["lines"]))
    sc = " ".join(twin.your_scale()["lines"])
    import projects
    g = projects.assessment_gaps()
    if g.get("real_projects", 0) >= 10 and g.get("assessed", 0) * 3 < g["real_projects"]:
        assert "start wide" in sc, "the flavour line vanished from the record that earns it"


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
