"""dashboard — the whole organism on one self-contained HTML page.

One file, zero external assets, written from the same envelopes the CLI
prints: goals (with scope drift), drift-correct loop, stilling, sangama,
formation queue, store census, heartbeat. Dark field, one gold — the same
design law as the product (Sandhyā: black + #E3B140, no cards).

  meditate dashboard          # write ~/.claude/meditation/dashboard.html
  meditate dashboard --open   # and open it in the browser
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

OUT_PATH = os.path.expanduser("~/.claude/meditation/dashboard.html")


def gather() -> Dict[str, Any]:
    import goals as gl
    import report as rp
    import formation as fm
    from ask import _load
    d: Dict[str, Any] = {"generated": time.strftime("%Y-%m-%d %H:%M")}
    d["report"] = rp.compute()
    d["goals"] = gl.scan()
    mems = _load(os.path.expanduser("~/.claude/meditation/nidra_store"))
    active = [m for m in mems if m.get("active")]
    d["store"] = {
        "active": len(active),
        "verified": sum(1 for m in active
                        if m["epistemic"]["evidence_status"] == "machine_checked"),
        "formed": sum(1 for m in active if "commit-fact" in m.get("tags", [])),
    }
    try:
        from sessions import scan_all_projects
        sessions = scan_all_projects(cap=500)["data"]["sessions"]
        d["formation_queue"] = len(fm.formation_queue(sessions))
    except Exception:
        d["formation_queue"] = None
    hb = os.path.expanduser("~/.claude/meditation/heartbeat.log")
    d["heartbeat_h"] = round((time.time() - os.path.getmtime(hb)) / 3600, 1) \
        if os.path.exists(hb) else None
    d["repair_queue"] = os.path.exists(
        os.path.expanduser("~/.claude/meditation/repair-queue.md"))
    return d


def render(d: Dict[str, Any]) -> str:
    e = html.escape
    G, DIM, BG, FG = "#E3B140", "#8a8578", "#0b0a08", "#d8d2c4"

    def bar(pct: float, w: int = 200) -> str:
        return ('<span style="display:inline-block;width:%dpx;height:8px;'
                'background:#1d1a14;vertical-align:middle;border-radius:4px">'
                '<span style="display:block;width:%.0f%%;height:8px;'
                'background:%s;border-radius:4px"></span></span>'
                % (w, max(0, min(100, pct)), G))

    rows: List[str] = []
    for g in d["goals"]:
        widen = ('  <span style="color:%s">scope +%d</span>' % (G, g["scope_delta"])
                 if g.get("scope_delta", 0) > 0 else "")
        nxt = e(g["next"] or "—")
        rows.append(
            '<div style="margin:10px 0"><div style="display:flex;gap:14px;'
            'align-items:center"><span style="width:230px;color:%s">%s</span>%s'
            '<span style="color:%s">%.0f%%</span>'
            '<span style="color:%s">%d/%d</span>%s</div>'
            '<div style="color:%s;font-size:12px;margin:3px 0 0 244px">next: %s</div></div>'
            % (FG, e(g["title"][:44]), bar(g["pct"]), G, g["pct"],
               DIM, g["done"], g["total"], widen, DIM, nxt))

    r = d["report"]
    dr, st, sg = r["drift"], r["stilling"], r["sangama"]
    s = d["store"]

    def stat(label: str, value: str, note: str = "") -> str:
        return ('<div style="min-width:150px"><div style="font-size:26px;color:%s">%s'
                '</div><div style="font-size:12px;color:%s">%s%s</div></div>'
                % (G, e(value), DIM, e(label),
                   ('<br><span style="color:%s">%s</span>' % (DIM, e(note))) if note else ""))

    stats = "".join([
        stat("graded memories", "%d" % s["active"],
             "%.1f%% verified" % (100.0 * s["verified"] / s["active"] if s["active"] else 0)),
        stat("formed by the system", "%d" % s["formed"], "commit-facts"),
        stat("drift caught / repaired", "%d / %d" % (dr["caught"], dr["repaired"]),
             "%d real open" % dr["open_real"]),
        stat("sessions archived", "%d" % st["sessions_archived"],
             "%d continuation chats" % st["continuation_chats"]),
        stat("facts served / warns", "%d / %d" % (sg["facts_served"], sg["collisions_warned"]),
             "at the moment of need"),
        stat("last heartbeat", "%.1f h" % d["heartbeat_h"] if d["heartbeat_h"] is not None
             else "never", "6 h cycle"),
        stat("awaiting distillation", str(d["formation_queue"])
             if d["formation_queue"] is not None else "—", "substantive sessions"),
    ])

    repair = ('<div style="color:%s;margin-top:6px">⚠ repair queue open — '
              'knowledge failed verification</div>' % G) if d["repair_queue"] else ""

    chips = []
    nxt = "meditate go" if (d["repair_queue"] or d["goals"]) else "/meditate"
    for cmd, label in (("meditate", "where am I"),
                       (nxt, "do the next thing"),
                       ("meditate fix", "repair knowledge") if d["repair_queue"] else ("meditate ask \"...\"", "question memory"),
                       ("/meditate", "full stilling pass")):
        chips.append('<code style="border:1px solid #2a2620;border-radius:6px;'
                     'padding:7px 12px;color:%s;font-size:13px">%s'
                     '<span style="color:%s;font-size:11px;margin-left:8px">%s</span></code>'
                     % (G, html.escape(cmd), DIM, html.escape(label)))
    chips_html = "".join(chips)
    return ("""<!doctype html><meta charset="utf-8">
<title>meditate — the organism</title>
<body style="background:%s;color:%s;font:14px/1.5 -apple-system,Helvetica,sans-serif;
margin:0;padding:48px 56px;max-width:960px">
<div style="letter-spacing:.35em;font-size:11px;color:%s">MEDITATE</div>
<div style="font-size:22px;margin:6px 0 2px;color:%s">the organism, at a glance</div>
<div style="font-size:12px;color:%s">generated %s · every number from the graded store, not recall</div>%s
<div style="display:flex;flex-wrap:wrap;gap:26px;margin:30px 0 8px">%s</div>
<div style="letter-spacing:.3em;font-size:11px;color:%s;margin-top:30px">GOALS</div>
%s
<div style="letter-spacing:.3em;font-size:11px;color:#6b6557;margin-top:30px">ACT</div>
<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px">__CHIPS__</div>
<div style="color:%s;font-size:12px;margin-top:22px">checkboxes tick only when work verifies · push only on the owner's go</div>
</body>""" % (BG, FG, "#6b6557", G, DIM, e(d["generated"]), repair, stats,
              "#6b6557", "".join(rows), DIM)).replace("__CHIPS__", chips_html)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="One-page organism dashboard")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = gather()
    page = render(d)
    with open(OUT_PATH, "w") as f:
        f.write(page)
    env = {"tool_name": "meditate_dashboard", "success": True,
           "data": {"path": OUT_PATH, "bytes": len(page),
                    "goals": len(d["goals"])},
           "metadata": {"self_contained": "http" not in page},
           "errors": []}
    if args.json:
        print(json.dumps(env, indent=2))
        return 0
    print("wrote %s (%d bytes, self-contained)" % (OUT_PATH, len(page)))
    if args.open:
        os.system("open '%s'" % OUT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
