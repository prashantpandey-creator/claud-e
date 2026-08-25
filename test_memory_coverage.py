"""Tests for paths.memory_coverage / paths.link_memory — who starts BLIND.

WHY (measured 2026-08-25 on the real machine):

A session gets memories from ~/.claude/projects/<cwd-slug>/memory. Work in a
cwd that has no such dir and you start with nothing — silently. Nobody had
ever counted it:

    139 sessions  projects/vedic puran        264 memories
     45 sessions  .claude/skills/meditate       0 memories   <-- blind
     11 sessions  projects/mila-english        21 memories

164 of 228 transcripts (72%) ran somewhere with memories. The other 28%
started cold, and the worst case is the tool's own repo — second-most-worked
directory on the machine, forty-five sessions, zero memory, because all the
meditate knowledge was written from the vedic-puran cwd where the work
actually happens.

The mechanical part of the fix generalises; the judgement does not. A cwd
that is a SUB-PATH of a project that already has memories should share that
project's dir — that is what the four PuranGPT cwds already do by symlink,
and it needs no opinion. A cwd that is nobody's sub-path is a new project and
the tool must NOT guess which memories it should inherit.

Two guards, both learned from the real data rather than imagined:
  - the home directory is not a "covering project". Every path on the machine
    is under ~/, so accepting it made the rule answer
    ".claude/skills/meditate -> -Users-badenath" (3 memories) instead of
    admitting it did not know.
  - temp dirs are not projects. /tmp and /var/folders scratch cwds are not
    worth a memory dir and must never be reported as owed one.

Run: python3 ~/.claude/skills/meditate/test_memory_coverage.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402


def _projects(tmp, spec):
    """spec: {slug: (n_sessions, n_memories)} -> a fake ~/.claude/projects."""
    root = os.path.join(tmp, "projects")
    for slug, (sessions, mems) in spec.items():
        d = os.path.join(root, slug)
        os.makedirs(d, exist_ok=True)
        for i in range(sessions):
            open(os.path.join(d, "s%d.jsonl" % i), "w").close()
        if mems:
            md = os.path.join(d, "memory")
            os.makedirs(md, exist_ok=True)
            for i in range(mems):
                open(os.path.join(md, "m%d.md" % i), "w").close()
    return root


HOME_SLUG = "-Users-someone"


# ---------------------------------------------------------------------------

def test_a_covered_project_is_not_reported():
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-Users-someone-proj-a": (5, 10)})
        assert paths.memory_coverage(root, HOME_SLUG)["blind"] == []


def test_a_blind_project_is_reported_with_its_session_count():
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-Users-someone-proj-a": (5, 10),
                               "-Users-someone-proj-b": (7, 0)})
        blind = paths.memory_coverage(root, HOME_SLUG)["blind"]
        assert len(blind) == 1, blind
        assert blind[0]["slug"] == "-Users-someone-proj-b"
        assert blind[0]["sessions"] == 7
        assert blind[0]["link_to"] is None, "unrelated project must not be auto-linked"


def test_a_subpath_is_auto_linkable_to_its_parent():
    """The PuranGPT case: a worktree under a project that has memories."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-Users-someone-proj-a": (5, 10),
                               "-Users-someone-proj-a-worktree": (2, 0)})
        blind = paths.memory_coverage(root, HOME_SLUG)["blind"]
        assert len(blind) == 1
        assert blind[0]["link_to"] == "-Users-someone-proj-a"


def test_longest_covering_parent_wins():
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-Users-someone-a": (1, 3),
                               "-Users-someone-a-b": (1, 3),
                               "-Users-someone-a-b-c": (1, 0)})
        blind = paths.memory_coverage(root, HOME_SLUG)["blind"]
        assert blind[0]["link_to"] == "-Users-someone-a-b", blind


def test_HOME_is_never_a_covering_project():
    """The guard that matters. Every path is under ~/.

    Without it the rule answered ".claude/skills/meditate -> -Users-someone"
    — technically containment, meaninglessly so, and it would have hidden a
    real 45-session blind spot behind a confident wrong answer.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {HOME_SLUG: (2, 3),
                               HOME_SLUG + "--claude-skills-meditate": (45, 0)})
        blind = paths.memory_coverage(root, HOME_SLUG)["blind"]
        assert len(blind) == 1
        assert blind[0]["link_to"] is None, "home was accepted as a parent project"
        assert blind[0]["sessions"] == 45


def test_prefix_must_end_on_a_separator():
    """'-proj-abc' is not inside '-proj-ab'."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-Users-someone-proj-ab": (1, 4),
                               "-Users-someone-proj-abc": (1, 0)})
        blind = paths.memory_coverage(root, HOME_SLUG)["blind"]
        assert blind[0]["link_to"] is None, "string prefix matched across a path boundary"


def test_temp_dirs_are_not_projects():
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-private-tmp": (2, 0), "-private-tmp-hproj": (6, 0),
                               "-private-var-folders-4d-xyz": (1, 0), "-tmp-scratch": (3, 0)})
        assert paths.memory_coverage(root, HOME_SLUG)["blind"] == []


def test_a_cwd_with_no_sessions_is_not_owed_anything():
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-Users-someone-never-opened": (0, 0)})
        assert paths.memory_coverage(root, HOME_SLUG)["blind"] == []


def test_an_empty_memory_dir_counts_as_blind():
    """The real shape: the dir exists, nothing is in it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-Users-someone-p": (4, 0)})
        os.makedirs(os.path.join(root, "-Users-someone-p", "memory"))
        assert len(paths.memory_coverage(root, HOME_SLUG)["blind"]) == 1


def test_coverage_reports_the_totals():
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-Users-someone-a": (10, 5), "-Users-someone-b": (5, 0)})
        c = paths.memory_coverage(root, HOME_SLUG)
        assert c["sessions_total"] == 15 and c["sessions_covered"] == 10


def test_missing_root_is_empty_not_an_error():
    assert paths.memory_coverage("/nonexistent/xyz", HOME_SLUG)["blind"] == []


# ---- link_memory ----------------------------------------------------------

def test_link_memory_creates_the_symlink():
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-Users-someone-a": (1, 3), "-Users-someone-a-wt": (1, 0)})
        r = paths.link_memory("-Users-someone-a-wt", "-Users-someone-a", root)
        assert r["linked"] is True, r
        link = os.path.join(root, "-Users-someone-a-wt", "memory")
        assert os.path.islink(link)
        assert len(os.listdir(link)) == 3, "the linked dir must see the parent's memories"


def test_link_memory_REFUSES_to_replace_a_non_empty_dir():
    """The falsifier. Never destroy memories to make a link."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-Users-someone-a": (1, 3), "-Users-someone-b": (1, 2)})
        r = paths.link_memory("-Users-someone-b", "-Users-someone-a", root)
        assert r["linked"] is False and "not empty" in r["reason"], r
        assert len(os.listdir(os.path.join(root, "-Users-someone-b", "memory"))) == 2


def test_link_memory_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-Users-someone-a": (1, 3), "-Users-someone-a-wt": (1, 0)})
        paths.link_memory("-Users-someone-a-wt", "-Users-someone-a", root)
        r = paths.link_memory("-Users-someone-a-wt", "-Users-someone-a", root)
        assert r["linked"] is False and "already" in r["reason"], r


def test_link_memory_refuses_a_target_with_no_memories():
    with tempfile.TemporaryDirectory() as tmp:
        root = _projects(tmp, {"-Users-someone-a": (1, 0), "-Users-someone-a-wt": (1, 0)})
        r = paths.link_memory("-Users-someone-a-wt", "-Users-someone-a", root)
        assert r["linked"] is False, r


def test_live_machine_reports_without_crashing():
    c = paths.memory_coverage()
    assert isinstance(c["blind"], list)
    print("       live: %d/%d sessions covered, %d blind cwd(s), %d auto-linkable"
          % (c["sessions_covered"], c["sessions_total"], len(c["blind"]),
             sum(1 for b in c["blind"] if b["link_to"])))


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
