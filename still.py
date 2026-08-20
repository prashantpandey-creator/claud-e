#!/usr/bin/env python3
"""still — the yogic diagnosis of the workspace-mind, as a JSON envelope.

Reads the facts from scan_projects and computes, deterministically:
  - each project's vṛtti class (the kind of mental modification it is, YS 1.6),
  - the antarāyas afflicting the whole workspace (the scatterers, YS 1.30),
  - a single nirodha / stillness index (0..100) — how calm the citta is (YS 1.2).

This is the deterministic half of /meditate (Rule 0). The skill consumes only
`data`; it never re-walks the trees or re-derives these scores itself. The
judgment half (vikalpa vs viparyaya nuance, which fork is the "real" one, the
four-attitude framing of each brief) stays in the skill.

Vṛtti classes (YS 1.6 — pañca vṛttayaḥ), English meaning beside each:
  pramāṇa   — valid/grounded   : a live project, recent work or open threads
  vikalpa   — concept/not-real : recent but barely any code yet — an idea
  nidrā     — sleep/dormant    : no commit in a long while
  smṛti     — memory/record    : recent, clean, settled — a finished record
  (viparyaya — error/wrong-track — left to the skill's judgment)
  container — not a vṛtti      : a workspace folder holding other repos

Descriptor:
  { "tool_name": "still",
    "input_schema":  { "roots": ["path"], "today": "YYYY-MM-DD" },
    "output_schema": { "projects": [ { ...scan fields..., "vritti", "vritti_en" } ],
                       "antarayas": { "alasya", "anavasthitatva", "samshaya", "nidra" },
                       "nirodha": { "stillness", "scatter", "breakdown" } } }
Envelope: { "success", "data", "metadata", "errors" }
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import scan_projects  # noqa: E402

# Thresholds (days). Tunable; documented in README.
ACTIVE_DAYS = 14     # within this, a repo is plainly live
SETTLE_DAYS = 30     # 14..30 clean = settling into a record; beyond = dormant
TINY_CODE = 5        # fewer real code files than this (recent) = idea-stage

# Extensions that are NOT "real code" for the vikalpa (idea) test.
NON_CODE_EXT = {"md", "txt", "json", "lock", "yml", "yaml", "toml", "cfg",
                "ini", "csv", "log", "png", "jpg", "jpeg", "svg", "gif",
                "wav", "npy", "pdf", "plist", "resolved"}

VRITTI_EN = {
    "pramana": "valid / grounded — a live project",
    "vikalpa": "concept / not-yet-real — an idea",
    "nidra": "sleep / dormant — long untouched",
    "smriti": "memory / record — settled, finished",
    "viparyaya": "error / wrong-track",
    "container": "not a vṛtti — a workspace folder",
}


def _age_days(project, today):
    lc = project.get("last_commit")
    if not lc or not lc.get("date"):
        return None
    try:
        d = datetime.date.fromisoformat(lc["date"])
        return (datetime.date.fromisoformat(today) - d).days
    except (ValueError, TypeError):
        return None


def _real_code_files(project):
    return sum(n for ext, n in project.get("languages", []) if ext not in NON_CODE_EXT)


def classify_vritti(project, today):
    """Return (vritti_key, english_reason)."""
    if not project.get("is_git") or project.get("kind") == "workspace":
        return "container", VRITTI_EN["container"]

    age = _age_days(project, today)
    dirty = project.get("dirty_files", 0)

    if age is None:
        return "nidra", "no commits yet — asleep"
    if age > SETTLE_DAYS:
        return "nidra", f"untouched {age} days — dormant"
    if dirty > 0 or age <= ACTIVE_DAYS:
        # recent, but if there's almost no real code it's still just an idea
        if _real_code_files(project) < TINY_CODE and dirty == 0:
            return "vikalpa", "recent but barely any code — an idea taking shape"
        return "pramana", f"active — {dirty} open, last touched {age}d ago"
    # 14..30 days, clean, has code: a settled record
    if _real_code_files(project) < TINY_CODE:
        return "vikalpa", "recent but barely any code — an idea taking shape"
    return "smriti", f"clean and settling — last touched {age}d ago"


def _stem(name):
    """Crude family stem: lowercase, first token before -/_ , digits stripped."""
    base = name.lower().replace("_", "-").split("-")[0]
    return "".join(c for c in base if not c.isdigit())


def diagnose_antarayas(projects):
    repos = [p for p in projects if p.get("is_git")]

    dirty = [p for p in repos if p.get("dirty_files", 0) > 0]
    dirty.sort(key=lambda p: -p["dirty_files"])
    alasya = {
        "meaning": "sloth — work begun and left uncommitted (YS 1.30 ālasya)",
        "repos": [{"name": p["name"], "path": p["path"],
                   "dirty_files": p["dirty_files"]} for p in dirty],
        "total_dirty": sum(p["dirty_files"] for p in dirty),
    }

    off_main = [p for p in repos if (p.get("branch") or "main") not in ("main", "master")]
    anavasthitatva = {
        "meaning": "instability — attention split across feature branches (YS 1.30 anavasthitatva)",
        "repos": [{"name": p["name"], "path": p["path"], "branch": p["branch"]}
                  for p in off_main],
    }

    # samshaya — doubt/duplication: exact-name collisions + family-stem clusters
    by_name = {}
    for p in repos:
        by_name.setdefault(p["name"].lower(), []).append(p["path"])
    collisions = {n: paths for n, paths in by_name.items() if len(paths) > 1}

    by_stem = {}
    for p in repos:
        by_stem.setdefault(_stem(p["name"]), []).append(p["name"])
    families = {s: sorted(set(names)) for s, names in by_stem.items() if len(set(names)) > 1}

    samshaya = {
        "meaning": "doubt — parallel/duplicate efforts; the mind unsure which is real (YS 1.30 saṃśaya)",
        "exact_name_collisions": collisions,
        "fork_families": families,
    }

    nidra_repos = []  # filled by caller via classification, kept here for shape parity
    return {
        "alasya": alasya,
        "anavasthitatva": anavasthitatva,
        "samshaya": samshaya,
        "nidra": {"meaning": "sleep — projects long dormant (YS 1.30)", "repos": nidra_repos},
    }


def nirodha_index(projects, antarayas):
    """A transparent scatter score; stillness = 100 - scatter, clamped 0..100."""
    alasya = antarayas["alasya"]
    off_main = antarayas["anavasthitatva"]["repos"]
    families = antarayas["samshaya"]["fork_families"]
    nidra = antarayas["nidra"]["repos"]

    open_whirls = len(alasya["repos"])
    dirty_mass = sum(min(r["dirty_files"], 50) for r in alasya["repos"]) / 20.0
    doubt = sum(max(0, len(members) - 1) for members in families.values())

    breakdown = {
        "open_whirls(alasya)": round(2 * open_whirls, 1),
        "dirty_mass(alasya)": round(dirty_mass, 1),
        "instability(anavasthitatva)": round(3 * len(off_main), 1),
        "doubt(samshaya)": round(4 * doubt, 1),
        "dormancy(nidra)": round(1 * len(nidra), 1),
    }
    scatter = round(sum(breakdown.values()), 1)
    stillness = max(0, min(100, round(100 - scatter)))
    return {
        "stillness": stillness,
        "scatter": scatter,
        "scale": "0 = perfectly still citta · 100 = perfectly calm; higher stillness is calmer",
        "breakdown": breakdown,
    }


def compute(scan_env, today=None):
    """Pure: scan envelope in -> diagnosis envelope out."""
    today = today or datetime.date.today().isoformat()
    if not scan_env.get("success"):
        return {
            "tool_name": "still", "success": False, "data": {},
            "metadata": {"today": today},
            "errors": scan_env.get("errors") or [{"code": "scan_failed",
                       "message": "upstream scan returned success=false"}],
        }

    projects = [dict(p) for p in scan_env["data"]["projects"]]
    for p in projects:
        key, en = classify_vritti(p, today)
        p["vritti"], p["vritti_en"] = key, en

    antarayas = diagnose_antarayas(projects)
    antarayas["nidra"]["repos"] = [
        {"name": p["name"], "path": p["path"],
         "last_commit": (p.get("last_commit") or {}).get("date")}
        for p in projects if p.get("vritti") == "nidra"
    ]
    nirodha = nirodha_index(projects, antarayas)

    counts = {}
    for p in projects:
        counts[p["vritti"]] = counts.get(p["vritti"], 0) + 1

    return {
        "tool_name": "still", "success": True,
        "data": {
            "projects": projects,
            "vritti_counts": counts,
            "antarayas": antarayas,
            "nirodha": nirodha,
        },
        "metadata": {"today": today, "count": len(projects)},
        "errors": [],
    }


def still(roots, today=None, max_depth=4):
    scan_env = scan_projects.scan(roots, max_depth=max_depth)
    return compute(scan_env, today)


def _human(env):
    if not env["success"]:
        return "still: FAILED — " + json.dumps(env["errors"])
    d = env["data"]
    n = d["nirodha"]
    lines = [
        f"निरोध nirodha / stillness:  {n['stillness']}/100   "
        f"(scatter {n['scatter']} — lower is calmer)",
        "  " + "  ".join(f"{k}={v}" for k, v in n["breakdown"].items()),
        "",
        "वृत्ति vṛtti census (kinds of mental modification):",
    ]
    for k, c in sorted(d["vritti_counts"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {k:<10} {c:>2}   {VRITTI_EN.get(k, '')}")
    a = d["antarayas"]
    lines += ["", "अन्तराय antarāyas (the scatterers):"]
    lines.append(f"  ālasya (sloth): {len(a['alasya']['repos'])} repos, "
                 f"{a['alasya']['total_dirty']} uncommitted files")
    if a["alasya"]["repos"]:
        top = a["alasya"]["repos"][0]
        lines.append(f"    loudest: {top['name']} — {top['dirty_files']} dirty")
    lines.append(f"  anavasthitatva (instability): {len(a['anavasthitatva']['repos'])} "
                 f"repos off main")
    fam = a["samshaya"]["fork_families"]
    if fam:
        lines.append("  saṃśaya (doubt / forks): " +
                     "; ".join(f"{s}→{members}" for s, members in fam.items()))
    if a["samshaya"]["exact_name_collisions"]:
        lines.append("    same-name collisions: " +
                     ", ".join(a["samshaya"]["exact_name_collisions"].keys()))
    lines.append(f"  nidrā (dormant): {len(a['nidra']['repos'])} repos")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Yogic diagnosis of the workspace-mind")
    ap.add_argument("--root", action="append", dest="roots")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (default: actual today)")
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    roots = args.roots or scan_projects.DEFAULT_ROOTS
    env = still(roots, today=args.today, max_depth=args.max_depth)
    print(json.dumps(env, indent=2, ensure_ascii=False) if args.json else _human(env))
    return 0 if env["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
