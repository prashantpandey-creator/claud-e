"""go — move everything forward. One verb, no menu.

    meditate go          # launch what the world needs: a repair agent if the
                         # queue is open, plus one agent per dispatchable goal
    meditate go 2        # same, capped at 2 launches total
    meditate go 0        # dry-run: show what WOULD launch

The fleet size is not a setting — the world sets it. Open repair queue = one
repair agent. N goals with open milestones and no agent already on them
(4h cooldown) = N goal agents. A number only restrains, never pads.

Priority is fixed and matches status: knowledge integrity before new work —
a fleet building on drifted facts builds wrong.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)
import paths

MEDITATION_DIR = os.path.expanduser("~/.claude/meditation")
STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")


def repair_items(store_dir: str = STORE_DIR):
    """Selectable repair items: only ACTIONABLE drift (has failing evidence or
    the drifted flag) — evidence-free session stubs are noise, not work."""
    from coordination import drift_report
    rep = drift_report(store_dir)
    return [m for m in rep["memories"]
            if m.get("failing") or "drifted" in (m.get("flags") or [])]


def _resolve_claim(claim: str):
    """Check ONE failing claim. Three-valued on purpose.

    True  = the thing really is gone (real drift, worth an agent)
    False = it is right there (the grader is wrong, worth nothing)
    None  = not mechanically checkable from here

    Conflating None with False is the single root cause behind all six
    grader defects fixed in nidra a1c1baf. A checker that cannot say
    "I don't know" will say "broken" instead, and someone pays for it.
    """
    claim = str(claim or "")
    if claim.startswith("path:"):
        return not os.path.exists(os.path.expanduser(claim[5:]))
    if claim.startswith("wikilink:[[") and claim.endswith("]]"):
        target = claim[11:-2]
        if target.endswith(".md"):
            target = target[:-3]
        roots = [paths.memory_root()]
        for root in roots:
            if not os.path.isdir(root):
                continue
            for sub in os.listdir(root):
                if os.path.exists(os.path.join(root, sub, target + ".md")):
                    return False
        return True
    return None


def precheck(items) -> Dict[str, object]:
    """Measure the queue's precision BEFORE spending an agent on it.

    An `os.path.exists` per item costs microseconds and zero tokens; an
    agent investigating the same item costs thousands. Measured on a real
    store 2026-08-23: 28 of 30 items were the grader inventing claims, and
    the fleet agent that investigated them burned ~44k tokens to change
    nothing. Any tool that can dispatch work must first gate on whether its
    own findings are true.
    """
    real, fp, unknown, keep = 0, 0, 0, []
    for m in items:
        verdicts = [_resolve_claim(f.get("claim")) for f in (m.get("failing") or [])]
        if not verdicts:
            unknown += 1
            continue
        if any(v is True for v in verdicts):
            real += 1
            keep.append(m)
        elif any(v is False for v in verdicts):
            fp += 1
        else:
            unknown += 1
    decided = real + fp
    precision = (real / decided) if decided else 1.0
    out = {"real": real, "false_positive": fp, "not_checkable": unknown,
           "precision": precision, "actionable": keep, "verdict": "ok", "message": ""}
    # Below this line the queue is a bug report about the tool, not a work list.
    if decided and precision < 0.5:
        out["verdict"] = "instrument"
        out["message"] = (
            "%d of %d checkable findings are FALSE — the paths/links they name "
            "are right there on disk. That is the grader inventing claims, not "
            "knowledge drifting. Do NOT dispatch a repair agent: fix the "
            "extractor in nidra/adapters/memory_files.py with a test that fails "
            "without the fix, then re-grade." % (fp, decided))
    return out


def _repair_kickoff(meditation_dir: str, store_dir: str = STORE_DIR,
                    select: Optional[str] = None,
                    items: Optional[List[Dict]] = None) -> Optional[Dict[str, str]]:
    items = repair_items(store_dir) if items is None else items
    if select is not None:
        picked = [m for i, m in enumerate(items, 1)
                  if str(i) == select or m.get("id", "").startswith(select)]
        if not picked:
            return None
        items = picked
    if not items:
        qp = os.path.join(meditation_dir, "repair-queue.md")
        if not os.path.exists(qp):
            return None
    # Resolve every claim HERE, deterministically, for zero tokens. An agent
    # made to re-derive this pays for it in every later turn of its context.
    gate = precheck(items)
    if gate["verdict"] == "instrument":
        return {"cwd": os.path.expanduser("~"), "prompt": "", "name": "repair-blocked",
                "blocked": gate["message"], "gate": gate}
    items = gate["actionable"] or items
    detail = "\n".join(
        "- %s: %s%s" % (m["id"], m["statement"][:140],
                        "".join("\n    CONFIRMED GONE: " + f["claim"]
                                for f in m.get("failing", [])
                                if _resolve_claim(f.get("claim")) is True))
        for m in items) or "(see the queue file)"
    prompt = (
        "Repair these graded memories. Every claim below was ALREADY CHECKED "
        "on disk by the dispatcher and confirmed missing — you do not need to "
        "`ls` anything to reconfirm it:\n"
        "%s\n"
        "For each: fix the source .md so it states the truth. If the thing was "
        "removed, SAY it was removed — phrasing it as an absence ('since "
        "removed', 'no longer exists') is both accurate and clears the claim, "
        "because the grader stops asserting a path a memory says is gone. If "
        "the world moved somewhere new, point the memory at the new location.\n"
        "Then run `meditate grade` — a clean re-check clears the queue and "
        "counts as a REPAIR. Do not push; commit local if you touch a repo." % detail)
    name = "repair-" + (items[0]["id"][-6:] if select else "queue")
    return {"cwd": os.path.expanduser("~"), "prompt": prompt, "name": name}




HEADLESS_LOG_DIR = os.path.join(MEDITATION_DIR, "agents")


def _headless(cwd: str, prompt: str, name: str, model: str = "") -> bool:
    """Run the agent with no GUI at all, output to a log.

    `claude -p` needs no window, no Terminal and no awake display — verified
    directly with the screen off. It runs the task to completion and exits,
    which is what an unattended dispatch wants anyway.
    """
    import subprocess
    try:
        os.makedirs(HEADLESS_LOG_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        log = os.path.join(HEADLESS_LOG_DIR, "%s-%s.log" % (stamp, name[:40]))
        with open(log, "w") as fh:
            fh.write("# %s\n# cwd: %s\n\n" % (name, cwd))
            fh.flush()
            subprocess.Popen(
                ["claude", "-p", prompt, "--model", model or "sonnet",
                 "--dangerously-skip-permissions"],
                cwd=cwd if os.path.isdir(cwd) else os.path.expanduser("~"),
                stdout=fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except (OSError, ValueError):
        return False


def dispatch_one(cwd: str, prompt: str, name: str, model: str = "",
                 gui=None, headless=None) -> bool:
    """Open a watchable window if we can; otherwise run it headless.

    Measured live: the heartbeat's first real run selected the right work,
    opened the gate correctly (away 115 min) and dispatched ZERO — every
    osascript launch failed, the first by timing out after 75 seconds. The
    cause is a conflict baked into the design: the gate fires when the owner
    is AWAY, away almost always means the DISPLAY IS OFF, and driving Terminal
    through System Events needs an awake display.

    There is no display detector to consult — pmset's IODisplayWrangler probe
    answers "Internal failure" on this hardware — so this does not predict. It
    tries the window and falls back. A watchable window is better when it is
    available; a running agent is better than a window that never opened.
    """
    if gui is None:
        from launch import launch_claude as gui       # type: ignore
    if headless is None:
        headless = _headless
    try:
        if gui(cwd, prompt, name, model):
            return True
    except Exception:
        pass                       # osascript times out by raising, not returning
    try:
        return bool(headless(cwd, prompt, name, model))
    except Exception:
        return False


# Away = no key or mouse for this long. Below it you catch someone reading,
# and an agent editing files under a reader's hands is exactly the collision
# the sangama layer spends its life preventing.
AUTO_AWAY_AFTER_S = 20 * 60
AUTO_MAX_AGENTS = 3          # ~35k boot tokens each before any work happens


_READ_IT = object()      # "I brought no reading — go take one"


def auto_should_run(idle_s: Any = _READ_IT) -> Dict[str, Any]:
    """Should the heartbeat dispatch right now, and how many agents may it use?

    Deterministic and side-effect free so it can be tested without a Mac, a
    fleet, or a night. Fails CLOSED: if idle time cannot be read we assume the
    owner is present. Unknown is not away — the same three-valued rule the
    grader had to learn, applied to a person.

    Three-valued in the SIGNATURE too, and that is the whole repair. `None`
    used to mean both "unknown" and "no reading supplied", so the one call that
    meant unknown — auto_should_run(idle_s=None) — quietly took a live reading
    instead, and on an away machine that reading answered RUN. The unknown
    branch below was correct and unreachable from the outside; its own test
    passed only while the owner happened to be at the keyboard. A float is a
    reading, None is UNKNOWN and holds, and only the sentinel goes to ioreg.
    """
    if idle_s is _READ_IT:
        try:
            import attention
            idle_s = attention.signals().get("idle_s")
        except Exception:
            idle_s = None
    if idle_s is None:
        return {"run": False, "budget": 0,
                "why": "cannot tell whether you are here, so assuming you are"}
    if idle_s < AUTO_AWAY_AFTER_S:
        return {"run": False, "budget": 0,
                "why": "someone is here (idle %d min)" % int(idle_s / 60)}
    return {"run": True, "budget": AUTO_MAX_AGENTS,
            "why": "away %d min" % int(idle_s / 60)}


def thread_work(sessions_dir: Optional[str] = None,
                ledger_path: Optional[str] = None,
                cwd_for: Optional[Callable[[str], str]] = None) -> List[Dict[str, str]]:
    """Live continuation threads the fleet can start on its own.

    /meditate splits a tangled session into per-thread chats, each carrying a
    ready kickoff prompt and its own cwd. `go` dispatched goals and the repair
    queue and had never heard of them — two parallel systems that never met,
    so every split ended in "here is a prompt, paste it somewhere". That is a
    manual system wearing an automation costume.

    A live thread WITH a kickoff is work. A live thread without one is half a
    chat, and dispatching an agent with no instruction burns ~35k boot tokens
    to accomplish nothing, so it is skipped. Anything already sent (the
    dispatch ledger) is skipped too, or the heartbeat re-opens the same agent
    every hour.
    """
    import launch as lp
    base = sessions_dir or lp.MEDITATION_DIR
    try:
        threads = lp.find_threads(base)
    except Exception:
        return []
    sent = set()
    led = ledger_path
    if led is None:
        try:
            import drive as dv
            led = dv.LEDGER_PATH
        except Exception:
            led = None
    if led and os.path.exists(led):
        try:
            with open(led) as fh:
                for line in fh:
                    try:
                        sent.add(json.loads(line).get("name"))
                    except ValueError:
                        continue
        except OSError:
            pass
    out = []
    for th in threads:
        if th.get("status") != "live" or not th.get("kickoff"):
            continue
        stem = os.path.splitext(os.path.basename(th.get("path") or ""))[0]
        name = ("thread-%s-%s" % (th.get("session", ""), stem))[:60]
        if name in sent:
            continue
        sent.add(name)          # two INDEX rows may name the same chat file;
                                # dispatching it twice is two agents on one job
        # Unknown cwd is not a guess. Eight live threads come from July
        # sessions whose transcripts are gone, so find_session_cwd falls back
        # to a default — under automation that is eight agents opening in a
        # directory nobody chose, on month-old threads. A thread the tool
        # cannot place is one it must not start.
        resolve = cwd_for or lp.find_session_cwd
        try:
            cwd = resolve(str(th.get("session", "")).split("-")[0])
        except Exception:
            continue
        if not cwd or os.path.realpath(cwd) == os.path.realpath(lp.FALLBACK_CWD):
            continue
        if not os.path.isdir(cwd):
            continue
        out.append({"name": name, "title": th.get("thread", "")[:70],
                    "cwd": cwd, "prompt": th["kickoff"]})
    return out


def run(n: Optional[int] = None, repair_only: bool = False,
        only_goal: Optional[str] = None, repair_select: Optional[str] = None,
        meditation_dir: str = MEDITATION_DIR, store_dir: str = STORE_DIR,
        goals_dir: Optional[str] = None, history_path: Optional[str] = None,
        ledger_path: Optional[str] = None,
        launcher: Optional[Callable[[str, str, str], bool]] = None) -> Dict[str, Any]:
    import drive as dv
    import goals as gl

    lp = ledger_path or dv.LEDGER_PATH
    cands = dv.dispatchable(goals_dir, lp, history_path)
    if only_goal:
        cands = [c for c in cands if c["name"] == only_goal]
        repair = None
    else:
        repair = _repair_kickoff(meditation_dir, store_dir, select=repair_select)

    would: List[str] = []
    # The queue failed its own precision gate: it is a bug report about the
    # grader, not work. Dispatching here is how ~44k tokens got spent
    # confirming findings that were false before the agent ever booted.
    blocked = repair.get("blocked") if repair else None
    if blocked:
        repair = None
    if repair:
        would.append("repair: " + os.path.join(meditation_dir, "repair-queue.md"))
    would += ["goal: %s -> %s" % (g["name"], g["next"]) for g in cands]
    # Threads belong in the plan, not only in the launch loop: `go 0` is the
    # dry run, and a dry run that omits half the work is a lie about the plan.
    _threads = thread_work() if not only_goal else []
    would += ["thread: %s" % th["title"] for th in _threads]

    result: Dict[str, Any] = {"would": would, "repair_launched": False,
                              "goals_launched": 0, "sent": [], "errors": [],
                              "cooling": getattr(dv.dispatchable, "cooling", 0)}
    if blocked:
        result["repair_blocked"] = blocked
        result["errors"].append({"code": "instrument", "message": blocked})
    if n == 0:
        return result

    if launcher is None:
        # Not launch_claude directly: dispatch_one tries the watchable window
        # and falls back to headless when it fails. The first real unattended
        # run selected the right work and launched NOTHING because every
        # osascript call failed with the display asleep.
        launcher = dispatch_one
    budget = n if n is not None else len(would)

    if repair and budget > 0:
        try:
            if launcher(repair["cwd"], repair["prompt"], "repair-queue"):
                result["repair_launched"] = True
                result["sent"].append("repair-queue")
                result.setdefault("launched", []).append(
                    {"kind": "repair", "title": "the repair queue",
                     "doing": "re-checking the facts that failed verification",
                     "cwd": repair["cwd"]})
                budget -= 1
        except Exception as e:
            # NOT silent: a launcher that raises (signature drift, missing
            # Terminal) hid a real break for commits behind "0 launched".
            result["errors"].append("repair launch failed: %s" % e)

    taken: Dict[str, str] = {}
    if repair_only:
        budget = 0
    if budget > 0:
        kw = {}
        if goals_dir:
            kw["goals_dir"] = goals_dir
        if history_path:
            kw["history_path"] = history_path
        # ONE AGENT PER WORKING DIRECTORY.
        #
        # The machine is not the limit: a claude session measured 286 MB and
        # ~6% CPU here, so six of them is 1.7 GB of 32 GB and a third of one
        # core. What actually breaks is two agents editing the same checkout —
        # 8 collisions warned in this workspace's own log, the latest on
        # launch.py. Sessions also pile into the same place (15 in one project
        # directory, 20 in home), so "all of them at once" means "all of them
        # into the same repo".
        #
        # Different repos still run in parallel; the same repo queues. The
        # deferred ones are named, never silently dropped.
        # dispatchable rows carry the slug, not the human title — without this
        # join the launch report reads "goal-production-stable" instead of
        # "Production stable", which is exactly the report nobody can use.
        titles: Dict[str, str] = {}
        try:
            for row in gl.scan(**kw):
                titles[row.get("name", "")] = (row.get("title") or "")
        except Exception:
            pass
        for g in cands[:budget]:
            k = gl.kickoff(g["name"], **kw)
            if not k:
                continue
            if not (g.get("next") or "").strip():
                # A goal with no open milestone cannot ever be finished, so an
                # agent sent at it reports "88 minutes, worth a look" forever.
                # Three of these were sitting in the live ledger.
                result.setdefault("skipped", []).append(
                    {"goal": g["name"], "why": "nothing open to work on"})
                continue
            here = os.path.realpath(k["cwd"])
            if here in taken:
                result.setdefault("deferred", []).append(
                    {"goal": g["name"], "waiting_on": taken[here],
                     "cwd": k["cwd"],
                     "why": "another agent is already working in this repo"})
                continue
            taken[here] = g["name"]
            ok = False
            try:
                ok = bool(launcher(k["cwd"], k["prompt"], "goal-" + g["name"][:20],
                                   k.get("model", "")))
            except Exception as e:
                result["errors"].append("launch %s failed: %s" % (g["name"], e))
            if not ok:
                continue
            result["goals_launched"] += 1
            result["sent"].append("goal-" + g["name"])
            result.setdefault("launched", []).append(
                {"kind": "goal",
                 "title": (g.get("title") or titles.get(g["name"]) or
                           g["name"]).split("\u2014")[0].strip(),
                 "doing": (g.get("next") or "the open milestone").strip(),
                 "cwd": k["cwd"]})
            try:
                os.makedirs(os.path.dirname(lp), exist_ok=True)
                with open(lp, "a") as f:
                    f.write(json.dumps({"goal": g["name"], "milestone": g["next"],
                                        "ts_epoch": time.time(),
                                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                            time.gmtime()),
                                        # Which Terminal window this agent got.
                                        # drive.py recorded it; this writer did
                                        # not, so nothing could ever close the
                                        # right window for a goal launched here.
                                        "window_id": getattr(launcher,
                                                             "last_window_id", ""),
                                        }) + "\n")
            except OSError:
                pass
            budget -= 1

    # ---- continuation threads --------------------------------------------
    # The split writes per-thread chats with a ready kickoff and its own cwd.
    # Without this loop each one ended as "here is a prompt, paste it" — a
    # manual system wearing an automation costume. One agent per working
    # directory still holds, so a thread whose cwd is already taken waits.
    if budget > 0:
        for th in _threads:
            if budget <= 0:
                break
            here = os.path.abspath(th["cwd"])
            if here in taken:
                result.setdefault("deferred", []).append(
                    {"goal": th["title"], "waiting_on": taken[here],
                     "cwd": th["cwd"],
                     "why": "another agent is already working in this repo"})
                continue
            taken[here] = th["name"]
            ok = False
            try:
                ok = bool(launcher(th["cwd"], th["prompt"], th["name"][:40]))
            except Exception as e:
                result["errors"].append("launch %s failed: %s" % (th["name"], e))
            if not ok:
                continue
            result["threads_launched"] = result.get("threads_launched", 0) + 1
            result["sent"].append(th["name"])
            result.setdefault("launched", []).append(
                {"kind": "thread", "title": th["title"],
                 "doing": "the next step of a split thread", "cwd": th["cwd"]})
            budget -= 1
            try:
                os.makedirs(os.path.dirname(lp), exist_ok=True)
                with open(lp, "a") as f:
                    f.write(json.dumps({"name": th["name"], "kind": "thread",
                                        "ts_epoch": time.time(),
                                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                            time.gmtime()),
                                        "window_id": getattr(launcher,
                                                             "last_window_id", ""),
                                        }) + "\n")
            except OSError:
                pass
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate go", description="Move everything forward")
    ap.add_argument("sel", nargs="?", default=None,
                    help="cap (int), goal name, or repair item # / mem_id")
    ap.add_argument("--auto", action="store_true",
                    help="heartbeat mode: dispatch ONLY while the owner is away")
    ap.add_argument("--repair-only", action="store_true",
                    help="only the repair agent (this is `meditate fix`)")
    ap.add_argument("--list", action="store_true",
                    help="with --repair-only: numbered repair items")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.auto:
        # The heartbeat's entry point. Everything unattended in this tool was
        # read-only — grade, archive, dashboard, voice — so nothing ever ACTED
        # on its own, and every split ended as a prompt for a human to paste.
        # This is the connection: gated on absence, capped, and honest when it
        # declines, because a gate that holds silently reads as "there was
        # nothing to do".
        gate = auto_should_run()
        if not gate["run"]:
            print("holding — %s" % gate["why"])
            return 0
        res = run(n=gate["budget"])
        launched = (res.get("goals_launched", 0) + res.get("threads_launched", 0)
                    + (1 if res.get("repair_launched") else 0))
        print("dispatched %d (%s)" % (launched, gate["why"]))
        for row in res.get("launched", []):
            print("  %-7s %s" % (row.get("kind", ""), row.get("title", "")[:64]))
        return 0

    if args.repair_only and args.list:
        items = repair_items()
        if not items:
            print("Repair queue is clean.")
            return 0
        print("Repairable items (meditate fix <n> to launch one):")
        for i, m in enumerate(items, 1):
            print("  %d. %s  %s" % (i, m["id"], m["statement"][:110]))
            for fl in m.get("failing", []):
                print("       FAILS %s" % fl["claim"])
        return 0

    n = None
    only_goal = None
    repair_select = None
    if args.sel is not None:
        try:
            n = int(args.sel)
        except ValueError:
            if args.repair_only:
                repair_select = args.sel
            else:
                only_goal = args.sel
    if args.repair_only and n is not None and n > 0 and str(n) == args.sel:
        repair_select = args.sel        # `meditate fix 2` = item 2, not a cap
        n = None
    data = run(n=n, repair_only=args.repair_only,
               only_goal=only_goal, repair_select=repair_select)
    env = {"tool_name": "meditate_go", "success": True, "data": data,
           "metadata": {"dry_run": n == 0}, "errors": []}
    if args.json:
        print(json.dumps(env, indent=2))
        return 0
    if n == 0:
        print("Would launch (dry-run):")
        for w in data["would"]:
            print("  " + w)
        if not data["would"]:
            print("  nothing — world is clean and goals are covered")
        return 0
    if not data["sent"]:
        print("Nothing to move: no repair queue, no dispatchable goal"
              + (" (%d cooling)" % data["cooling"] if data["cooling"] else ""))
        return 0
    detail = data.get("launched") or []
    if detail:
        heads = " and ".join(d["title"] for d in detail[:3])
        print("Started %d agent%s \u2014 %s. Each is in its own Terminal "
              "window." % (len(detail), "" if len(detail) == 1 else "s", heads))
        for d in detail:
            print("  \u2022 %s \u2014 %s  (in %s)"
                  % (d["title"], d["doing"][:90],
                     d["cwd"].replace(os.path.expanduser("~"), "~")))
    else:
        print("Launched %d agent(s):" % len(data["sent"]))
        for s in data["sent"]:
            print("  " + s)
    for d in data.get("deferred") or []:
        print("  \u23f8 %s waits \u2014 %s is already working in that repo"
              % (d["goal"], d["waiting_on"]))
    for e in data.get("errors") or []:
        print("  \u26a0 %s" % (e if isinstance(e, str) else e.get("message", e)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
