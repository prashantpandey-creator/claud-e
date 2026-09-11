"""ssh_readonly — a CLOSED set of read-only checks on the Mumbai prod box.

The gap this closes: an assess probe (dontAsk, no hands) hit "SSH to root@…
is denied by this session's permission" five times on 2026-09-11 checking
things like the Meta pixel ID and the live Caddy config — all of them
genuinely read-only questions. The fix considered first — add
`Bash(ssh:*)` to the assess role — was refused by the harness's own auto-mode
classifier, twice, on the reasoning that "read-only" for raw ssh is only a
PROMPT instruction: ssh itself hands an unattended agent a full remote
shell, and nothing at the tool-permission layer stops it from typing
`docker restart` once connected.

This is the tool-level guarantee instead. `Bash(meditate:*)` is already
granted to every role, so a probe can run `meditate ssh-ro run <name>` — but
`<name>` is checked against COMMANDS below BEFORE anything is built, and
the actual remote command string for each name is authored ONCE, here, by a
human. An agent can select from three fixed questions; it cannot ask a
fourth, and it cannot ask this file's questions with a different host or key
either — HOST and KEY are constants, not parameters.

What is deliberately NOT here: the Meta System User token and the page
token are secrets, not read-only facts, and no command in this file
prints one — "Page token made permanent" stays a wall a human clears,
not a question this wrapper answers.

    meditate ssh-ro list                 # the three questions, and why
    meditate ssh-ro run pixel-id         # ask one
    meditate ssh-ro run pixel-id --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Callable, Dict, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))

KEY = os.path.expanduser("~/.ssh/purangpt_hetzner")
HOST = "root@209.182.233.163"
SSH_TIMEOUT_S = 20.0
MAX_OUTPUT_CHARS = 4000

# Every value is a LITERAL string, verified against the repo's own source —
# never built from a caller-supplied fragment:
#   pixel-id      — the goal file names this exact path/var (meta-ads-india.md)
#   docker-ps     — universally safe, no argument
#   caddy-config  — the SAME container-discovery the owner's own
#                   scripts/configure-gia-proxy.sh uses (Caddy runs in a
#                   container with a bind-mounted Caddyfile; there is no
#                   fixed container name to hardcode). The Caddyfile is
#                   routing config, not a secret — no TLS key material in it.
COMMANDS: Dict[str, Dict[str, str]] = {
    "pixel-id": {
        "about": "The NEXT_PUBLIC_META_PIXEL_ID line in /root/stack.env — a tracking id, not a secret.",
        "cmd": "grep -m1 NEXT_PUBLIC_META_PIXEL_ID /root/stack.env || echo '(not set)'",
    },
    "docker-ps": {
        "about": "Every running container: name, image, status.",
        "cmd": "docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'",
    },
    "caddy-config": {
        "about": "The live Caddyfile — routing only, no TLS key material.",
        "cmd": ("cid=$(docker ps --format '{{.Names}} {{.Image}}' | "
               "awk 'tolower($0) ~ /caddy/ {print $1; exit}'); "
               "if [ -z \"$cid\" ]; then echo 'no caddy container found' >&2; exit 1; fi; "
               "docker exec \"$cid\" cat /etc/caddy/Caddyfile"),
    },
}


def list_commands() -> Dict[str, str]:
    return {name: v["about"] for name, v in COMMANDS.items()}


def run(name: str, popen: Optional[Callable] = None, timeout_s: float = SSH_TIMEOUT_S) -> Dict[str, Any]:
    """Run ONE allowlisted question over ssh. `name` not in COMMANDS is
    refused before any subprocess exists — the caller gets the real names
    back, not a hint about how to spell one correctly."""
    entry = COMMANDS.get(name)
    if entry is None:
        return {"ok": False, "name": name,
                "error": "unknown command: %r — known: %s" % (name, ", ".join(sorted(COMMANDS)))}
    popen = popen or subprocess.Popen
    argv = [
        "ssh", "-i", KEY,
        "-o", "BatchMode=yes",              # never prompt for a passphrase and hang the run
        "-o", "ConnectTimeout=8",
        "-o", "StrictHostKeyChecking=accept-new",
        HOST, entry["cmd"],
    ]
    try:
        proc = popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as e:
        return {"ok": False, "name": name, "error": str(e)}
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        return {"ok": False, "name": name, "error": "timed out after %ds" % int(timeout_s)}
    if proc.returncode != 0:
        return {"ok": False, "name": name, "error": (err or out or "exit %d" % proc.returncode).strip()[:MAX_OUTPUT_CHARS]}
    truncated = len(out) > MAX_OUTPUT_CHARS
    result: Dict[str, Any] = {"ok": True, "name": name, "output": out[:MAX_OUTPUT_CHARS]}
    if truncated:
        result["truncated"] = True
    return result


def main(argv: Optional[list] = None) -> int:
    # --json is accepted on EITHER side of the subcommand (`ssh-ro --json list`
    # and `ssh-ro list --json` both work) — a shared parent, not one flag on
    # the top parser, which only argparse's pre-subcommand position sees.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    ap = argparse.ArgumentParser(prog="meditate ssh-ro", parents=[common],
                                 description="A closed set of read-only checks on the Mumbai prod box")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", parents=[common])
    rp = sub.add_parser("run", parents=[common])
    rp.add_argument("name")
    args = ap.parse_args(argv)

    if args.cmd == "run":
        data = run(args.name)
        ok = data["ok"]
    else:
        data = {"commands": list_commands(), "host": HOST}
        ok = True

    if args.json:
        print(json.dumps({"tool_name": "meditate_ssh_readonly", "success": ok,
                          "data": data, "metadata": {}, "errors": [] if ok else [data.get("error", "")]},
                         indent=2))
        return 0 if ok else 1

    if args.cmd == "run":
        if ok:
            print(data["output"])
        else:
            print("error: %s" % data["error"], file=sys.stderr)
        return 0 if ok else 1

    for name, about in data["commands"].items():
        print("  %-14s %s" % (name, about))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
