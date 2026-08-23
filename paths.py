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
    d = describe()
    if "--json" in sys.argv:
        print(json.dumps({"tool_name": "meditate_paths", "success": True,
                          "data": d, "metadata": {}, "errors": []}, indent=2))
    else:
        for k, v in d.items():
            exists = "" if k == "nidra_root" or os.path.isdir(v) else "  (missing)"
            print("  %-15s %s%s" % (k, v.replace(HOME, "~"), exists))
