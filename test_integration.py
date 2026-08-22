"""Integration — are the organs actually CONNECTED, on the live system?

Unit suites prove each organ works alone. This suite proves the edges:
every producer's output is readable by its consumer, on the real files,
read-only. An edge that only works in a diagram fails here.

    bridge ──path_index──► coordination.facts_for
    bridge ──journal─────► report (drift), coordination (48h scan)
    bridge ──queue───────► SessionStart nudge, /meditate
    formation ──memories─► sleep grades them ──► ask retrieves them
    coordination ──events► report (sangama efficacy)
    archive ──index──────► report (stilling) + restore path intact
    goals ──files────────► SessionStart nudge + history snapshots
    heartbeat ──log──────► fresh inside 2 intervals

Run: python3 ~/.claude/skills/meditate/test_integration.py
"""
from __future__ import annotations

import json
import os
import sys
import time

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

STORE = os.path.expanduser("~/.claude/meditation/nidra_store")
MED = os.path.expanduser("~/.claude/meditation")
COORD = os.path.expanduser("~/.claude/coordination")


def test_edge_index_to_fact_serving():
    """bridge's path_index must be servable by coordination on a real path."""
    import coordination as co
    idx_path = os.path.join(STORE, "path_index.json")
    assert os.path.exists(idx_path), "bridge never wrote path_index.json"
    with open(idx_path) as f:
        idx = json.load(f)
    mc = [(p, e) for p, v in idx.items() for e in v if e["status"] == "machine_checked"]
    assert mc, "index holds no machine_checked claims"
    path = mc[0][0]
    facts = co.facts_for(path, served=[], store_dir=STORE)
    assert facts, f"facts_for cannot serve an indexed path: {path}"


def test_edge_formed_memories_graded_and_askable():
    """formation -> store -> sleep grade -> ask retrieval, end to end."""
    import ask as ak
    mems = ak._load(STORE)
    formed = [m for m in mems if m.get("active") and "commit-fact" in m.get("tags", [])]
    assert formed, "no formed commit-facts in the live store"
    graded = [m for m in formed
              if m["epistemic"]["evidence_status"] == "machine_checked"]
    assert len(graded) / len(formed) > 0.9, \
        f"formed memories not grading: {len(graded)}/{len(formed)}"
    hits = ak.query("shipped meditate adapter evidence receipts", store_dir=STORE)
    assert hits, "ask cannot retrieve from the live store"


def test_edge_queue_consistency():
    """repair queue exists IFF drift report has failing/flagged items."""
    from coordination import drift_report
    rep = drift_report(STORE)
    actionable = [m for m in rep["memories"]
                  if m.get("failing") or "drifted" in (m.get("flags") or [])]
    qp = os.path.join(MED, "repair-queue.md")
    if actionable:
        assert os.path.exists(qp), "drift exists but no queue file"
        body = open(qp).read()
        assert actionable[0]["id"] in body, "queue does not name the drifted memory"
    else:
        assert not os.path.exists(qp), "clean world but queue file lingers"


def test_edge_events_to_report():
    """coordination's event log must be countable by report."""
    import report as rp
    g = rp._sangama(COORD)
    ev = os.path.join(COORD, "events.jsonl")
    if os.path.exists(ev):
        n = sum(1 for l in open(ev) if l.strip())
        assert g["facts_served"] + g["collisions_warned"] <= n
        assert g["facts_served"] + g["collisions_warned"] > 0, \
            "events exist but report counts zero"


def test_edge_archive_index_matches_files():
    """every ARCHIVE-INDEX entry still has its file (unless later restored)."""
    import archive as ar
    idx = os.path.join(ar.ARCHIVE_ROOT, "ARCHIVE-INDEX.jsonl")
    if not os.path.exists(idx):
        return
    last = {}
    for line in open(idx):
        try:
            r = json.loads(line)
            last[r["sid"]] = r
        except Exception:
            continue
    for sid, r in last.items():
        in_archive = os.path.exists(os.path.join(ar.ARCHIVE_ROOT, r["slug"], sid + ".jsonl"))
        restored = os.path.exists(os.path.join(r["from"], sid + ".jsonl"))
        assert in_archive or restored, f"archived session {sid} lost by both paths"


def test_edge_goals_to_session_start():
    """a live goal with cwd must produce the SessionStart nudge line."""
    import goals as gl
    from coordination import session_start
    gs = [g for g in gl.scan() if g.get("cwd") and g["done"] < g["total"]
          and g["status"] not in ("done", "paused")]
    if not gs:
        return
    out = session_start({"session_id": "integration-probe", "cwd": gs[0]["cwd"]})
    assert "Goal:" in out, f"goal exists for {gs[0]['cwd']} but no nudge"


def test_edge_heartbeat_fresh():
    """The clock must actually tick — threshold DERIVED from the real
    interval, not a hardcoded 12.5h. A malformed plist once left the
    heartbeat dead 13.7h with nothing reporting it; this is that alarm."""
    log = os.path.join(MED, "heartbeat.log")
    assert os.path.exists(log), "heartbeat has never fired"
    import cadence as cd
    interval_h = (cd.current_interval_s() or 6 * 3600) / 3600
    age_h = (time.time() - os.path.getmtime(log)) / 3600
    # macOS launchd does NOT fire while the lid is closed; it fires once on
    # wake. A gap of a few hours after sleep is normal, so the alarm floor is
    # 6h — still well under the 13.7h silent death this test exists to catch.
    limit = max(interval_h * 2 + 0.5, 6.0)
    assert age_h < limit, \
        f"last heartbeat {age_h:.1f}h ago; interval is {interval_h:.0f}h (limit {limit:.1f}h)"


def test_edge_plist_is_valid_xml():
    """The plist must PARSE. A hand-written heredoc embedded raw >> and 2>&1
    inside <string>; launchd tolerated it, every parser choked, and the
    heartbeat silently stopped running for 13.7h."""
    import plistlib
    p = os.path.expanduser("~/Library/LaunchAgents/com.meditate.grade.plist")
    if not os.path.exists(p):
        return
    with open(p, "rb") as f:
        d = plistlib.load(f)          # raises if malformed — that IS the test
    assert d.get("StartInterval", 0) > 0, d


def test_edge_heartbeat_runs_all_silent_stages():
    """Install = consent: grade + archive-empties + dashboard all ride the
    heartbeat; a plist missing a stage silently re-manualizes the product."""
    plist = os.path.expanduser("~/Library/LaunchAgents/com.meditate.grade.plist")
    body = open(plist).read()
    for stage in ("nidra_bridge.py", "archive.py", "dashboard.py"):
        assert stage in body, f"heartbeat missing silent stage: {stage}"


def test_edge_hook_installed_matches_repo():
    """the injected surface must be the version the repo tests."""
    with open(os.path.join(SKILL, "hooks", "meditate-hook.sh"), "rb") as a, \
         open(os.path.expanduser("~/.claude/hooks/meditate-hook.sh"), "rb") as b:
        assert a.read() == b.read(), "installed hook drifted from repo source"


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
