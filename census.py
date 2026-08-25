"""census — how many people run this, and are they upgrading. Nothing else.

The question a maintainer actually needs answered is "is anyone using this,
and does the new version stick?" That question needs a COUNT, not a customer
list. So this sends five fields and no sixth:

    install_id   a random UUID made on this machine, once
    version      meditate's version
    os           e.g. "darwin 26"          (no hostname, no build string)
    python       e.g. "3.14"
    day          the date, so a day can be counted once

What is deliberately NOT here, and must never be added: username, hostname,
paths, project names, goal titles, memory statements, counts of anything you
have written, IP-derived location. If a field would let someone reconstruct
what you are working on, it does not belong in a census.

`install_id` makes this PSEUDONYMOUS, not anonymous — it is a stable random
number, so repeat visits from one machine can be counted as one machine. That
is the whole reason it exists, and calling it "anonymous" would be a lie of
the kind this tool is built to stop telling. It is a coin flip's worth of
identity: it cannot be traced to a person, and deleting one file resets it.

Off is one word, and the tool says so at install:

    meditate census off          never send anything again
    meditate census show         print the exact bytes it would send
    meditate census              is it on, and what was last sent

It is inert until an endpoint is configured (MEDITATE_CENSUS_URL, or
census-url in the meditation dir): with no endpoint there is nowhere to send
and nothing is sent. It fails silent, times out in 2 seconds, and can never
block or slow the tool.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
import uuid
from typing import Any, Dict, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)
import paths

DIR = paths.MEDITATION_DIR
ID_FILE = os.path.join(DIR, "install-id")
OFF_FILE = os.path.join(DIR, "census-off")
URL_FILE = os.path.join(DIR, "census-url")
LAST_FILE = os.path.join(DIR, "census-last.json")

# once a day at most; a ping per session would be a behaviour log, not a count
MIN_GAP_S = 24 * 60 * 60
TIMEOUT_S = 2.0


def endpoint() -> str:
    u = os.environ.get("MEDITATE_CENSUS_URL")
    if u:
        return u.strip()
    try:
        with open(URL_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def enabled() -> bool:
    """Off wins, always: the env var, the file, or no endpoint at all."""
    if os.environ.get("MEDITATE_CENSUS", "").strip() in ("0", "off", "no"):
        return False
    if os.path.exists(OFF_FILE):
        return False
    return bool(endpoint())


def install_id() -> str:
    """A random number this machine keeps. Never derived from anything about
    you — not the hostname, not the username, not the MAC address, all of
    which would make it re-identifiable."""
    try:
        with open(ID_FILE) as f:
            v = f.read().strip()
        if len(v) >= 8:
            return v
    except OSError:
        pass
    v = uuid.uuid4().hex
    try:
        os.makedirs(DIR, exist_ok=True)
        with open(ID_FILE, "w") as f:
            f.write(v + "\n")
    except OSError:
        pass
    return v


def _version() -> str:
    try:
        with open(os.path.join(SKILL_DIR, "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "unknown"


def payload() -> Dict[str, Any]:
    """Exactly what would be sent. `meditate census show` prints this, so the
    claim 'five fields, no sixth' is checkable rather than promised."""
    return {
        "install_id": install_id(),
        "version": _version(),
        # major only: "darwin 26", never the full build, which narrows a machine
        "os": "%s %s" % (platform.system().lower(),
                         platform.release().split(".")[0]),
        "python": "%d.%d" % sys.version_info[:2],
        "day": time.strftime("%Y-%m-%d"),
    }


def _last() -> Dict[str, Any]:
    try:
        with open(LAST_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def due(now: Optional[float] = None) -> bool:
    now = now if now is not None else time.time()
    last = _last()
    if not last:
        return True
    # a new version is worth a ping even inside the daily gap: version
    # adoption is the one number that answers "did the release land"
    if last.get("payload", {}).get("version") != _version():
        return True
    return (now - float(last.get("at", 0))) >= MIN_GAP_S


def ping(force: bool = False) -> Dict[str, Any]:
    """Send once. Never raises, never blocks longer than TIMEOUT_S."""
    if not enabled():
        return {"sent": False, "why": "off" if not endpoint() or
                os.path.exists(OFF_FILE) else "no endpoint"}
    if not force and not due():
        return {"sent": False, "why": "already counted today"}
    body = payload()
    try:
        import urllib.request
        req = urllib.request.Request(
            endpoint(), data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            code = r.status
    except Exception as e:                                   # noqa: BLE001
        # A census that can break the tool is worse than no census.
        return {"sent": False, "why": str(e)[:120]}
    try:
        os.makedirs(DIR, exist_ok=True)
        with open(LAST_FILE, "w") as f:
            json.dump({"at": time.time(), "code": code, "payload": body}, f)
    except OSError:
        pass
    return {"sent": True, "code": code, "payload": body}


def turn(on: bool) -> str:
    os.makedirs(DIR, exist_ok=True)
    if on:
        try:
            os.remove(OFF_FILE)
        except OSError:
            pass
        return "census on" + ("" if endpoint() else " (no endpoint set — inert)")
    with open(OFF_FILE, "w") as f:
        f.write("off\n")
    return "census off — nothing will be sent again"


# ---------------------------------------------------------------- receiver

def serve(port: int = 8900, out: str = "") -> int:
    """The other end, so the loop is testable and self-hostable.

    Appends one line per ping and counts uniques. Deliberately dumb: no
    database, no cookies, no IP logging — an IP is the identifier this whole
    design is avoiding, and writing it down here would undo the rest.
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer
    path = out or os.path.join(DIR, "census.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path != "/counts":
                return self._json(404, {"ok": False})
            self._json(200, counts(path))

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", "0"))
                row = json.loads(self.rfile.read(n) or b"{}")
            except Exception:                                # noqa: BLE001
                return self._json(400, {"ok": False})
            keep = {k: str(row.get(k, ""))[:64]
                    for k in ("install_id", "version", "os", "python", "day")}
            keep["at"] = int(time.time())
            with open(path, "a") as f:
                f.write(json.dumps(keep) + "\n")
            self._json(200, {"ok": True})

    srv = HTTPServer(("127.0.0.1", port), H)
    print("census receiver on 127.0.0.1:%d -> %s" % (port, path))
    srv.serve_forever()
    return 0


def counts(path: str = "") -> Dict[str, Any]:
    """installs, active machines, version split — from the raw log."""
    path = path or os.path.join(DIR, "census.jsonl")
    seen, days, versions = set(), {}, {}
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:                            # noqa: BLE001
                    continue
                iid = r.get("install_id", "")
                if not iid:
                    continue
                seen.add(iid)
                days.setdefault(r.get("day", "?"), set()).add(iid)
                versions[r.get("version", "?")] = \
                    versions.get(r.get("version", "?"), 0) + 1
    except OSError:
        pass
    return {
        "installs": len(seen),
        "active_by_day": {d: len(v) for d, v in sorted(days.items())[-30:]},
        "versions": dict(sorted(versions.items(), key=lambda kv: -kv[1])),
    }


def main(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="How many run this. Nothing else.")
    ap.add_argument("cmd", nargs="?", default="status",
                    choices=["status", "show", "on", "off", "ping", "serve",
                             "counts"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        return serve(a.port)
    if a.cmd == "counts":
        print(json.dumps(counts(), indent=2))
        return 0
    if a.cmd in ("on", "off"):
        print("  " + turn(a.cmd == "on"))
        return 0
    if a.cmd == "show":
        print(json.dumps(payload(), indent=2))
        return 0
    if a.cmd == "ping":
        r = ping(force=a.force)
        print(json.dumps(r, indent=2) if a.json else
              ("  sent" if r["sent"] else "  not sent — " + r["why"]))
        return 0

    ep = endpoint()
    last = _last()
    if a.json:
        print(json.dumps({"tool_name": "meditate_census", "success": True,
                          "data": {"enabled": enabled(), "endpoint": ep,
                                   "last_sent": last.get("at"),
                                   "payload": payload()},
                          "metadata": {}, "errors": []}, indent=2))
        return 0
    print("  census   %s" % ("on" if enabled() else "off"))
    print("  endpoint %s" % (ep or "(none set — nothing is sent)"))
    if last.get("at"):
        print("  last     %s" % time.strftime("%Y-%m-%d %H:%M",
                                              time.localtime(last["at"])))
    print("  sends    %s" % ", ".join(sorted(payload())))
    print("  off      meditate census off")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
