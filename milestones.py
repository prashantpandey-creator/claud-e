"""milestones — check whether an open milestone is already true.

Milestones are ticked by hand, so the ledger drifts away from the world and
the console keeps asking for work that is already done. Caught live:

    - [ ] stilling pass run (/meditate — STILLNESS.md 7+ days overdue)

STILLNESS.md was 1.5 HOURS old. The condition was met, the box was empty, and
"needs you: stilling pass run" was on screen telling the owner to do it again.

The tool already holds the evidence for a handful of these — the stillness
clock, the drift counters, the repos. So check them, and say which look done.

It does NOT tick the box. A ledger that edits itself is a ledger you can no
longer trust to disagree with the tool, and the owner's standing rule is that
the tool surfaces and the human triggers. `looks_done` is a claim WITH its
evidence, for a person to accept.

Anything it cannot decide comes back `unknown`, which is the honest answer for
"App Store privacy labels submitted".

    python3 milestones.py            # what looks done, and why
    python3 milestones.py --json
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

STILL_FRESH_DAYS = 3.0

# Status baked into milestone TEXT goes stale the moment the world moves.
# "(currently WAITING)" and "(STILLNESS.md 7+ days overdue)" are both claims
# about now, frozen in a file, inside a line describing a condition.
_EMBEDDED_STATUS = re.compile(
    r"\((?:[^)]*\b(?:currently|as of|still|now|today|overdue|"
    r"WAITING|DEAD|BLOCKED|FAILED|\d+\s*\+?\s*days?)\b[^)]*)\)", re.I)


def _facts() -> Dict[str, Any]:
    """The live world, gathered once and handed to every checker."""
    f: Dict[str, Any] = {}
    try:
        import report as rp
        r = rp.compute()
        f["stillness_age_days"] = (r.get("stilling") or {}).get("stillness_age_days")
        f["repaired"] = (r.get("drift") or {}).get("repaired", 0)
        f["caught"] = (r.get("drift") or {}).get("caught", 0)
    except Exception:
        pass
    return f


def _git(cwd: str, *args: str) -> str:
    if not cwd or not os.path.isdir(cwd):
        return ""
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                           text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


# ---- checkers ---------------------------------------------------------------
# Each returns (verdict, evidence) where verdict is True / False / None.
# None means "this tool cannot decide", which is most of them, and saying so
# is the point — a checker that guesses is worse than no checker.

def _check_stilling(text: str, goal: Dict[str, Any], f: Dict[str, Any]):
    if not re.search(r"\bstilling\b|\bSTILLNESS\b|/meditate\b", text, re.I):
        return None, ""
    age = f.get("stillness_age_days")
    if age is None:
        return None, ""
    if age < STILL_FRESH_DAYS:
        return True, "STILLNESS.md was written %.1f days ago" % age
    return False, "STILLNESS.md is %.1f days old" % age


def _check_repaired(text: str, goal: Dict[str, Any], f: Dict[str, Any]):
    # "repaired: 0 -> 1" states the CURRENT value then the target. Reading the
    # first number made the goal its own starting point — the checker declared
    # "repaired >= 0" satisfied and called an untouched milestone done.
    arrow = re.search(r"repaired[^0-9]{0,12}(\d+)\s*(?:->|\u2192|to)\s*(\d+)",
                      text, re.I)
    m = re.search(r"repaired\s*(?:>=|:)\s*(\d+)", text, re.I)
    if not arrow and not m and not re.search(r"\brepair loop closed\b", text, re.I):
        return None, ""
    want = int(arrow.group(2)) if arrow else (int(m.group(1)) if m else 1)
    want = max(want, 1)
    have = f.get("repaired")
    if have is None:
        return None, ""
    return have >= want, "meditate report shows repaired=%d (needs %d)" % (have, want)


def _check_license(text: str, goal: Dict[str, Any], f: Dict[str, Any]):
    if "LICENSE" not in text:
        return None, ""
    cwd = goal.get("cwd") or ""
    if not cwd or not os.path.isdir(cwd):
        return None, ""
    have = os.path.exists(os.path.join(cwd, "LICENSE"))
    return (None if not have else True), (
        "LICENSE exists in %s" % os.path.basename(cwd) if have else "")


def _check_tags(text: str, goal: Dict[str, Any], f: Dict[str, Any]):
    if not re.search(r"\b(release tags?|versioned|tagged)\b", text, re.I):
        return None, ""
    cwd = goal.get("cwd") or ""
    tags = [t for t in _git(cwd, "tag").splitlines() if t.strip()]
    if not cwd or not os.path.isdir(cwd):
        return None, ""
    if tags:
        return True, "%d tag(s) in %s, latest %s" % (
            len(tags), os.path.basename(cwd), tags[-1])
    return False, "no tags in %s" % os.path.basename(cwd)


def _check_pushed(text: str, goal: Dict[str, Any], f: Dict[str, Any]):
    # "push" as a NOUN is usually somebody else's subject: "full suite green
    # on push (macOS runner)" is about CI, and matching it reported an unbuilt
    # pipeline as done because the repo happened to have nothing unpushed.
    # Only the past participle describes a state this checker can verify.
    if not re.search(r"\bpushed\b", text, re.I):
        return None, ""
    if re.search(r"\b(CI|workflow|runner|github action|on push)\b", text, re.I):
        return None, ""
    cwd = goal.get("cwd") or ""
    if not cwd or not os.path.isdir(cwd):
        return None, ""
    ahead = _git(cwd, "rev-list", "--count", "@{u}..HEAD")
    if ahead == "":
        return None, ""
    n = int(ahead or 0)
    if n == 0:
        return True, "%s has nothing unpushed" % os.path.basename(cwd)
    return False, "%s is %d commit(s) ahead of its remote" % (
        os.path.basename(cwd), n)


CHECKERS: List[Callable] = [_check_stilling, _check_repaired, _check_license,
                            _check_tags, _check_pushed]


_CONJUNCTION = re.compile(r"\s\+\s|\band\b|\bboth\b|\s&\s|,\s+then\b", re.I)


def has_multiple_conditions(text: str) -> bool:
    """Does this milestone ask for more than one thing?"""
    return bool(_CONJUNCTION.search(text or ""))


def check_milestone(text: str, goal: Dict[str, Any],
                    f: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One milestone against the world. verdict True / False / None.

    CLAIM SCOPE = CHECK SCOPE. "LICENSE + versioned release tags on both repos"
    has four conditions, and a checker that found a LICENSE file reported the
    whole line done. A single checker may therefore never pass a milestone with
    more than one condition — it can still FAIL it, because one unmet condition
    is enough to keep a milestone open.
    """
    f = _facts() if f is None else f
    multi = has_multiple_conditions(text)
    for c in CHECKERS:
        try:
            verdict, evidence = c(text, goal, f)
        except Exception:
            verdict, evidence = None, ""
        if verdict is False:
            return {"verdict": False, "evidence": evidence,
                    "checker": c.__name__.replace("_check_", "")}
        if verdict is True:
            if multi:
                return {"verdict": None,
                        "evidence": "%s — but this milestone asks for more "
                                    "than one thing" % evidence,
                        "checker": c.__name__.replace("_check_", "") + "/partial"}
            return {"verdict": True, "evidence": evidence,
                    "checker": c.__name__.replace("_check_", "")}
    return {"verdict": None, "evidence": "", "checker": None}


def stale_wording(text: str) -> str:
    """A status claim frozen inside a condition. Returns the offending phrase."""
    m = _EMBEDDED_STATUS.search(text or "")
    return m.group(0) if m else ""


def audit(goals_dir: Optional[str] = None) -> Dict[str, Any]:
    """Every OPEN milestone, checked where the tool has evidence."""
    try:
        import goals as gl
        rows = gl.scan(**({"goals_dir": goals_dir} if goals_dir else {}))
    except Exception:
        return {"looks_done": [], "confirmed_open": [], "unknown": 0,
                "stale_wording": []}

    f = _facts()
    looks_done, confirmed_open, stale_words = [], [], []
    unknown = 0
    for g in rows:
        for text in _open_milestones(g):
            res = check_milestone(text, g, f)
            row = {"goal": g.get("title", g.get("name", "")),
                   "goal_name": g.get("name", ""),
                   "milestone": text, "evidence": res["evidence"],
                   "checker": res["checker"]}
            if res["verdict"] is True:
                looks_done.append(row)
            elif res["verdict"] is False:
                confirmed_open.append(row)
            else:
                unknown += 1
            bad = stale_wording(text)
            if bad:
                stale_words.append({"goal": row["goal"], "milestone": text,
                                    "phrase": bad})
    return {"looks_done": looks_done, "confirmed_open": confirmed_open,
            "unknown": unknown, "stale_wording": stale_words}


def _open_milestones(goal: Dict[str, Any]) -> List[str]:
    path = goal.get("file") or ""
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                m = re.match(r"\s*-\s*\[\s\]\s+(.*\S)", line)
                if m:
                    out.append(m.group(1))
    except OSError:
        pass
    return out


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="meditate milestones", description="Which milestones are already true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    d = audit()
    if a.json:
        print(json.dumps({"tool_name": "meditate_milestones", "success": True,
                          "data": d, "metadata": {}, "errors": []}, indent=2))
        return 0
    if d["looks_done"]:
        print("ALREADY TRUE — the box is still empty")
        for r in d["looks_done"]:
            print("  %s" % r["milestone"])
            print("     %s  ·  %s" % (r["evidence"], r["goal"]))
    else:
        print("Nothing is silently done.")
    if d["stale_wording"]:
        print("\nSTATUS FROZEN INTO THE TEXT — will read wrong as the world moves")
        for r in d["stale_wording"]:
            print("  %-28s %s" % (r["phrase"][:28], r["milestone"][:70]))
    print("\n%d checked open, %d the tool cannot decide."
          % (len(d["confirmed_open"]), d["unknown"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
