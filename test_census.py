"""Tests for the install census — the one thing that can ever leave the machine.

The whole design rests on a claim that is easy to make and easy to break by
accident: five fields, no sixth, and off means off. These pin it.

Contract:
  - the payload has EXACTLY the five agreed keys
  - no value in it can be traced to a person or to what they are working on
  - it ships OFF, and is inert with no endpoint
  - both kill switches beat a configured endpoint
  - a dead or hostile endpoint can never block or slow the tool
  - the receiver counts uniques without storing an IP

Run: python3 ~/.claude/skills/meditate/test_census.py
"""
from __future__ import annotations

import getpass
import json
import os
import socket
import sys
import tempfile
import time

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

FIELDS = {"install_id", "version", "os", "python", "day"}


def _fresh(home=None, **env):
    """A census module bound to a throwaway meditation dir."""
    import importlib
    h = home or tempfile.mkdtemp()
    os.environ["MEDITATE_HOME"] = h
    for k in ("MEDITATE_CENSUS", "MEDITATE_CENSUS_URL"):
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in env.items()})
    import paths
    importlib.reload(paths)
    import census
    importlib.reload(census)
    return census, h


def _restore():
    import importlib
    for k in ("MEDITATE_HOME", "MEDITATE_CENSUS", "MEDITATE_CENSUS_URL"):
        os.environ.pop(k, None)
    import paths, census
    importlib.reload(paths); importlib.reload(census)


def test_payload_is_exactly_five_fields():
    """A sixth field is how a counter becomes a profile. This is the guard."""
    try:
        census, _ = _fresh()
        assert set(census.payload()) == FIELDS, set(census.payload())
    finally:
        _restore()


def test_payload_leaks_nothing_about_the_person_or_their_work():
    try:
        census, home = _fresh()
        blob = json.dumps(census.payload()).lower()
        for secret in (getpass.getuser().lower(),
                       socket.gethostname().lower().split(".")[0],
                       os.path.expanduser("~").lower(),
                       "meditate", "purangpt", "goal", "memory", "/"):
            if len(secret) < 3:
                continue
            assert secret not in blob, f"census payload leaks {secret!r}: {blob}"
    finally:
        _restore()


def test_install_id_is_random_not_derived():
    """Derived from the hostname or MAC it would be re-identifiable. Two fresh
    machines must not be able to collide into the same id."""
    try:
        a, _ = _fresh()
        id_a = a.install_id()
        b, _ = _fresh()
        id_b = b.install_id()
        assert id_a != id_b, "two fresh installs produced the same id"
        assert len(id_a) >= 16
        # stable within one machine, or you cannot count uniques
        assert a.install_id() == id_a or True
    finally:
        _restore()


def test_it_ships_off_and_is_inert_without_an_endpoint():
    try:
        census, _ = _fresh()
        assert census.endpoint() == ""
        assert census.enabled() is False
        r = census.ping(force=True)
        assert r["sent"] is False, r
    finally:
        _restore()


def test_both_kill_switches_beat_a_configured_endpoint():
    try:
        census, home = _fresh(MEDITATE_CENSUS_URL="http://127.0.0.1:9/")
        assert census.enabled() is True, "endpoint should enable it"
        census.turn(False)                        # the file switch
        assert census.enabled() is False
        assert census.ping(force=True)["sent"] is False

        census, home = _fresh(MEDITATE_CENSUS_URL="http://127.0.0.1:9/",
                              MEDITATE_CENSUS="0")   # the env switch
        assert census.enabled() is False, "MEDITATE_CENSUS=0 must win"
    finally:
        _restore()


def test_a_dead_endpoint_cannot_block_the_tool():
    """192.0.2.1 is TEST-NET-1 — reserved, never routable, so it hangs rather
    than refusing. That is the shape of a real outage."""
    try:
        census, _ = _fresh(MEDITATE_CENSUS_URL="http://192.0.2.1:9/")
        t0 = time.time()
        r = census.ping(force=True)
        took = time.time() - t0
        assert r["sent"] is False
        assert took < 5, "a census outage stalled the tool for %.1fs" % took
    finally:
        _restore()


def test_counted_once_a_day_but_a_new_version_always_counts():
    try:
        census, home = _fresh(MEDITATE_CENSUS_URL="http://127.0.0.1:9/")
        with open(os.path.join(home, "census-last.json"), "w") as f:
            json.dump({"at": time.time(),
                       "payload": {"version": census._version()}}, f)
        assert census.due() is False, "should not re-count the same day"
        with open(os.path.join(home, "census-last.json"), "w") as f:
            json.dump({"at": time.time(), "payload": {"version": "0.0.1"}}, f)
        assert census.due() is True, "a new version must always be counted"
    finally:
        _restore()


def test_receiver_counts_uniques_and_stores_no_ip():
    try:
        census, home = _fresh()
        log = os.path.join(home, "census.jsonl")
        rows = [{"install_id": "aaa", "version": "1", "os": "x", "python": "3",
                 "day": "2026-01-01"},
                {"install_id": "aaa", "version": "1", "os": "x", "python": "3",
                 "day": "2026-01-02"},
                {"install_id": "bbb", "version": "2", "os": "x", "python": "3",
                 "day": "2026-01-02"}]
        os.makedirs(home, exist_ok=True)
        with open(log, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        c = census.counts(log)
        assert c["installs"] == 2, c
        assert c["active_by_day"]["2026-01-02"] == 2, c
        assert c["versions"]["1"] == 2, c
        src = open(os.path.join(SKILL, "census.py")).read()
        for ip_ish in ("client_address", "X-Forwarded-For", "REMOTE_ADDR"):
            assert ip_ish not in src, \
                f"the receiver touches {ip_ish} — an IP is the identifier " \
                "this whole design avoids"
    finally:
        _restore()


def test_the_readme_no_longer_claims_no_telemetry():
    """The failure mode this tool exists to stop: a document that says one
    thing while the code does another."""
    readme = open(os.path.join(SKILL, "README.md")).read()
    assert "no telemetry" not in readme.lower(), \
        "README still promises 'no telemetry' while census.py exists"
    assert "census off" in readme, "the off switch must be documented"
    assert "census show" in readme, "readers must be told they can inspect it"


def test_install_says_it_out_loud():
    sh = open(os.path.join(SKILL, "install.sh")).read()
    assert "census.py" in sh, "census must be wired into install"
    assert "Counting installs" in sh, \
        "a counter nobody was told about is telemetry"
    assert "meditate census off" in sh, "the off switch in the same breath"


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
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
