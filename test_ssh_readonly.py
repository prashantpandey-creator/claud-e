"""ssh_readonly — a CLOSED set of read-only checks on the Mumbai prod box,
reachable through `Bash(meditate:*)` (already granted to every role) instead
of a raw `Bash(ssh:*)` grant.

Why this exists: an unattended, dontAsk headless agent with raw `ssh` is a
full remote shell — "read only" would be a prompt instruction, not a tool
guarantee. This wrapper is the tool-level guarantee instead: an agent can
only pass a NAME from a closed dict; the actual remote command string is
authored here, once, and never built from anything an agent supplies. No
test in this file ever really calls ssh — every one injects `popen`.

Run: python3 ~/.claude/skills/meditate/test_ssh_readonly.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

import ssh_readonly as sr  # noqa: E402


class _FakePopen:
    """Records the argv it would have run; never touches a socket."""
    calls = []

    def __init__(self, argv, **kw):
        _FakePopen.calls.append({"argv": argv, "kw": kw})
        self._out, self._err, self._rc = "ok\n", "", 0

    def communicate(self, timeout=None):
        return self._out, self._err

    @property
    def returncode(self):
        return self._rc


def _fake(out="ok\n", err="", rc=0, raise_timeout=False, argv_cmd=None):
    _FakePopen.calls = []

    class P(_FakePopen):
        def communicate(self, timeout=None):
            if raise_timeout:
                raise subprocess.TimeoutExpired(cmd="ssh", timeout=timeout or 0)
            return out, err

        @property
        def returncode(self):
            return rc
    return P


# ---------------------------------------------------------------------------
# the allowlist itself
# ---------------------------------------------------------------------------

def test_the_allowlist_is_closed_and_every_entry_is_a_FIXED_string():
    """Every command is a literal string authored in this file — nothing here
    is built with .format/% against caller input."""
    names = sr.list_commands()
    assert set(names) == {"pixel-id", "docker-ps", "caddy-config"}, names
    for name, about in names.items():
        assert about and isinstance(about, str)
        assert isinstance(sr.COMMANDS[name]["cmd"], str) and sr.COMMANDS[name]["cmd"]


def test_only_a_name_from_the_dict_can_run_never_free_text():
    """The one property that makes this safe to grant via Bash(meditate:*):
    an agent can pass a NAME, never a command. `; rm -rf /` as a "name" must
    be rejected before any subprocess is ever built."""
    calls_before = len(_FakePopen.calls)
    out = sr.run("; rm -rf /", popen=_FakePopen)
    assert out["ok"] is False
    assert "unknown command" in out["error"].lower()
    assert "pixel-id" in out["error"], "the real names are offered, not hidden"
    assert len(_FakePopen.calls) == calls_before, "nothing was ever launched"


# ---------------------------------------------------------------------------
# the ssh invocation itself
# ---------------------------------------------------------------------------

def test_run_targets_EXACTLY_the_one_key_and_host_named_in_the_secrets_doc():
    """Scoped, not a blanket grant: the wrapper cannot be pointed at a
    different host or a different key, because those are constants here,
    not parameters."""
    P = _fake("PIXEL_ID=980984061684331\n")
    out = sr.run("pixel-id", popen=P)
    assert out["ok"] is True, out
    argv = _FakePopen.calls[-1]["argv"]
    assert argv[0] == "ssh"
    assert "-i" in argv and argv[argv.index("-i") + 1] == os.path.expanduser("~/.ssh/purangpt_hetzner")
    assert argv[-2] == "root@209.182.233.163", argv
    assert argv[-1] == sr.COMMANDS["pixel-id"]["cmd"], "the LAST arg is the fixed command, not agent text"
    assert "BatchMode=yes" in " ".join(argv), "must never prompt for a passphrase and hang the run"
    assert out["output"].strip() == "PIXEL_ID=980984061684331"


def test_a_nonzero_exit_is_reported_not_silently_ok():
    P = _fake(out="", err="Permission denied (publickey).\n", rc=255)
    out = sr.run("docker-ps", popen=P)
    assert out["ok"] is False
    assert "Permission denied" in out["error"]


def test_a_timeout_is_reported_as_a_timeout_not_a_hang():
    P = _fake(raise_timeout=True)
    out = sr.run("caddy-config", popen=P, timeout_s=5)
    assert out["ok"] is False
    assert "timed out" in out["error"].lower() and "5" in out["error"]


def test_output_is_capped_so_one_command_cannot_flood_the_agents_context():
    P = _fake(out="x" * 20000)
    out = sr.run("caddy-config", popen=P)
    assert out["ok"] is True
    assert len(out["output"]) <= sr.MAX_OUTPUT_CHARS
    assert out.get("truncated") is True


def test_an_OSError_launching_ssh_is_reported_not_raised():
    class Boom:
        def __init__(self, *a, **k):
            raise OSError("ssh binary not found")
    out = sr.run("docker-ps", popen=Boom)
    assert out["ok"] is False and "ssh binary not found" in out["error"]


# ---------------------------------------------------------------------------
# the CLI (`meditate ssh-ro list|run <name>`)
# ---------------------------------------------------------------------------

def test_cli_list_is_JSON_and_names_every_command():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "ssh_readonly.py"), "list", "--json"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env
    assert set(env["data"]["commands"]) == {"pixel-id", "docker-ps", "caddy-config"}


def test_cli_run_of_an_unknown_name_exits_nonzero_and_never_touches_the_network():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "ssh_readonly.py"), "run", "delete-everything", "--json"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0
    env = json.loads(r.stdout)
    assert env["success"] is False
    assert "unknown command" in json.dumps(env).lower()


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1; print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
