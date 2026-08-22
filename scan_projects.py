#!/usr/bin/env python3
"""scan_projects — discover every project in a set of roots and report the
facts needed to write a continuation brief, as a JSON envelope.

This is the deterministic half of /renew: a pure parse-filter-reshape over
predictable filesystem + git output. The orchestrator (the skill) consumes only
`data` and never re-walks the trees itself.

Descriptor:
  { "tool_name": "scan_projects",
    "input_schema":  { "roots": ["path"], "max_depth": "int" },
    "output_schema": { "roots": ["path"], "count": "int",
                       "projects": [ { "name","path","kind","is_git","branch",
                                       "last_commit": {"hash","date","subject"},
                                       "dirty_files","ahead","behind",
                                       "languages": [["ext","count"]],
                                       "docs": ["..."], "markers": ["..."] } ] } }

Envelope: { "success", "data", "metadata", "errors" }
Human/CLI:  python3 scan_projects.py            -> short summary
Tool/JSON:  python3 scan_projects.py --json     -> envelope on stdout
"""
import argparse
import json
import os
import subprocess
import sys
import time

# Directories we never descend into (noise, build output, vendored deps).
PRUNE = {
    "node_modules", "venv", ".venv", "env", "__pycache__", ".git", ".next",
    "dist", "build", ".cache", ".turbo", ".pytest_cache", ".mypy_cache",
    "target", "vendor", "Pods", ".gradle", ".idea", ".vscode", "coverage",
    ".terraform", ".serverless", "site-packages", "DerivedData", ".expo",
    "Library", "Applications", ".Trash", ".npm", ".pnpm-store", "ComfyUI",
}

# Files whose presence marks a directory as a project worth a brief.
MARKERS = {
    ".git", "package.json", "pyproject.toml", "requirements.txt",
    "Cargo.toml", "go.mod", "CLAUDE.md", "Package.swift", "pom.xml",
    "build.gradle", "composer.json", "Gemfile",
}

# Docs that carry resumable context — reported so the brief can point at them.
DOC_FILES = [
    "CLAUDE.md", "AGENTS.md", "README.md", "PROJECT_CONTEXT.md", "ARCHITECTURE.md",
    "TODO.md", "task.md", "TASKS.md", "NOTES.md", "ROADMAP.md",
    "docker-compose.yml", "package.json", "requirements.txt", "pyproject.toml",
]

def _default_roots():
    """Where this USER's projects live — derived, never hardcoded.

    Two honest sources: the cwds Claude Code has actually run in (encoded in
    ~/.claude/projects slugs), and the common dev dirs that exist in THIS
    home. Env MEDITATE_PROJECT_ROOTS (colon-separated) overrides everything.
    """
    env = os.environ.get("MEDITATE_PROJECT_ROOTS")
    if env:
        return [p for p in env.split(":") if os.path.isdir(p)]
    home = os.path.expanduser("~")
    roots = set()
    # 1. parents of dirs Claude Code has actually opened
    proj_dir = os.path.expanduser("~/.claude/projects")
    if os.path.isdir(proj_dir):
        for slug in os.listdir(proj_dir):
            # slug "-Users-alice-code-foo" -> real path "/Users/alice/code/foo"
            if slug.startswith("-Users-") or slug.startswith("-home-"):
                # slug is lossy (spaces became dashes) so don't reconstruct deep
                # paths — just take the direct child of home it lives under.
                real = "/" + slug[1:].replace("-", "/")
                if real.startswith(home + "/"):
                    seg = real[len(home) + 1:].split("/")[0]
                    child = os.path.join(home, seg)
                    if seg and os.path.isdir(child):
                        roots.add(child)
    # 2. conventional dev dirs that exist in THIS home
    for name in ("projects", "code", "dev", "src", "work", "repos", "Documents"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            roots.add(p)
    return sorted(roots) or [home]


DEFAULT_ROOTS = _default_roots()


def _git(cwd, *args, timeout=5):
    """Run a git command in cwd; return stripped stdout or None on any failure."""
    try:
        r = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return None
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return None


def _git_facts(path):
    """Branch, last commit, dirty count, ahead/behind for a git repo."""
    facts = {
        "branch": None, "last_commit": None,
        "dirty_files": 0, "ahead": 0, "behind": 0,
    }
    facts["branch"] = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    log = _git(path, "log", "-1", "--format=%h\x1f%cs\x1f%s")
    if log and "\x1f" in log:
        h, date, subject = log.split("\x1f", 2)
        facts["last_commit"] = {"hash": h, "date": date, "subject": subject}
    status = _git(path, "status", "--porcelain")
    if status:
        facts["dirty_files"] = len([ln for ln in status.splitlines() if ln.strip()])
    ab = _git(path, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    if ab:
        parts = ab.split()
        if len(parts) == 2:
            facts["behind"], facts["ahead"] = _safe_int(parts[0]), _safe_int(parts[1])
    return facts


def _safe_int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def _languages_git(path, top=6):
    """Tally tracked-file extensions via `git ls-files` (fast, accurate)."""
    out = _git(path, "ls-files", timeout=10)
    if out is None:
        return []
    return _tally_exts(out.splitlines(), top)


def _languages_walk(path, top=6, max_files=4000):
    """Tally extensions by a bounded walk (for non-git projects)."""
    files = []
    for dp, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in PRUNE and not d.startswith(".")]
        files.extend(filenames)
        if len(files) >= max_files:
            break
    return _tally_exts(files, top)


def _tally_exts(paths, top):
    counts = {}
    for p in paths:
        base = os.path.basename(p)
        if "." not in base:
            continue
        ext = base.rsplit(".", 1)[1].lower()
        if len(ext) > 8 or not ext.isalnum():
            continue
        counts[ext] = counts.get(ext, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [[ext, n] for ext, n in ranked[:top]]


def _docs_present(path):
    return [d for d in DOC_FILES if os.path.isfile(os.path.join(path, d))]


def _markers_present(dirpath, dirnames, filenames):
    present = []
    names = set(dirnames) | set(filenames)
    for m in MARKERS:
        if m in names:
            present.append(m)
    return present


def _make_project(path, is_git, kind, markers):
    name = os.path.basename(os.path.normpath(path))
    proj = {
        "name": name,
        "path": path,
        "kind": kind,            # "repo" | "workspace"
        "is_git": is_git,
        "branch": None,
        "last_commit": None,
        "dirty_files": 0,
        "ahead": 0,
        "behind": 0,
        "languages": [],
        "docs": _docs_present(path),
        "markers": sorted(markers),
    }
    if is_git:
        proj.update(_git_facts(path))
        proj["languages"] = _languages_git(path)
    else:
        proj["languages"] = _languages_walk(path)
    return proj


def scan(roots, max_depth=4, scan_cap=40000):
    """Walk roots, detect projects, return the JSON envelope (dict)."""
    started = time.time()
    projects = []
    seen = set()
    errors = []
    scanned_dirs = 0

    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            errors.append({"code": "root_missing", "message": f"not a directory: {root}"})
            continue
        root_depth = root.rstrip(os.sep).count(os.sep)

        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            scanned_dirs += 1
            if scanned_dirs > scan_cap:
                errors.append({"code": "scan_cap", "message": f"hit scan cap {scan_cap}"})
                dirnames[:] = []
                continue

            depth = dirpath.rstrip(os.sep).count(os.sep) - root_depth
            if depth >= max_depth:
                dirnames[:] = []

            is_git = ".git" in dirnames or ".git" in filenames
            markers = _markers_present(dirpath, dirnames, filenames)

            if is_git:
                if dirpath not in seen:
                    seen.add(dirpath)
                    projects.append(_make_project(dirpath, True, "repo", markers))
                dirnames[:] = []  # a repo is one project; don't descend into it
                continue

            if markers and dirpath != root or (markers and dirpath == root):
                # marker-only dir = workspace folder; record but keep descending
                # so nested repos underneath are still found.
                if dirpath not in seen:
                    seen.add(dirpath)
                    projects.append(_make_project(dirpath, False, "workspace", markers))

            # prune for further descent
            dirnames[:] = [
                d for d in dirnames if d not in PRUNE and not d.startswith(".")
            ]

    projects.sort(key=lambda p: (p["kind"] != "repo", p["name"].lower()))
    elapsed_ms = int((time.time() - started) * 1000)

    return {
        "tool_name": "scan_projects",
        "success": True,
        "data": {
            "roots": [os.path.abspath(os.path.expanduser(r)) for r in roots],
            "count": len(projects),
            "projects": projects,
        },
        "metadata": {
            "scanned_dirs": scanned_dirs,
            "elapsed_ms": elapsed_ms,
            "max_depth": max_depth,
        },
        "errors": errors,
    }


def _human(env):
    d = env["data"]
    lines = [
        f"scanned {env['metadata']['scanned_dirs']} dirs in "
        f"{env['metadata']['elapsed_ms']}ms — {d['count']} projects",
        "",
    ]
    for p in d["projects"]:
        tag = "repo" if p["kind"] == "repo" else "wksp"
        git = ""
        if p["is_git"]:
            lc = p["last_commit"]
            commit = f"{lc['date']} {lc['subject'][:48]}" if lc else "no commits"
            dirty = f" *{p['dirty_files']}dirty" if p["dirty_files"] else ""
            git = f" [{p['branch']}]{dirty} — {commit}"
        langs = ",".join(e for e, _ in p["languages"][:3])
        lines.append(f"  ({tag}) {p['name']:<22} {langs:<14}{git}")
        lines.append(f"        {p['path']}")
    for e in env["errors"]:
        lines.append(f"  ! {e['code']}: {e['message']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Discover projects + facts for /renew")
    ap.add_argument("--root", action="append", dest="roots",
                    help="root to scan (repeatable); default: workspace roots")
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--json", action="store_true", help="emit JSON envelope")
    args = ap.parse_args(argv)

    roots = args.roots or DEFAULT_ROOTS
    env = scan(roots, max_depth=args.max_depth)

    if args.json:
        print(json.dumps(env, indent=2))
    else:
        print(_human(env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
