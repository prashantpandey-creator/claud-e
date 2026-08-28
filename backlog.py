"""backlog — the things you have SEEN and chosen not to do yet.

There is a difference between work you have not looked at and work you looked
at and put down, and until now the companion could not tell them apart. Every
item it had, it offered, every time, forever. The only way to make one stop
was to finish it.

That is what makes a tool feel like it is nagging rather than helping: it has
no memory of your judgement. A backlog is that memory. An item you send here
stops being offered — it is not deleted, not finished, and not hidden; it is
waiting, and `meditate backlog` lists it whenever you want to look.

    backlog.add("goal:mila-live:Mila iOS approved and live", "not this week")
    backlog.keys()          -> {"goal:mila-live:..."} for filtering the agenda
    backlog.items()         -> the full rows, for the report page
    backlog.remove(key)     -> bring it back

The key is the item's identity, not its sentence: kind, goal and milestone.
Sentences get reworded every time the wording of a milestone changes, and a
backlog keyed on a sentence quietly forgets everything the moment someone
edits a file.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Set

PATH = os.environ.get("MEDITATE_BACKLOG_FILE") or os.path.expanduser(
    "~/.claude/meditation/.backlog.json")


def key_for(item: Dict[str, str]) -> str:
    """The identity of an agenda item, stable across rewording."""
    kind = (item.get("kind") or "").strip()
    goal = (item.get("goal") or "").strip()
    ms = (item.get("milestone") or "").strip()
    if kind == "goal":
        return "goal:%s:%s" % (goal, ms)
    if kind == "dormant":
        # There are eight of these, one per project you started and left.
        # Keying them all on the kind would mean putting bro-os down also
        # silences flight-postman and the six others — permanently, and
        # invisibly. Identity is the project.
        return "dormant:%s" % (item.get("project") or goal or "").strip()
    # repair and sessions are singular — there is only ever one of each on the
    # list, so the kind IS the identity.
    return kind or "unknown"


def _load() -> Dict[str, Any]:
    try:
        with open(PATH) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        with open(PATH, "w") as f:
            json.dump(d, f, indent=2)
    except OSError:
        pass          # a backlog that cannot be written is not an error


def add(key: str, say: str = "", note: str = "") -> Dict[str, Any]:
    if not key:
        return {"ok": False, "why": "an item with no identity cannot be kept"}
    d = _load()
    d[key] = {"say": say[:300], "note": note[:200], "at": time.time()}
    _save(d)
    return {"ok": True, "key": key, "total": len(d)}


def remove(key: str) -> Dict[str, Any]:
    d = _load()
    if key not in d:
        return {"ok": False, "why": "not in the backlog"}
    d.pop(key)
    _save(d)
    return {"ok": True, "key": key, "total": len(d)}


def keys() -> Set[str]:
    return set(_load().keys())


def items() -> List[Dict[str, Any]]:
    """Newest first — the order you would want to review them in."""
    out = [dict(v, key=k) for k, v in _load().items()]
    out.sort(key=lambda r: r.get("at", 0), reverse=True)
    for r in out:
        r["days"] = round((time.time() - r.get("at", 0)) / 86400.0, 1)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="meditate backlog",
                                 description="What you put down on purpose")
    ap.add_argument("--add", default="")
    ap.add_argument("--remove", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.add:
        r = add(a.add, note=a.note)
    elif a.remove:
        r = remove(a.remove)
    else:
        r = {"ok": True, "items": items()}

    if a.json:
        print(json.dumps({"tool_name": "meditate_backlog",
                          "success": bool(r.get("ok")), "data": r,
                          "metadata": {"path": PATH}, "errors": []}, indent=2))
    elif "items" in r:
        if not r["items"]:
            print("  nothing put down — everything you know about is live")
        for it in r["items"]:
            print("  %-52s  %s days" % ((it.get("say") or it["key"])[:52],
                                        it.get("days", "?")))
    else:
        print("  " + (r.get("key", "") if r.get("ok") else r.get("why", "")))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
