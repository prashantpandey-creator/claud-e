"""paths — where everything lives, decided ONCE.

Packaging defect this fixes: the tool only ran on the author's machine.
Five modules hardcoded his layout —

    NIDRA_ROOT = ~/projects/nidra          (his checkout)
    MEMORY_ROOT = ~/claude-sync/memory     (his personal sync folder)
    MEMORY_DIR  = .../-Users-badenath-projects-vedic-puran
    goals_dir   = ~/claude-sync/goals
    slug default = "-Users-badenath-projects-vedic-puran"

— so on any other machine nidra failed to import, memory graded nothing, and
goals came up empty. Proved on a clean HOME before the fix:
    {"success": false, "errors": [{"code": "import",
                                   "message": "No module named 'nidra'"}]}

Resolution order, same for every location:

    1. an explicit environment variable          (deliberate override)
    2. a path recorded by install.sh             (this machine's answer)
    3. a conventional location that EXISTS       (keeps an existing setup
                                                  working, unchanged)
    4. a default inside ~/.claude/meditation     (always writable, always
                                                  correct on a fresh machine)

Step 3 before step 4 is deliberate: an existing install must not silently
move its data because the tool learned to package itself.
"""
from __future__ import annotations

import os
from typing import List, Optional

HOME = os.path.expanduser("~")

# ---------------------------------------------------------------------------
# What "the author's machine" looks like, so shipped code can be checked
# against it. This lived in test_packaging.py, and coordination.py's live
# red-squiggle check imported it FROM THE TEST FILE — so in any build that
# ships code without tests the check silently returned [] and reported
# nothing. An absent check that renders as a passing one.
#
# It belongs here because deciding where things live is this module's whole
# job, and PERSONAL is the negative of that: the places nothing may hardcode.
# ---------------------------------------------------------------------------
PERSONAL = [
    r"/Users/[a-z]+/",                   # anybody's absolute home
    r"\bbadenath\b",
    r"projects/nidra",
    r"expanduser\([\"']~/claude-sync",   # DEPENDING on the sync folder
]
# "claude-sync" in a blocklist of directory names to ignore is a heuristic,
# not a dependency — it costs nothing to a user with no such folder. Only
# resolving PATHS from it is the packaging defect. paths.py is the ONE file
# allowed to name conventional locations, because naming them is its job;
# docstrings elsewhere may quote the history.
EXEMPT_FILES = {"paths.py", "test_packaging.py"}


def code_lines(path: str, root: Optional[str] = None) -> List[str]:
    """Source lines with comments and docstrings stripped, roughly — enough
    to tell 'this module DEPENDS on the path' from 'this module MENTIONS
    it'."""
    out: List[str] = []
    in_doc = False
    delim = ""
    full = path if os.path.isabs(path) else os.path.join(
        root or os.path.dirname(os.path.abspath(__file__)), path)
    for line in open(full, encoding="utf-8", errors="replace"):
        stripped = line.strip()
        if in_doc:
            if delim in stripped:
                in_doc = False
            continue
        if stripped.startswith(('"""', "'''")):
            delim = stripped[:3]
            if not (stripped.endswith(delim) and len(stripped) > 3):
                in_doc = True
            continue
        code = line.split("#", 1)[0]
        if code.strip():
            out.append(code)
    return out
MEDITATION_DIR = os.environ.get("MEDITATE_HOME") or os.path.join(
    HOME, ".claude", "meditation")


def _first_existing(candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c and os.path.isdir(os.path.expanduser(c)):
            return os.path.expanduser(c)
    return None


def _recorded(name: str) -> Optional[str]:
    """A path install.sh worked out for this machine and wrote down."""
    try:
        with open(os.path.join(MEDITATION_DIR, name)) as f:
            p = f.read().strip()
        return p if p and os.path.isdir(p) else None
    except OSError:
        return None


def _resolve(env_var: str, record: str, conventional: List[str],
             default: str) -> str:
    p = os.environ.get(env_var)
    if p:
        return os.path.expanduser(p)
    p = _recorded(record)
    if p:
        return p
    p = _first_existing(conventional)
    if p:
        return p
    return default


def memory_root() -> str:
    """Where the markdown memories live, before grading."""
    return _resolve(
        "MEDITATE_MEMORY_ROOT", "memory-path",
        ["~/claude-sync/memory", "~/.claude/memory"],
        os.path.join(MEDITATION_DIR, "memory"))


def goals_dir() -> str:
    """One .md per goal."""
    return _resolve(
        "MEDITATE_GOALS_DIR", "goals-path",
        ["~/claude-sync/goals", "~/.claude/goals"],
        os.path.join(MEDITATION_DIR, "goals"))


def store_dir() -> str:
    """The graded store. Always ours — no conventional alternative."""
    return os.environ.get("MEDITATE_STORE_DIR") or os.path.join(
        MEDITATION_DIR, "nidra_store")


def nidra_root() -> Optional[str]:
    """A nidra CHECKOUT to put on sys.path, or None.

    None is a normal answer, not a failure: when nidra is pip-installed the
    import works with no path help at all. Callers must try the plain import
    either way.
    """
    p = os.environ.get("MEDITATE_NIDRA_ROOT")
    if p and os.path.isdir(os.path.expanduser(p)):
        return os.path.expanduser(p)
    p = _recorded("nidra-path")
    if p:
        return p
    return _first_existing([
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "nidra"),
        "~/projects/nidra", "~/nidra", "~/src/nidra", "~/code/nidra",
    ])


def add_nidra_to_path() -> Optional[str]:
    """Make `import nidra` work if it can be made to work. Returns the path
    added, or None when nidra is already importable (or absent)."""
    root = nidra_root()
    if root and root not in os.sys.path:
        os.sys.path.insert(0, root)
        return root
    return None


def project_slug(cwd: Optional[str] = None) -> str:
    """Claude Code's directory slug for a working directory.

    This was hardcoded to the author's own project as a DEFAULT VALUE, so
    every machine that failed to detect a slug silently adopted his.
    """
    p = os.path.abspath(cwd or os.getcwd())
    return p.replace("/", "-")


CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")

# A cwd under one of these is scratch, not a project, and is never owed a
# memory dir. Measured: 9 of 228 transcripts on this machine ran in /tmp or a
# /var/folders sandbox. Reporting those as "blind projects" would be noise,
# and a health check nobody reads is a health check that does nothing.
_SCRATCH_PREFIXES = ("-private-tmp", "-tmp", "-private-var-folders", "-var-folders")


def _slug_children(slug: str, parent: str) -> bool:
    """Is `slug` a sub-path of `parent`, in slug space?

    The separator check is load-bearing: a bare startswith() makes
    "-proj-abc" a child of "-proj-ab", which is a different directory.
    """
    return slug.startswith(parent + "-")


def memory_coverage(projects_root: Optional[str] = None,
                    home_slug: Optional[str] = None) -> dict:
    """Which cwds you actually work in start with NO memory.

    A session reads memories from ~/.claude/projects/<cwd-slug>/memory. Work
    somewhere without one and you start cold — silently, with nothing saying
    so. Measured on the author's machine 2026-08-25: 164 of 228 transcripts
    (72%) ran in a covered cwd; the worst gap was the tool's own repo, 45
    sessions and zero memories, because every meditate memory had been
    written from the vedic-puran cwd where the work actually happens.

    A blind cwd that is a SUB-PATH of a covered project gets `link_to` set —
    that is mechanical, and exactly what the four PuranGPT cwds already do by
    symlink. Anything else gets link_to=None: it is a new project, and which
    memories it should inherit is a judgement the tool must not fake.

    HOME IS NOT A PARENT. Every path is under ~/, so allowing it made this
    answer ".claude/skills/meditate -> ~" and hide a real 45-session gap
    behind a confident wrong link.
    """
    root = projects_root or CLAUDE_PROJECTS
    home_slug = home_slug if home_slug is not None else project_slug(os.path.expanduser("~"))
    covered, blind, total, cov_sessions = [], [], 0, 0
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return {"blind": [], "sessions_total": 0, "sessions_covered": 0}
    for slug in entries:
        d = os.path.join(root, slug)
        if not os.path.isdir(d):
            continue
        try:
            sessions = sum(1 for f in os.listdir(d) if f.endswith(".jsonl"))
            mem = os.path.join(d, "memory")
            mems = sum(1 for f in os.listdir(mem) if f.endswith(".md")) \
                if os.path.isdir(mem) else 0
        except OSError:
            continue
        if not sessions:
            continue                      # never opened; owed nothing
        total += sessions
        if mems:
            covered.append(slug); cov_sessions += sessions
        elif not slug.startswith(_SCRATCH_PREFIXES):
            blind.append({"slug": slug, "sessions": sessions})
    for b in blind:
        parents = [c for c in covered
                   if c != home_slug and _slug_children(b["slug"], c)]
        parents.sort(key=len, reverse=True)     # nearest enclosing project
        b["link_to"] = parents[0] if parents else None
    blind.sort(key=lambda b: -b["sessions"])
    return {"blind": blind, "sessions_total": total, "sessions_covered": cov_sessions}


def link_memory(slug: str, target_slug: str,
                projects_root: Optional[str] = None) -> dict:
    """Point one cwd's memory dir at another's. Never destroys memories.

    Refuses when the dir already holds .md files — making a link is not worth
    losing a memory over, and "it was empty anyway" is a claim to CHECK, not
    to assume.
    """
    root = projects_root or CLAUDE_PROJECTS
    src = os.path.join(root, slug, "memory")
    dst = os.path.join(root, target_slug, "memory")
    dst = os.path.realpath(dst) if os.path.exists(dst) else dst
    if not os.path.isdir(dst) or not any(f.endswith(".md") for f in os.listdir(dst)):
        return {"linked": False, "reason": "target has no memories: %s" % target_slug}
    if os.path.islink(src):
        return {"linked": False, "reason": "already a link -> %s" % os.readlink(src)}
    if os.path.isdir(src):
        if any(f.endswith(".md") for f in os.listdir(src)):
            return {"linked": False, "reason": "not empty — refusing to replace real memories"}
        try:
            os.rmdir(src)
        except OSError as e:
            return {"linked": False, "reason": "could not remove empty dir: %s" % e}
    try:
        os.makedirs(os.path.dirname(src), exist_ok=True)
        os.symlink(dst, src)
    except OSError as e:
        return {"linked": False, "reason": str(e)}
    return {"linked": True, "slug": slug, "target": dst}


def describe() -> dict:
    """Every resolved location, for doctor and for `meditate where`."""
    return {
        "meditation_dir": MEDITATION_DIR,
        "memory_root": memory_root(),
        "goals_dir": goals_dir(),
        "store_dir": store_dir(),
        "nidra_root": nidra_root() or "(pip-installed or absent)",
    }


if __name__ == "__main__":
    import json
    import sys

    # --link-memory APPLIES only the mechanical links: a cwd that is a
    # sub-path of a project which already has memories. Anything else is left
    # alone and printed, because inheriting another project's memories is a
    # judgement and the tool guessing it would be worse than the gap.
    if "--link-memory" in sys.argv:
        cov = memory_coverage()
        auto = [b for b in cov["blind"] if b["link_to"]]
        for b in auto:
            r = link_memory(b["slug"], b["link_to"])
            print("  %s  %s -> %s%s" % (
                "linked " if r["linked"] else "skipped",
                b["slug"].replace(HOME.replace("/", "-"), "~"), b["link_to"][-40:],
                "" if r["linked"] else "  (%s)" % r["reason"]))
        for b in cov["blind"]:
            if not b["link_to"]:
                print("  DECIDE   %s — %d sessions, no covering project. Pick a "
                      "memory dir for it or accept that it starts cold."
                      % (b["slug"], b["sessions"]))
        sys.exit(0)

    d = describe()
    cov = memory_coverage()
    if "--json" in sys.argv:
        # coverage goes in METADATA, not data. test_packaging asserts every
        # value in `data` is a path string under the sandbox home — that is
        # the guard which catches the author's own directories escaping into
        # a fresh install, and putting a dict there broke it. The guard was
        # right; this envelope was wrong.
        print(json.dumps({"tool_name": "meditate_paths", "success": True,
                          "data": d, "metadata": {"coverage": cov},
                          "errors": []}, indent=2))
    else:
        for k, v in d.items():
            exists = "" if k == "nidra_root" or os.path.isdir(v) else "  (missing)"
            print("  %-15s %s%s" % (k, v.replace(HOME, "~"), exists))
        if cov["sessions_total"]:
            print("  %-15s %d/%d sessions in a cwd WITH memories; %d blind cwd(s)%s"
                  % ("coverage", cov["sessions_covered"], cov["sessions_total"],
                     len(cov["blind"]),
                     " — fix with --link-memory" if any(b["link_to"] for b in cov["blind"]) else ""))
