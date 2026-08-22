"""formation — knowledge FORMING as a natural part of the pipeline.

nidra grades; it never formed. The adapters only imported knowledge that
already existed (hand-written .md files, shallow session maps). This module
is the missing half of the sleep cycle: the day becomes memory.

Two lanes, honest about what each can be:

LANE 1 — deterministic, runs on every heartbeat. Commits made during a
session ARE distilled knowledge (the owner already wrote the distillation:
the commit message). Each becomes a memory whose evidence is born attached —
source = the transcript, excerpt = the literal commit line — so the very
next grade pass verifies it machine_checked, and archiving the transcript
retargets it like any other receipt. Nothing is served unverified.

LANE 2 — judgment, orchestrated. Substantive sessions queue for
distillation; `meditate distill <sid>` emits an agent kickoff that writes a
real memory .md (Why / How to apply / originSessionId) into the memory
store, where the memory_files adapter grades it within one heartbeat.
Formation is free; serving still has to be earned.

CLI:
  meditate distill              # the formation queue
  meditate distill <sid>        # agent kickoff for one session
  meditate distill --done <sid> # mark distilled (agent does this at the end)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)
NIDRA_ROOT = os.path.expanduser("~/projects/nidra")
sys.path.insert(0, NIDRA_ROOT)

STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")
LEDGER_PATH = os.path.expanduser("~/.claude/meditation/distilled.jsonl")
MEMORY_ROOT = os.path.expanduser("~/claude-sync/memory")
SUBSTANTIVE_USER_MSGS = 15

# git commit stdout: "[branch abc1234] subject"
_COMMIT_STDOUT = re.compile(r"\[[\w./-]{1,40} ([0-9a-f]{7,10})\] ([^\\\"\n]{8,140})")
# The log-oneline pattern ("abc1234 subject") was tried and REMOVED: live
# transcripts are full of hex-prefixed listings (session ids, tool output)
# and it formed garbage like "83525ed2 can you check if we can find blue
# lotus...". Only git commit's own stdout shape is unambiguous.


def extract_commits(transcript_path: str) -> List[Dict[str, str]]:
    """Every commit visible in a transcript, deduped by hash, stdout-form wins."""
    try:
        with open(transcript_path, errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    found: Dict[str, Dict[str, str]] = {}
    for m in _COMMIT_STDOUT.finditer(text):
        h, subj = m.group(1), m.group(2).strip()
        found[h] = {"hash": h, "subject": subj, "excerpt": m.group(0)[:200]}
    return list(found.values())


FORM_WINDOW_DAYS = 30   # heartbeat organ: form from RECENT sessions; the
                        # one-time backfill already happened. Full-text
                        # reading all 750 MB on every pass timed doctor out.


def form_commit_facts(store_dir: str, sessions: List[Dict[str, Any]],
                      since_days: int = FORM_WINDOW_DAYS) -> int:
    """LANE 1: append commit-fact memories for unseen hashes. Idempotent.

    Reads only transcripts modified inside the window AND changed since the
    last scan (mtime cache in the store dir) — a heartbeat must stay cheap.
    """
    try:
        from nidra.store import Store, new_memory, sha256_text
    except ImportError:
        return 0
    store = Store(store_dir)
    if not store.exists():
        store.init()
    cache_path = os.path.join(store_dir, "formation-scan.json")
    try:
        with open(cache_path) as fh:
            scanned = json.load(fh)
    except Exception:
        scanned = {}
    mems = store.load()
    known = {m["id"] for m in mems}
    formed = 0
    fresh = []
    cutoff = time.time() - since_days * 86400
    for s in sessions:
        tp = s.get("file", "")
        if tp and not os.path.isabs(tp):
            # sessions.py emits basenames; the directory rides separately
            tp = os.path.join(s.get("_project_dir", ""), tp)
        if not tp or not os.path.isfile(tp):
            continue
        try:
            mt = os.path.getmtime(tp)
        except OSError:
            continue
        if mt < cutoff or scanned.get(tp) == mt:
            continue
        scanned[tp] = mt
        slug = s.get("_project_slug", "") or s.get("_project_dir", "")
        for c in extract_commits(tp):
            mid = "mem_" + sha256_text("commitfact|" + c["hash"])[:12]
            if mid in known:
                continue
            known.add(mid)
            m = new_memory(
                "Shipped %s: %s" % (c["hash"], c["subject"]),
                subject="commit:" + c["hash"],
                tags=["formed", "commit-fact"] + (["project:" + slug] if slug else []),
                confidence=0.8,
            )
            m["id"] = mid
            m["evidence"].append({
                "source": tp,
                "excerpt": c["excerpt"],
                "sha256": sha256_text(c["excerpt"]),
                "locator": "commit:" + c["hash"],
                "checked_at": None,
            })
            fresh.append(m)
            formed += 1
    try:
        with open(cache_path + ".tmp", "w") as fh:
            json.dump(scanned, fh)
        os.replace(cache_path + ".tmp", cache_path)
    except OSError:
        pass
    if fresh:
        store.save(mems + fresh)
        store.journal({"event": "formation.commit_facts", "formed": formed,
                       "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())})
    return formed


# ---- LANE 2: the distillation queue ----------------------------------------

def _distilled(ledger_path: str) -> set:
    done = set()
    if os.path.exists(ledger_path):
        with open(ledger_path, errors="replace") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["session_id"])
                except Exception:
                    continue
    return done


def mark_distilled(sid: str, ledger_path: str = LEDGER_PATH) -> None:
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    with open(ledger_path, "a") as f:
        f.write(json.dumps({"session_id": sid,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}) + "\n")


def formation_queue(sessions: List[Dict[str, Any]],
                    ledger_path: str = LEDGER_PATH) -> List[Dict[str, Any]]:
    """Substantive sessions not yet distilled — the day awaiting memory."""
    done = _distilled(ledger_path)
    out = []
    for s in sessions:
        if s.get("session_id") in done:
            continue
        if (s.get("counts") or {}).get("user", 0) < SUBSTANTIVE_USER_MSGS:
            continue
        out.append(s)
    return out


def distill_kickoff(sid: str, sessions: List[Dict[str, Any]],
                    memory_root: str = MEMORY_ROOT) -> Optional[Dict[str, str]]:
    for s in sessions:
        if s.get("session_id") != sid:
            continue
        slug = s.get("_project_slug", "-Users-badenath-projects-vedic-puran")
        mem_dir = os.path.join(memory_root, slug)
        prompt = (
            "Distill session %s ('%s', transcript: %s) into durable memory.\n"
            "Read the transcript. For each NON-OBVIOUS lesson, decision, or law "
            "worth keeping (not restatable from git/code), write ONE .md file in "
            "%s with frontmatter (name, description, metadata.type, "
            "metadata.originSessionId: %s) and a body containing the fact plus "
            "Why: and How to apply: lines. Link related memories with "
            "[[wikilinks]]. Add one index line each to MEMORY.md. "
            "Skip anything already covered — check first. When finished run:\n"
            "  meditate distill --done %s\n"
            "The next heartbeat grades what you wrote; only what verifies gets "
            "served."
            % (sid, s.get("title") or "untitled", s.get("file", "?"),
               mem_dir, sid, sid))
        return {"sid": sid, "cwd": s.get("cwd") or os.path.expanduser("~"),
                "prompt": prompt}
    return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Knowledge formation")
    ap.add_argument("sid", nargs="?", help="session to distill")
    ap.add_argument("--done", metavar="SID", help="mark a session distilled")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.done:
        mark_distilled(args.done)
        print("marked distilled: %s" % args.done)
        return 0

    try:
        from sessions import scan_all_projects
        sessions = scan_all_projects(cap=500)["data"]["sessions"]
    except Exception as e:
        sessions = []
        if not args.json:
            print("session scan failed: %s" % e)

    if args.sid:
        k = distill_kickoff(args.sid, sessions)
        if not k:
            print("no such session: %s" % args.sid)
            return 0
        if args.open:
            try:
                from launch import launch_claude
                if launch_claude(k["cwd"], k["prompt"], "distill-" + args.sid[:8]):
                    print("opened Terminal to distill %s" % args.sid)
                    return 0
            except Exception:
                pass
        print("\ncd %r && claude %r\n" % (k["cwd"], k["prompt"]))
        return 0

    q = formation_queue(sessions)
    env = {"tool_name": "meditate_formation", "success": True,
           "data": {"queued": len(q),
                    "sessions": [{"sid": s.get("session_id"),
                                  "title": (s.get("title") or "")[:60],
                                  "user_msgs": (s.get("counts") or {}).get("user", 0)}
                                 for s in q[:30]]},
           "metadata": {"ledger": LEDGER_PATH,
                        "threshold_user_msgs": SUBSTANTIVE_USER_MSGS},
           "errors": []}
    if args.json:
        print(json.dumps(env, indent=2))
        return 0
    if not q:
        print("Formation queue empty — every substantive session is distilled.")
        return 0
    print("Formation queue — %d session(s) awaiting distillation" % len(q))
    for s in q[:15]:
        print("  %s  %3du  %s" % (str(s.get("session_id"))[:12],
                                  (s.get("counts") or {}).get("user", 0),
                                  (s.get("title") or "(untitled)")[:60]))
    print("\n  meditate distill <sid>          # agent kickoff for one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
