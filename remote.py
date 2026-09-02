"""remote — the read-only remote VIEW (Rung 2). See your brain from your phone.

The safety line, held by design: data flows ONE WAY only.

    your machine  ──POST summary──►  a small page on your server  ──►  your eyes

The machine PUSHES a bounded summary (project shares, goal %, counts, fleet
status). The server STORES the latest and SERVES it read-only behind a secret.
There is deliberately NO path for the server to send anything back that the
machine acts on — push() reads the HTTP status and discards the body. That is
what keeps a remote view from becoming remote control.

What is pushed: a summary only — never a memory statement, transcript line,
file path content, or raw text. Names and numbers, nothing that could leak a
secret. Opt-in: nothing is sent unless you configure an endpoint.

CLI:
  meditate remote push --url https://you.example/ingest --token T   # one push
  meditate remote serve --port 8899 --ingest T --view V             # the server
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")


def snapshot(store_dir: str = STORE_DIR, goals_dir: Optional[str] = None,
             history_path: Optional[str] = None) -> Dict[str, Any]:
    """A bounded SUMMARY safe to leave the machine — names and numbers only.

    Explicitly excludes: memory statements, transcript text, evidence
    excerpts, repair-item text. Anything that could carry a secret stays home.
    """
    from projects import rollup
    from ask import _load

    mems = [m for m in _load(store_dir) if m.get("active")]
    counts = {
        "facts": len(mems),
        "verified": sum(1 for m in mems
                        if m["epistemic"]["evidence_status"] == "machine_checked"),
        "repair": sum(1 for m in mems
                      if m["epistemic"]["evidence_status"] == "unverified"
                      and (m.get("evidence") or m.get("flags"))),
    }
    projs = []
    for r in rollup(store_dir=store_dir, goals_dir=goals_dir,
                    history_path=history_path):
        if not (r["messages"] or r["goals"]):
            continue
        projs.append({          # names + numbers only; open_tasks are goal
            "project": r["project"], "messages": r["messages"],  # milestone
            "sessions": r["sessions"], "facts": r["facts"],      # titles the
            "repair_items": r["repair_items"], "pct": r["pct"],  # owner wrote
            "open_tasks": [t["task"][:80] for t in r["open_tasks"]]})
    return {"generated": time.strftime("%Y-%m-%d %H:%M"),
            "counts": counts, "projects": projs}


def push(url: str, token: str,
         snapshot_fn: Callable[[], Dict[str, Any]] = snapshot) -> Dict[str, Any]:
    """POST the summary. Read ONLY the status — the body is never actioned.

    This function contains no eval/exec/subprocess: the server cannot command
    the machine through its response. That is the one-way guarantee, in code.
    """
    body = json.dumps(snapshot_fn()).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Ingest": token})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            status = getattr(r, "status", 200)
            r.read()                      # drain, then DISCARD — never parsed
        return {"pushed": True, "status": status}
    except Exception as e:
        return {"pushed": False, "error": str(e)[:200]}


# ---- the receiver (runs on the owner's server) ------------------------------

_VIEW = """<!doctype html><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>meditate — remote view</title>
<body style="background:#0b0a08;color:#d8d2c4;font:15px/1.6 -apple-system,sans-serif;margin:0;padding:28px 20px;max-width:640px">
<div style="letter-spacing:.3em;font-size:11px;color:#6b6557">MEDITATE · REMOTE VIEW · READ-ONLY</div>
<div style="font-size:20px;color:#E3B140;margin:6px 0">%(headline)s</div>
<div style="font-size:12px;color:#8a8578">as of %(generated)s · pushed from your machine · nothing here can touch it</div>
%(projects)s
</body>"""


def _render(snap: Dict[str, Any]) -> str:
    c = snap.get("counts", {})
    head = "%d facts · %d to fix" % (c.get("facts", 0), c.get("repair", 0))
    rows = []
    tot = sum(p.get("messages", 0) for p in snap.get("projects", [])) or 1
    for p in snap.get("projects", []):
        share = 100.0 * p.get("messages", 0) / tot
        w = int(min(100, share) * 1.4)
        tasks = "".join('<div style="margin-left:14px;font-size:12.5px;color:#8a8578">↳ %s</div>'
                        % _esc(t) for t in p.get("open_tasks", []))
        rows.append(
            '<div style="margin:12px 0"><div style="color:#E3B140">%s '
            '<span style="color:#8a8578;font-size:12px">%.0f%% · %d facts%s</span></div>'
            '<div style="background:#1d1a14;border-radius:4px;height:7px;width:200px;margin:4px 0">'
            '<div style="background:#E3B140;height:7px;border-radius:4px;width:%dpx"></div></div>%s</div>'
            % (_esc(p.get("project", "")), share, p.get("facts", 0),
               (" · %d to fix" % p["repair_items"]) if p.get("repair_items") else "",
               w, tasks))
    return _VIEW % {"headline": _esc(head), "generated": _esc(snap.get("generated", "—")),
                    "projects": "".join(rows) or "<div>no data yet</div>"}


def _esc(s: Any) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ---- the gist backend (a host you already own: github.com, not a new box) ----
#
# The receiver above needs a server you stand up. This path needs none: it
# writes the same summary-only snapshot into a SECRET gist through the `gh`
# token already on the machine. The gist's URL hash is the view secret; GitHub
# renders the markdown for the phone. Same one-way guarantee: we WRITE out via
# `gh` and read only its exit status — nothing gh returns is eval'd or acted on.

def _md_cell(s: Any) -> str:
    return str(s or "").replace("|", "/").replace("\n", " ").strip()


def render_md(snap: Dict[str, Any]) -> str:
    """The snapshot as GitHub-flavoured markdown — summary only, no raw text."""
    c = snap.get("counts", {})
    out = ["# 🧘 meditate — remote view", "",
           "**%d facts · %d verified · %d to fix**  "
           % (c.get("facts", 0), c.get("verified", 0), c.get("repair", 0)), "",
           "as of %s · pushed from your machine · read-only"
           % _md_cell(snap.get("generated", "—")), "",
           "| project | share | facts | goal | open |",
           "|---|--:|--:|--:|---|"]
    tot = sum(p.get("messages", 0) for p in snap.get("projects", [])) or 1
    for p in snap.get("projects", []):
        share = 100.0 * p.get("messages", 0) / tot
        pct = ("%.0f%%" % p["pct"]) if p.get("pct") is not None else "—"
        goal = pct + ((" · %d to fix" % p["repair_items"])
                      if p.get("repair_items") else "")
        tasks = "; ".join(_md_cell(t) for t in p.get("open_tasks", []))
        out.append("| %s | %.0f%% | %d | %s | %s |"
                   % (_md_cell(p.get("project", "")), share,
                      p.get("facts", 0), goal, tasks))
    return "\n".join(out) + "\n"


def push_gist(gist_id: str, filename: str = "meditate-view.md",
              gh_bin: str = "gh",
              snapshot_fn: Callable[[], Dict[str, Any]] = snapshot) -> Dict[str, Any]:
    """Replace the gist file in place with the current summary. One-way:
    reads only the subprocess return code; gh's output is never actioned."""
    import subprocess
    import tempfile
    md = render_md(snapshot_fn())
    d = tempfile.mkdtemp()
    local = os.path.join(d, filename)
    try:
        with open(local, "w") as f:
            f.write(md)
        r = subprocess.run([gh_bin, "gist", "edit", gist_id, "-f", filename, local],
                           capture_output=True, text=True, timeout=30)
        ok = r.returncode == 0
        return {"pushed": ok, "status": r.returncode,
                "error": "" if ok else (r.stderr or r.stdout)[:200]}
    except Exception as e:                       # gh missing / offline / timeout
        return {"pushed": False, "error": str(e)[:200]}
    finally:
        try:
            os.remove(local); os.rmdir(d)
        except OSError:
            pass


def gist_init(gh_bin: str = "gh", filename: str = "meditate-view.md",
              snapshot_fn: Callable[[], Dict[str, Any]] = snapshot) -> Dict[str, Any]:
    """Create the secret gist once and wire remote-config.json to it.
    No new login: uses the gh token already on the machine."""
    import shutil
    import subprocess
    import tempfile
    resolved = shutil.which(gh_bin) or gh_bin
    d = tempfile.mkdtemp()
    local = os.path.join(d, filename)
    try:
        with open(local, "w") as f:
            f.write(render_md(snapshot_fn()))
        r = subprocess.run([resolved, "gist", "create", local,
                            "-d", "meditate — remote view (secret)"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or r.stdout)[:200]}
        url = ""
        for line in (r.stdout + r.stderr).splitlines():
            line = line.strip()
            if line.startswith("https://gist.github.com/"):
                url = line
        if not url:
            return {"ok": False, "error": "could not parse gist url from gh output"}
        gid = url.rsplit("/", 1)[-1]
        cfg = {"backend": "gist", "gist_id": gid, "gh_bin": resolved,
               "filename": filename, "view_url": url}
        cfg_path = os.path.expanduser("~/.claude/meditation/remote-config.json")
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w") as f:
            json.dump(cfg, f, indent=1)
        return {"ok": True, "url": url, "gist_id": gid}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    finally:
        try:
            os.remove(local); os.rmdir(d)
        except OSError:
            pass


def make_receiver(port: int, ingest_token: str, view_secret: str,
                  store_path: str) -> ThreadingHTTPServer:
    """POST /ingest (token) stores the snapshot; GET /?k=secret serves it.
    No other routes exist — there is no way to command a machine from here."""
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            try:
                self.wfile.write(b)
            except BrokenPipeError:
                pass

        def do_POST(self):
            if self.path.split("?")[0] != "/ingest":
                self._send(404, "no"); return
            if self.headers.get("X-Ingest") != ingest_token:
                self._send(403, "no"); return
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) or b"{}"
            try:
                json.loads(raw)                       # validate; store verbatim
                with open(store_path, "wb") as f:
                    f.write(raw)
                self._send(200, json.dumps({"ok": True}), "application/json")
            except Exception:
                self._send(400, json.dumps({"ok": False}), "application/json")

        def do_GET(self):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            if q.get("k", [""])[0] != view_secret:
                self._send(403, "forbidden"); return
            try:
                snap = json.load(open(store_path))
            except Exception:
                snap = {"counts": {}, "projects": []}
            self._send(200, _render(snap))

        def log_message(self, *a):
            pass

    return ThreadingHTTPServer(("0.0.0.0", port), H)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="meditate remote", description="Read-only remote view")
    sub = ap.add_subparsers(dest="cmd")
    pp = sub.add_parser("push"); pp.add_argument("--url", required=True)
    pp.add_argument("--token", required=True); pp.add_argument("--json", action="store_true")
    sub.add_parser("auto")        # push from saved config (heartbeat calls this)
    sub.add_parser("gist-init")   # create the secret gist host + wire config
    sp = sub.add_parser("serve"); sp.add_argument("--port", type=int, default=8899)
    sp.add_argument("--ingest", required=True); sp.add_argument("--view", required=True)
    sp.add_argument("--store", default=os.path.expanduser("~/meditate-remote-latest.json"))
    args = ap.parse_args(argv)

    if args.cmd == "gist-init":
        r = gist_init()
        print(r["url"] if r["ok"] else "failed: " + r.get("error", ""))
        return 0 if r["ok"] else 1
    if args.cmd == "auto":
        cfg_path = os.path.expanduser("~/.claude/meditation/remote-config.json")
        try:
            cfg = json.load(open(cfg_path))
        except Exception:
            print("no remote-config.json — remote view not configured (opt-in)")
            return 0
        if cfg.get("backend") == "gist":
            r = push_gist(cfg["gist_id"], filename=cfg.get("filename", "meditate-view.md"),
                          gh_bin=cfg.get("gh_bin", "gh"))
            print(("pushed to " + cfg.get("view_url", "gist")) if r["pushed"]
                  else "push skipped (offline?): " + r.get("error", ""))
            return 0            # heartbeat step: an outbound push never fails the pass
        r = push(cfg["url"], cfg["ingest_token"])
        print("pushed" if r["pushed"] else "failed: " + r.get("error", ""))
        return 0 if r["pushed"] else 1
    if args.cmd == "push":
        r = push(args.url, args.token)
        print(json.dumps(r) if args.json else
              ("pushed" if r["pushed"] else "failed: " + r.get("error", "")))
        return 0 if r["pushed"] else 1
    if args.cmd == "serve":
        srv = make_receiver(args.port, args.ingest, args.view, args.store)
        print("remote view receiver on 0.0.0.0:%d — POST /ingest, GET /?k=<view>"
              % args.port)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
