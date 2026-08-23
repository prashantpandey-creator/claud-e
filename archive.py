"""archive — move finished sessions out of the live store, reversibly.

The consolidation gap this closes: /meditate could only archive through the
Desktop app's MCP tool (mcp__ccd_session_mgmt__archive_session). This works at
the FILE level, so it covers every surface that reads ~/.claude/projects —
the `claude` CLI resume picker and the Desktop app alike.

Semantics:
  - dry-run by DEFAULT; --apply to act
  - archive = MOVE <sid>.jsonl + its sidecar dir to
    ~/.claude/meditation/archive/<project-slug>/ and append a restore record
    to ARCHIVE-INDEX.jsonl. Nothing is ever deleted.
  - restore <sid> moves both back exactly where they were
  - a session touched in the last 24h is NEVER a candidate (it may be live)

CLI:
  python3 archive.py                     # dry-run: empty sessions
  python3 archive.py --older-than 60     # dry-run: also stale ones
  python3 archive.py --apply             # actually move the listed set
  python3 archive.py --restore <sid>     # bring one back
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from typing import Any, Dict, List, Optional

PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")
ARCHIVE_ROOT = os.path.expanduser("~/.claude/meditation/archive")
STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")

MIN_AGE_S = 24 * 3600        # never touch anything this fresh — it may be live
EMPTY_MAX_USER_MSGS = 1      # "empty" = at most one user message...
EMPTY_MAX_BYTES = 20_000     # ...and a tiny transcript


def _user_msgs(path: str) -> int:
    """Count user messages without loading the file (transcripts reach 100 MB)."""
    n = 0
    try:
        with open(path, errors="replace") as f:
            for line in f:
                if '"type": "user"' in line or '"type":"user"' in line:
                    n += 1
                    if n > EMPTY_MAX_USER_MSGS:
                        break
    except OSError:
        return 999
    return n


def candidates(projects_root: str = PROJECTS_ROOT, empty_only: bool = True,
               older_than_days: Optional[int] = None) -> List[Dict[str, Any]]:
    """Sessions safe to archive, oldest first. Never anything <24h old."""
    out = []
    now = time.time()
    if not os.path.isdir(projects_root):
        return out
    for slug in sorted(os.listdir(projects_root)):
        pdir = os.path.join(projects_root, slug)
        if not os.path.isdir(pdir):
            continue
        for fn in os.listdir(pdir):
            if not fn.endswith(".jsonl"):
                continue
            p = os.path.join(pdir, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            age_s = now - st.st_mtime
            if age_s < MIN_AGE_S:
                continue
            sid = fn[:-6]
            row = {"sid": sid, "slug": slug, "path": p, "bytes": st.st_size,
                   "age_days": round(age_s / 86400, 1)}
            if st.st_size < EMPTY_MAX_BYTES and _user_msgs(p) <= EMPTY_MAX_USER_MSGS:
                row["reason"] = "empty"
                out.append(row)
            elif not empty_only and older_than_days is not None \
                    and age_s > older_than_days * 86400:
                row["reason"] = "older than %dd" % older_than_days
                out.append(row)
    return sorted(out, key=lambda r: -r["age_days"])


def _retarget_evidence(old_path: str, new_path: str,
                       store_dir: Optional[str] = None) -> int:
    """Point graded evidence at a transcript's NEW location after a move.

    Without this, archiving a non-empty session breaks its memory's evidence
    source, the next sleep pass demotes it to unverified, and the drift alert
    reports noise we caused ourselves. The store must follow the file.
    """
    store_dir = store_dir or STORE_DIR      # resolve at CALL time, not def time
    mp = os.path.join(store_dir, "memories.jsonl")
    if not os.path.exists(mp):
        return 0
    changed = 0
    out_lines = []
    with open(mp) as f:
        for line in f:
            if old_path not in line:
                out_lines.append(line)
                continue
            try:
                m = json.loads(line)
                for ev in m.get("evidence", []):
                    if ev.get("source") == old_path:
                        ev["source"] = new_path
                        changed += 1
                out_lines.append(json.dumps(m) + "\n")
            except Exception:
                out_lines.append(line)
    if changed:
        with open(mp + ".tmp", "w") as f:
            f.writelines(out_lines)
        os.replace(mp + ".tmp", mp)
        try:
            with open(os.path.join(store_dir, "journal.jsonl"), "a") as f:
                f.write(json.dumps({"event": "archive.retarget",
                                    "from": old_path, "to": new_path,
                                    "rows": changed,
                                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}) + "\n")
        except OSError:
            pass
    return changed


def _archive_one(row: Dict[str, Any], archive_root: str) -> None:
    dst_dir = os.path.join(archive_root, row["slug"])
    os.makedirs(dst_dir, exist_ok=True)
    dst_jsonl = os.path.join(dst_dir, row["sid"] + ".jsonl")
    shutil.move(row["path"], dst_jsonl)
    _retarget_evidence(row["path"], dst_jsonl)
    sidecar = os.path.join(os.path.dirname(row["path"]), row["sid"])
    if os.path.isdir(sidecar):
        shutil.move(sidecar, os.path.join(dst_dir, row["sid"]))
    with open(os.path.join(archive_root, "ARCHIVE-INDEX.jsonl"), "a") as f:
        f.write(json.dumps({"sid": row["sid"], "slug": row["slug"],
                            "from": os.path.dirname(row["path"]),
                            "bytes": row["bytes"], "reason": row["reason"],
                            "archived_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}) + "\n")


def run(projects_root: str = PROJECTS_ROOT, archive_root: str = ARCHIVE_ROOT,
        empty_only: bool = True, older_than_days: Optional[int] = None,
        apply: bool = False, store_dir: Optional[str] = None) -> Dict[str, Any]:
    cands = candidates(projects_root, empty_only, older_than_days)
    archived = 0
    errors = []
    if apply:
        # _retarget_evidence rewrites memories.jsonl; take the SAME lock the
        # grade pass holds, or a concurrent grade + this archive interleave
        # two full rewrites and one silently drops the other's memories.
        import fcntl
        sd = store_dir or STORE_DIR
        os.makedirs(sd, exist_ok=True)
        _lk = open(os.path.join(sd, ".grade.lock"), "w")
        try:
            fcntl.flock(_lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _lk.close()
            return {"would_archive": len(cands), "archived": 0,
                    "bytes_reclaimed": 0, "candidates": cands,
                    "errors": [{"skip": "a grade pass holds the store lock; try again"}]}
        for row in cands:
            try:
                _archive_one(row, archive_root)
                archived += 1
            except Exception as e:
                errors.append({"sid": row["sid"], "error": str(e)})
        _lk.close()
    return {"would_archive": len(cands), "archived": archived,
            "bytes_reclaimed": sum(r["bytes"] for r in cands) if apply else 0,
            "candidates": cands, "errors": errors}


def restore(sid: str, archive_root: str = ARCHIVE_ROOT) -> Dict[str, Any]:
    idx = os.path.join(archive_root, "ARCHIVE-INDEX.jsonl")
    rec = None
    if os.path.exists(idx):
        with open(idx) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("sid") == sid:
                    rec = r                       # last record wins
    if not rec:
        return {"restored": False, "reason": "sid not in ARCHIVE-INDEX"}
    src = os.path.join(archive_root, rec["slug"], sid + ".jsonl")
    if not os.path.exists(src):
        return {"restored": False, "reason": "archived file missing"}
    os.makedirs(rec["from"], exist_ok=True)
    back = os.path.join(rec["from"], sid + ".jsonl")
    shutil.move(src, back)
    _retarget_evidence(src, back)
    side = os.path.join(archive_root, rec["slug"], sid)
    if os.path.isdir(side):
        shutil.move(side, os.path.join(rec["from"], sid))
    return {"restored": True, "to": rec["from"]}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate archive", description="Archive finished sessions, reversibly")
    ap.add_argument("--apply", action="store_true", help="actually move (default: dry-run)")
    ap.add_argument("--older-than", type=int, default=None, metavar="DAYS",
                    help="also archive non-empty sessions older than DAYS")
    ap.add_argument("--restore", metavar="SID", help="restore an archived session")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.restore:
        data = restore(args.restore)
    else:
        data = run(empty_only=args.older_than is None,
                   older_than_days=args.older_than, apply=args.apply)

    env = {"tool_name": "meditate_archive", "success": True, "data": data,
           "metadata": {"projects_root": PROJECTS_ROOT, "archive_root": ARCHIVE_ROOT,
                        "dry_run": not args.apply and not args.restore},
           "errors": data.get("errors", [])}
    if args.json:
        print(json.dumps(env, indent=2))
        return 0
    if args.restore:
        print("restored" if data.get("restored") else
              "NOT restored: %s" % data.get("reason"))
        return 0
    mode = "ARCHIVED" if args.apply else "would archive (dry-run — add --apply)"
    print("%s: %d session(s)" % (mode, data["would_archive"]))
    for r in data["candidates"]:
        print("  %-14s %8dB  %6.1fd  %-10s %s" %
              (r["sid"][:12], r["bytes"], r["age_days"], r["reason"], r["slug"]))
    if data["errors"]:
        for e in data["errors"]:
            print("  ERROR %s: %s" % (e["sid"], e["error"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
