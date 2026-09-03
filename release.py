#!/usr/bin/env python3
"""Lift the meditate product out of the working checkout, verified.

The owner sells meditate on its own and works through the twin. Those are
two different artifacts out of one tree, and the measurement that shapes
this file is:

    twin closure  36 of 47 modules
    meditate      16
    SHARED        13

The twin stands on thirteen of meditate's sixteen modules. So "remove
meditate" cannot mean deleting it — deleting it kills the twin. What is
removable is meditate as a THING HE TYPES: the product ships from here, and
the console becomes the only front door.

This script GENERATES the shippable product. The working checkout stays the
single source; the product dir is never hand-edited, so the two cannot
drift. Every release re-walks the import graph, so a new import is picked up
instead of quietly missing from the tarball.

    python3 release.py --plan --json      what would ship, and what would not
    python3 release.py --build DEST       stage it and verify it in isolation
    python3 release.py --build DEST --publish --remote git@...   push it

Verification is not "the files copied". It is: every module imports in a
fresh interpreter that can see the staging dir and nothing else, AND
`import twin` from inside the product FAILS. The second one is what makes
the first mean anything — a copy of the whole skill dir passes the first.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

# The layer sets live in test_layers.py, which is the ratchet the owner's
# separation decision is enforced by. Retyping them here would give the repo
# two answers to "is brain companion?" and the drift would be silent.
import test_layers  # noqa: E402

# The product's front doors. Everything else it needs is walked from here —
# never listed by hand.
#
# nidra_bridge is deliberately NOT one. It is the grading pipe, and it needs
# the author's separate `nidra` package: on a clean HOME, ask.query() hit
# `except ImportError: return []` and reported NO RESULTS instead of NO
# ENGINE. Caught by running the published one-liner against an empty HOME —
# test_ask 3/6 and test_formation 6/9 red in a buyer's first minute.
#
# Vendoring a retriever would have kept the feature. Cutting it is the
# better answer: graded memory is the COMPANION's pitch, and it made the
# product's own claim — no dependency outside the standard library — false.
ENTRY = ["sessions", "still", "launch", "scan_projects", "archive",
         # not a front door — it carries the packaging rule (PERSONAL,
         # code_lines) that the shipped gate checks the other modules with.
         # It used to live in a test file, so a build without tests had no
         # rule at all and reported clean.
         "paths"]

# Files that are the product but are not python modules, copied as-is.
EXTRA = ["LICENSE", "VERSION"]

# The product's own shell, kept in product/ so it is a reviewed file and not
# a string inside this script. The repo's own `meditate`, `install.sh` and
# `get.sh` are the COMPANION's — they build Casper and start a local server,
# neither of which exists in the product — so they are not shipped.
PRODUCT_DIR = os.path.join(SKILL, "product")
PRODUCT_FILES = ["meditate", "install.sh", "uninstall.sh", "get.sh"]

# Where the product is published. The README's install line is the first
# command a buyer runs, and it used to point at the COMPANION's repo — it
# returned 200 and installed the wrong product, which is worse than 404.
PRODUCT_REPO = "prashantpandey-creator/meditate-sessions"

# Tests that guard the whole product rather than one module.
#   test_release.py  imports test_layers and release — neither ships.
#   test_packaging.py imports doctor, a companion module, and asserts the
#     working repo's README contract. It guards the CHECKOUT.
# test_product.py is the buyer's version of that gate and touches only
# shipped modules.
GUARD_TESTS = ["test_product.py"]


def _modules() -> Set[str]:
    return {f[:-3] for f in os.listdir(SKILL)
            if f.endswith(".py") and not f.startswith("test_")}


def _imports_of(mod: str, mods: Set[str], hard_only: bool = True) -> Set[str]:
    """Imports of `mod`, split by whether the module can start without them.

    HARD = at module level. The file will not import without it, so it must
    ship. SOFT = inside a function or a try/except — the code already treats
    the module as optional and degrades when it is missing.

    The distinction is not a convenience. Walking every import made the
    product drag in `inbox` and `beacon`, which are companion modules, and
    the split reported itself impossible. Both are function-local imports
    inside `try: ... except Exception: pass` — a core module ASKING whether
    the companion happens to be installed. That is the layering working, and
    a walker that cannot see it reads it as a violation.
    """
    try:
        t = ast.parse(open(os.path.join(SKILL, mod + ".py"),
                           errors="ignore").read())
    except Exception:
        return set()
    hard: Set[str] = set()
    soft: Set[str] = set()
    top = set()
    for node in ast.walk(t):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Try)):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    top.add(id(sub))
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            names = {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            names = {n.module.split(".")[0]}
        else:
            continue
        (soft if id(n) in top else hard).update(names)
    return ((hard if hard_only else hard | soft) & mods)


UPWARD = test_layers.COMPANION | test_layers.TWIN


def soft_deps(mods_in: Set[str]) -> Dict[str, List[str]]:
    """Companion modules the product ASKS for but does not need.

    Reported, never shipped. Silence about them would be the lie: the buyer
    would not know a feature exists that lights up when the companion does.
    """
    mods = _modules()
    out: Dict[str, List[str]] = {}
    for m in sorted(mods_in):
        opt = (_imports_of(m, mods, hard_only=False) - _imports_of(m, mods)
               ) - mods_in
        if opt:
            out[m] = sorted(opt)
    return out


def closure(seeds: List[str]) -> Set[str]:
    """Every module the product needs, by walking imports.

    Walked, not listed. A hand list is right on the day it is written and
    wrong the first time somebody adds an import — and the failure lands on
    a stranger's machine, not here.

    Soft (function-local, try-wrapped) imports are followed WITHIN the layer
    and refused ACROSS it. Both halves were learned by getting them wrong:

      - Following every import dragged `inbox` and `beacon` in and declared
        the split impossible. Those two are core asking whether the mascot
        happens to be installed — the layering working, not a violation.
      - Then following none of them cut the product from 16 modules to 7,
        losing commit-mining and the repair queue. They are lazy for import
        order, not because the feature is optional; a build without them
        ships a tool that silently does less, which is the exact failure
        this whole codebase is written against.
    """
    mods = _modules()
    seen, stack = set(seeds), list(seeds)
    while stack:
        m = stack.pop()
        deps = _imports_of(m, mods, hard_only=False) - UPWARD
        for dep in deps | _imports_of(m, mods):   # hard imports even if upward
            if dep not in seen:
                seen.add(dep); stack.append(dep)
    return seen


def plan() -> Dict[str, Any]:
    """What ships, and — the part that decides anything — what does not."""
    mods = _modules()
    prod = sorted(closure(ENTRY))
    excluded = sorted(mods - set(prod))
    leaked = sorted(set(prod) & (test_layers.COMPANION | test_layers.TWIN))
    lines = 0
    for m in prod:
        try:
            lines += len(open(os.path.join(SKILL, m + ".py"),
                              errors="ignore").readlines())
        except OSError:
            pass
    tests = [f for f in sorted(os.listdir(SKILL))
             if f.startswith("test_") and f[5:-3] in prod] + GUARD_TESTS
    return {"modules": prod, "excluded": excluded,
            "all_modules": len(mods), "lines": lines,
            "tests": sorted(set(t for t in tests
                                if os.path.exists(os.path.join(SKILL, t)))),
            "leaked": leaked,
            "optional": soft_deps(set(prod)),
            "entry": ENTRY}


def _py(args: List[str], cwd: str, path_only: str) -> subprocess.CompletedProcess:
    """A fresh interpreter that can see `path_only` and nothing else.

    Inheriting the caller's PYTHONPATH is how a staging dir 'passes' while
    silently importing the dev tree next door.
    """
    env = {"PATH": os.environ.get("PATH", ""),
           "HOME": os.environ.get("HOME", ""),
           "PYTHONPATH": path_only, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable] + args, cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=120)


def verify(dest: str) -> Dict[str, Any]:
    """Isolation, proven twice — once positive, once negative."""
    p = plan()
    imports = []
    for m in p["modules"]:
        r = _py(["-c", "import " + m], dest, dest)
        imports.append({"module": m, "ok": r.returncode == 0,
                        "err": (r.stderr.strip().splitlines() or [""])[-1]
                               if r.returncode else ""})
    # THE load-bearing check. Without it, `cp -R` of the whole skill dir
    # passes everything above.
    absent = []
    for gone in sorted(test_layers.COMPANION | test_layers.TWIN):
        r = _py(["-c", "import " + gone], dest, dest)
        absent.append({"module": gone, "absent": r.returncode != 0})
    isolated = all(a["absent"] for a in absent)
    # A doc that tells a buyer to run `meditate twin` is as broken as a
    # missing module — it just fails in their hands instead of here.
    doc_hits = []
    for f in ("SKILL.md", "README.md"):
        fp = os.path.join(dest, f)
        if not os.path.exists(fp):
            continue
        for i, line in enumerate(open(fp, errors="ignore").read().splitlines(), 1):
            if re.search(r"\bCLAUD-E\b|\bcasper\b|\bmascot\b|\btwin\b", line, re.I):
                doc_hits.append("%s:%d %s" % (f, i, line.strip()[:70]))
    return {"imports": imports, "isolated": isolated,
            "still_present": [a["module"] for a in absent if not a["absent"]],
            "docs_clean": not doc_hits, "doc_hits": doc_hits,
            "modules": len(p["modules"])}


def _skill_md() -> str:
    """SKILL.md with the paragraphs that name the companion removed.

    Not a silent edit: `verify()` greps the staged docs for every companion
    and twin name, so a new mention added upstream fails the build here
    rather than shipping a doc that tells a buyer to run software they do
    not have.
    """
    src = open(os.path.join(SKILL, "SKILL.md"), errors="ignore").read()
    out, drop = [], False
    for line in src.splitlines(True):
        if line.startswith("- ") or (line.strip() and not line.startswith(" ")):
            drop = bool(re.search(r"\btwin\b|\bCLAUD-E\b|\bcasper\b|\bmascot\b",
                                  line, re.I))
        if not drop:
            out.append(line)
    return "".join(out)


def _readme(p: Dict[str, Any]) -> str:
    """The buyer-facing page. States what it does and what it does not."""
    return """# meditate

**One long Claude Code session becomes a 100 MB tangle of unrelated work.
`meditate` reads it without loading it, finds the separate threads inside,
and hands you a paste-able prompt to resume any one of them.**

```sh
curl -fsSL https://raw.githubusercontent.com/%(repo)s/main/get.sh | sh
```

Then:

```sh
meditate sessions
```

```
329 sessions across 17 projects

  sprawl   126  117.1MB    83u  ch:0   [vedic-puran]  Game work elements resume
  sprawl    90   39.3MB   296u  ch:12  [vedic-puran]  chart engine + pricing
  sprawl    78   29.2MB   238u  ch:12  [vedic-puran]  reader latency
```

Sprawl is how many distinct threads are tangled in one session. The top row
is the one you keep scrolling through to find where you were.

## The commands

| | |
|---|---|
| `meditate sessions` | every session, ranked by how tangled it is |
| `meditate split <id>` | one session, broken into its threads |
| `meditate threads` | what is still open across everything you split |
| `meditate open` | a Terminal per live thread, cd'd and prompted (macOS) |
| `meditate archive` | what's finished and can be set down (dry run) |
| `meditate repo` | the optional repo lens |
| `meditate test` | run the suite |

## How it reads a 100 MB transcript

By streaming it. A transcript never enters a context window — there is no
model call anywhere in this tool, and no network call at all. Each session
comes back as a capped record: title, sprawl, where the topic changed, the
human intents with tool noise stripped, and the files it touched.

That is also why it is fast and why it costs nothing to run.

## What it will not do

It never moves, renames or deletes a transcript or a project file. Every
delete site in the source is enumerated in `test_product.py` and a new one
fails the suite. It writes to two places: your project's memory directory
and `~/.meditation/`. `archive` is a dry run unless you pass `--apply`, and
archiving is reversible.

`install.sh` links one command and installs one skill. No background
service, no launch agent, no permissions requested. `uninstall.sh` undoes
exactly that and leaves your readings alone.

## Verify it yourself before you trust it

```sh
meditate test
```

%(n)d modules, %(lines)d lines, no dependency outside the Python standard
library. Python 3.9+, macOS and Linux. Every module is checked to import
with nothing else on the path, and the suite ships with the code.

## Licence

MIT. Use it, fork it, ship it inside your own thing, at work or at home.
The repo is public so it can be found and shared; that is the point of it.
""" % {"repo": PRODUCT_REPO, "n": len(p["modules"]), "lines": p["lines"]}


def build(dest: str) -> Dict[str, Any]:
    """Stage the product and verify it. Never touches the source tree."""
    p = plan()
    if p["leaked"]:
        return {"ok": False, "why": "companion/twin modules in the closure: %s"
                                    % p["leaked"], "plan": p}
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    shipped = []
    for m in p["modules"]:
        shutil.copy2(os.path.join(SKILL, m + ".py"), dest)
        shipped.append(m + ".py")
    for f in p["tests"]:
        shutil.copy2(os.path.join(SKILL, f), dest); shipped.append(f)
    for f in EXTRA:
        src = os.path.join(SKILL, f)
        if os.path.exists(src):
            shutil.copy2(src, dest); shipped.append(f)
    for f in PRODUCT_FILES:
        src = os.path.join(PRODUCT_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, dest)
            os.chmod(os.path.join(dest, f), 0o755)
            shipped.append(f)
    open(os.path.join(dest, "SKILL.md"), "w").write(_skill_md())
    open(os.path.join(dest, "README.md"), "w").write(_readme(p))
    shipped += ["SKILL.md", "README.md"]
    v = verify(dest)
    ok = (v["isolated"] and v["docs_clean"]
          and all(i["ok"] for i in v["imports"]))
    why = ""
    if not ok:
        why = "; ".join(filter(None, [
            "companion still importable: %s" % v["still_present"]
            if not v["isolated"] else "",
            "docs name the companion: %s" % v["doc_hits"][:3]
            if not v["docs_clean"] else "",
            "failed imports: %s" % [i["module"] for i in v["imports"]
                                    if not i["ok"]]
            if not all(i["ok"] for i in v["imports"]) else ""]))
    return {"ok": ok, "dest": dest, "shipped": sorted(shipped),
            "plan": p, "verify": v, "why": why}


def publish(report: Dict[str, Any], remote: str, dry: bool = True,
            message: str = "release") -> Dict[str, Any]:
    """Push the staged product to its own repo.

    Refuses a red report. Shipping an unverified build is the one way this
    script can do real damage — a buyer gets a product that does not import.
    Dry by default: nothing outward-facing happens because a flag was
    forgotten.
    """
    if not report.get("ok"):
        return {"published": False,
                "why": "build did not verify: %s" % (report.get("why") or "?")}
    if not remote:
        return {"published": False, "why": "no remote given"}
    dest = report["dest"]
    steps = [["git", "init", "-q"],
             ["git", "add", "-A"],
             ["git", "-c", "user.email=release@meditate",
              "-c", "user.name=meditate release", "commit", "-q", "-m", message],
             ["git", "remote", "add", "origin", remote],
             ["git", "push", "-q", "--force", "origin", "HEAD:main"]]
    if dry:
        return {"published": False, "why": "dry run",
                "would": [" ".join(s) for s in steps], "dest": dest}
    done = []
    for s in steps:
        r = subprocess.run(s, cwd=dest, capture_output=True, text=True)
        done.append({"cmd": " ".join(s), "rc": r.returncode,
                     "err": r.stderr.strip()[-200:]})
        if r.returncode and s[1] not in ("remote", "init"):
            return {"published": False, "why": "%s failed" % s[1], "steps": done}
    return {"published": True, "remote": remote, "steps": done, "dest": dest}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--build", metavar="DEST")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--remote", default="")
    ap.add_argument("--message", default="release")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.build:
        data = build(os.path.abspath(os.path.expanduser(a.build)))
        if a.publish:
            data["publish"] = publish(data, a.remote, dry=False,
                                      message=a.message)
    else:
        data = plan()
    if a.json or a.plan:
        print(json.dumps({"ok": bool(data.get("ok", True)), "data": data},
                         indent=2 if not a.json else None))
        return 0 if data.get("ok", True) else 1
    p = data.get("plan", data)
    print("meditate product: %d of %d modules, %d lines"
          % (len(p["modules"]), p["all_modules"], p["lines"]))
    print("  ships:    " + ", ".join(p["modules"]))
    print("  left out: " + ", ".join(p["excluded"]))
    if "verify" in data:
        v = data["verify"]
        print("  verify:   %d/%d import in isolation; twin/companion absent: %s"
              % (sum(1 for i in v["imports"] if i["ok"]), len(v["imports"]),
                 v["isolated"]))
    return 0 if data.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
