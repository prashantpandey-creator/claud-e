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



def test_the_boot_sequence_is_the_SAME_derivation_not_theatre():
    """The visual cue must never lie: boot() returns exactly the sections
    build() would, each line landing only when its derivation finished, with
    the section's own basis on it. A spinner over a sleep would be astrology
    with an animation."""
    frames = []
    secs = twin.boot(write=frames.append)
    plain = twin.build()
    assert [x["title"] for x in secs] == [x["title"] for x in plain]
    out = "".join(frames)
    assert "CLAUD-E ONLINE" in out
    for x in secs:
        assert x["basis"][:30] in out, "a section landed without its basis: %s" % x["title"]


def test_piped_output_carries_NO_escape_codes():
    """Scripts, tests and pipes get plain text. An ANSI code in a pipe is how
    a pretty tool breaks every consumer downstream of it."""
    import subprocess
    r = subprocess.run([sys.executable,
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "twin.py")],
                       env=dict(os.environ, MEDITATE_TESTING="1"),
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0
    assert "\x1b" not in r.stdout, "ANSI escaped into piped output"
    assert "CLAUD-E" in r.stdout



# ---------------------------------------------------------------------------
# DEPENDABILITY — what actually breaks CLAUD-E, measured 2026-08-29
# ---------------------------------------------------------------------------

def test_the_console_works_with_NO_internet():
    """The console loaded three.js and d3 from a CDN and used them at the top
    level. With the CDN unreachable initCore threw on THREE.WebGLRenderer —
    and because it ran FIRST in a bare sequence, load() and feed() never ran
    at all: no face, no sections, no chart. One unreachable script tag took
    the entire twin.

    Proven by rendering the real page with the script tags stripped: face
    painted, 6 sections, 6 chart rows, 36 points, 9 buttons.
    """
    html = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "twin_console.html"), errors="ignore").read()
    assert "function guarded(" in html, "one failure can blank the page again"
    assert "guarded(\"core\", initCore); guarded(\"load\", load)" in html, \
        "the three entry points are no longer independently guarded"
    assert "initCore2D" in html and "drawChartPlain" in html, \
        "the library-free fallbacks are gone"
    assert 'typeof THREE === "undefined") return initCore2D' in html
    assert 'typeof d3 === "undefined") return drawChartPlain' in html


def test_stale_code_is_SAID_not_swallowed():
    """The server holds the code it booted with, so after an edit it serves
    confident numbers from a version that no longer exists — it happened
    twice in one afternoon while the page was read and believed. The flag was
    already in the payload and unused."""
    html = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "twin_console.html"), errors="ignore").read()
    assert "s.server_stale" in html and "running old code" in html


def test_a_CRASHED_brain_comes_back_by_itself():
    """Observed, not traced: SIGKILL to the server, launchd respawned it
    within 14s on fresh code.

    And KeepAlive stays crash-only ON PURPOSE. Flipping it to always-restart
    looked like more dependability and is the opposite: brain.py exits 0 when
    the port is already held ('already running at ...' — measured), so
    always-restart spins that clean exit into a respawn loop. Crash-only is
    correct because the ONLY exit-0 path is the port clash.
    """
    import plistlib
    pl = os.path.expanduser("~/Library/LaunchAgents/com.meditate.brain.plist")
    if not os.path.exists(pl):
        return
    d = plistlib.load(open(pl, "rb"))
    ka = d.get("KeepAlive")
    assert ka is not True, \
        "KeepAlive=always turns the port-clash exit into a respawn loop"
    assert isinstance(ka, dict) and ka.get("SuccessfulExit") is False, \
        "the brain is no longer restarted on a crash: %r" % (ka,)
    assert "brain.py" in " ".join(d.get("ProgramArguments") or []), \
        "com.meditate.brain does not run brain.py"



def test_arm_reports_the_CONDITION_not_the_exit_code():
    """The lie this pins, told on its first real run.

    `arm` set did = (returncode == 0). `cp` preserves the destination's mode,
    so copying the hook over a non-executable file exited 0, printed ARMED,
    and left the hook exactly as broken as before. An exit code is not an
    outcome; `did` means the condition is FALSE again, re-checked after
    acting.
    """
    import subprocess, tempfile
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "twin.py")).read()
    assert "row[\"did\"] = not check()" in src, \
        "arm is trusting an exit code again"
    assert "chmod +x" in src, "cp alone leaves the old mode"


def test_arm_DRY_by_default_and_changes_nothing():
    """A twin that arms itself the moment you look at it is not a switch, it
    is a surprise."""
    import inspect
    assert inspect.signature(twin.arm).parameters["dry"].default is True
    r = twin.arm()
    assert r["dry"] is True and r["done"] == 0


def test_arm_only_touches_REVERSIBLE_local_things():
    """It may load a launch agent that is already written, start the local
    server, and install the hook. It must never write rules.md, touch a repo,
    or push — those stay with him."""
    import ast, inspect
    # Scan the CODE, not the prose. The first cut grepped the whole function
    # and tripped on arm's own docstring saying it never pushes — the same
    # mistake a test of mine made earlier this week against a comment. Only
    # string LITERALS that could become a command are checked.
    tree = ast.parse(inspect.getsource(twin.arm).lstrip())
    lits = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    # Docstrings, dropped by SHAPE. Comparing against ast.get_docstring fails:
    # it dedents, while the raw Constant keeps its indentation, so the two
    # never match and the prose sails straight into the scan. A command has no
    # newline in it; prose does.
    lits = [l for l in lits if "\n" not in l]
    joined = " ".join(lits).lower()
    for forbidden in ("git ", "push", "rm -", "rules.md", "deploy"):
        assert forbidden not in joined, \
            "arm can run something irreversible: %r" % forbidden


def test_arm_on_a_healthy_machine_needs_nothing():
    """FALSIFIER: it must not invent work to do."""
    r = twin.arm(dry=True)
    for a in r["acts"]:
        assert isinstance(a["needed"], bool)
    assert r["done"] == 0


CONSOLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "twin_console.html")


def test_every_section_has_a_ONE_WORD_name_in_the_console():
    """Measured before the rewrite: 11,567 characters over 4.4 screens and
    seven headings averaging six words. The console folds each to one word
    and a mark, from a table keyed on the server's title PREFIX.

    The drift this pins: rename a section here and the console silently
    falls back to the long title, so the page grows a six-word heading back
    one at a time and nobody notices until it is 4 screens again."""
    import re
    html = open(CONSOLE, errors="ignore").read()
    block = html[html.index("const SEC = ["):html.index("];", html.index("const SEC = ["))]
    prefixes = re.findall(r'\["([^"]+)",\s*"([A-Z]+)"', block)
    assert prefixes, block[:200]
    titles = [s["title"] for s in twin.build()]
    unmapped = [t for t in titles
                if not any(t.startswith(p) for p, _ in prefixes)]
    assert not unmapped, ("section(s) with no short name — the console will "
                          "print the full heading: %s" % unmapped)
    for _, short in prefixes:
        assert " " not in short and len(short) <= 10, short


def test_every_short_name_still_matches_a_REAL_section():
    """The other direction. A stale entry is harmless on screen and lying in
    the file — it says a section exists that does not."""
    import re
    html = open(CONSOLE, errors="ignore").read()
    block = html[html.index("const SEC = ["):html.index("];", html.index("const SEC = ["))]
    prefixes = [p for p, _ in re.findall(r'\["([^"]+)",\s*"([A-Z]+)"', block)]
    titles = [s["title"] for s in twin.build()]
    stale = [p for p in prefixes if not any(t.startswith(p) for t in titles)]
    assert not stale, "SEC names sections that no longer exist: %s" % stale


def test_the_console_ships_a_MARK_for_every_short_name():
    """A heading with no glyph falls back to the RULES mark, so two
    different sections look identical at a glance — which is the opposite of
    what the marks are for."""
    import re
    html = open(CONSOLE, errors="ignore").read()
    block = html[html.index("const SEC = ["):html.index("];", html.index("const SEC = ["))]
    icons = re.findall(r'"[A-Z]+",\s*"(\w+)"', block)
    glyphs = set(re.findall(r'^\s*(\w+):\s*"M', html, re.M))
    missing = [i for i in icons if i not in glyphs]
    assert not missing, "no glyph for: %s" % missing


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
