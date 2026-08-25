"""The pages' JavaScript must PARSE. Nothing checked this, and it cost the
whole dashboard.

Pulse was serving HTTP 200 with every section blank for who knows how long.
The API was fine — 73 live sessions, 8 projects — and the page fetched
nothing, because one character in the inline script killed it:

    function esc(s){... {"&":"&amp;", ... , <THREE QUOTES> :"&quot;", ...} }

(the literal characters are not reproduced here: a docstring containing them
would break this file the same way, which is exactly the point)

The source is a Python triple-quoted string, and a backslash-escaped quote
inside one collapses to a bare quote before it ever reaches the browser. The result parses as Python,
renders as HTML, returns 200 — and the browser throws SyntaxError on load, so
not one line of the page's own code ever ran.

Every other test passed the whole time. They tested the SERVER. Nobody tested
the thing the server sends. A syntax check is the cheapest possible test and
it is the one that was missing.

Run: python3 ~/.claude/skills/meditate/test_page_js.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

import brain as br


def _scripts(html: str):
    return re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)


def _check(js: str, label: str):
    """Parse-check with node. Skips loudly rather than passing quietly."""
    node = shutil.which("node")
    if not node:
        print("    (node not installed — cannot parse-check %s)" % label)
        return
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
        path = f.name
    try:
        r = subprocess.run([node, "--check", path], capture_output=True,
                           text=True, timeout=30)
        assert r.returncode == 0, "%s does not parse:\n%s" % (label, r.stderr[:600])
    finally:
        os.unlink(path)


def test_pulse_page_javascript_parses():
    js = _scripts(br.PAGE)
    assert js, "the dashboard has no script at all"
    for i, s in enumerate(js):
        _check(s, "PAGE script %d" % i)


def test_no_stray_triple_quote_in_any_emitted_script():
    """The exact shape of the bug, in case node is ever unavailable."""
    for name, html in (("PAGE", br.PAGE),):
        for s in _scripts(html):
            assert '"""' not in s, f"{name} emits a stray triple quote"


def test_every_element_the_script_fills_exists_in_the_page():
    """A render target that isn't in the DOM is a section that stays blank."""
    for name, html in (("PAGE", br.PAGE),):
        ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
        wanted = set(re.findall(r'getElementById\(["\']([A-Za-z0-9_-]+)["\']', html))
        missing = wanted - ids
        assert not missing, f"{name} script writes to missing ids: {sorted(missing)}"


def test_the_goals_bar_tracks_a_dispatched_fleet():
    """When an agent is launched on a goal, its row must show it working —
    the join from s.fleet into the goals renderer, and a live badge."""
    assert "onGoal" in br.PAGE, "fleet is not joined into the goals bar"
    assert "agent on it" in br.PAGE, "no visible sign an agent is working"
    assert "dispatched_min" in br.PAGE, "how long it has been working is missing"


def test_the_page_leads_with_words_not_tables():
    """The hero paragraph renders from state.brief; the tables sit behind a
    fold. A dashboard you must read has failed at being an assistant."""
    assert 'id="brief"' in br.PAGE
    assert "<details" in br.PAGE and "THE NUMBERS" in br.PAGE
    assert br.PAGE.index('id="brief"') < br.PAGE.index("<details")


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
