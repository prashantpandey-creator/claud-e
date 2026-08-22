#!/usr/bin/env python3
"""Launch continuation chats in new terminal windows; report archive candidates.

Reads the INDEX.md of each meditation session and does two things:
(1) opens a new Claude Code terminal for every live (🟢) thread, with the
    right working directory and kickoff prompt;
(2) reports which SESSIONS are fully settled (every thread ✅) so they're
    ready to archive.

This script never calls `archive_session` itself — it can't: that's an MCP
tool, only callable by a live Claude Code agent, not a standalone Python
process. It only detects and reports; the agent running /meditate does the
actual confirmed archive call (see SKILL.md Phase B4). Rule 0 shape: script
computes the WHAT, agent performs the part that needs the real tool call.
"""
import os, re, sys, subprocess, json, glob
from pathlib import Path

MEDITATION_DIR = os.path.expanduser("~/.claude/meditation/sessions")
CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
FALLBACK_CWD = os.path.expanduser("~/vyasa")


def find_session_cwd(short_session_id: str) -> str:
    """Look up the real working directory a session ran in, from its own
    transcript (same field sessions.py already reads). Falls back to
    FALLBACK_CWD only if no transcript match is found."""
    matches = glob.glob(os.path.join(CLAUDE_PROJECTS_DIR, "*", f"{short_session_id}*.jsonl"))
    for path in matches:
        try:
            with open(path) as f:
                for line in f:
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if o.get("cwd"):
                        return o["cwd"]
        except OSError:
            continue
    print(f"  ⚠️  no cwd found for session {short_session_id}, falling back to {FALLBACK_CWD}")
    return FALLBACK_CWD


def _parse_thread_row(line: str, session_slug: str) -> dict:
    """Parse one '| # | Thread | vṛtti (kind) | Status | Memory |' row.
    Returns None if the line isn't a data row of that shape."""
    if "|" not in line or ("🟢" not in line and "✅" not in line):
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 6:
        return None
    thread_name = parts[2].replace("**", "").strip("* ").strip()
    status_cell = parts[4].strip()
    memory = parts[5].strip()
    return {
        "session": session_slug,
        "thread": thread_name,
        "status": "live" if "🟢" in status_cell else "settled",
        "memory": memory,
    }


def find_threads(session_dir: str = None) -> list:
    """Find every thread (live 🟢 or settled ✅) across meditation sessions."""
    threads = []
    base = session_dir or MEDITATION_DIR
    if not os.path.exists(base):
        return threads

    for session_slug in os.listdir(base):
        index_path = os.path.join(base, session_slug, "INDEX.md")
        if not os.path.exists(index_path):
            continue

        with open(index_path) as f:
            content = f.read()

        for line in content.split("\n"):
            row = _parse_thread_row(line, session_slug)
            if row is None:
                continue

            if row["status"] == "live":
                # Attach the thread's OWN continuation chat. The Memory column
                # usually names it (→ **file.md**); only when it doesn't do we
                # fall back to the first continue*.md — the old behavior gave
                # EVERY live row in a session the same first-file kickoff.
                sdir = os.path.join(base, session_slug)
                target = None
                m = re.search(r"([A-Za-z0-9._-]+\.md)", row.get("memory", ""))
                if m and os.path.exists(os.path.join(sdir, m.group(1))):
                    target = m.group(1)
                else:
                    for fname in sorted(os.listdir(sdir)):
                        if fname.startswith("continue") and fname.endswith(".md"):
                            target = fname
                            break
                if target:
                    chat_path = os.path.join(sdir, target)
                    with open(chat_path) as cf:
                        chat_content = cf.read()
                    marker = "## Start a fresh chat with"
                    prompt_start = chat_content.find(marker)
                    kickoff = ""
                    if prompt_start > 0:
                        tail = chat_content[prompt_start + len(marker):]
                        fence = tail.find("```")
                        if fence != -1:
                            fence_end = tail.find("```", fence + 3)
                            if fence_end > 0:
                                kickoff = tail[fence + 3:fence_end].strip()
                        if not kickoff:
                            # The documented template has NO fence — take the
                            # prose up to the next heading (or EOF).
                            nxt = tail.find("\n## ")
                            kickoff = (tail[:nxt] if nxt > 0 else tail).strip()
                    row["path"] = chat_path
                    row["kickoff"] = kickoff

            threads.append(row)

    return threads


def find_archive_candidates(threads: list) -> list:
    """Sessions where EVERY thread is settled — safe to archive.
    A session with even one live thread is never a candidate."""
    by_session = {}
    for t in threads:
        by_session.setdefault(t["session"], []).append(t)

    candidates = []
    for session_slug, rows in by_session.items():
        if rows and all(r["status"] == "settled" for r in rows):
            candidates.append({
                "session": session_slug,
                "short_id": session_slug.split("-")[0],
                "thread_count": len(rows),
            })
    return candidates


FLEET_MODEL = os.environ.get("MEDITATE_FLEET_MODEL", "sonnet")


def build_launch(cwd: str, kickoff: str, thread_name: str, model: str = ""):
    """Build (kickoff_file, shell_cmd, applescript) — separated so tests can
    verify the command without opening windows.

    The old command was `cat file | claude`: PIPED stdin puts claude in
    non-interactive mode, so the agent ran headless-or-died and the owner got
    "just the terminal". The prompt must be an ARGUMENT — claude "$(cat f)" —
    which starts a real interactive session that stays open. cwd is quoted
    (goal cwds contain spaces: 'vedic puran').
    """
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in thread_name)[:40]
    kickoff_file = f"/tmp/claude-kickoff-{safe_name}.txt"
    with open(kickoff_file, "w") as f:
        f.write(kickoff)
    # Fleet agents run unattended — a permission prompt in an unwatched
    # Terminal is a silent stall (owner: "should run in allow-all ideally").
    # The gate moves into the kickoff TEXT: ship discipline rides in the
    # prompt + the SessionStart hook, not in prompts nobody is there to click.
    import shlex
    mdl = model or FLEET_MODEL
    if not all(c.isalnum() or c in "-._" for c in mdl):
        mdl = "sonnet"
    safe_cwd = cwd if os.path.isdir(cwd) else os.path.expanduser("~")
    # Start a LIVE interactive session. Do NOT pass the prompt as an argument:
    # `claude "prompt"` answers once and EXITS, leaving a bare shell prompt —
    # that is exactly why dispatched agents looked like "only a terminal opened".
    shell_cmd = ("cd %s && clear && echo %s && claude --model %s "
                 "--dangerously-skip-permissions"
                 % (shlex.quote(safe_cwd),
                    shlex.quote("\u2500\u2500 " + safe_name + " \u2500\u2500"), mdl))
    as_escaped = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
    # ...then TYPE the kickoff into that live session so the agent actually
    # receives its instructions and keeps working.
    kick = " ".join(kickoff.split())
    kick_escaped = kick.replace("\\", "\\\\").replace('"', '\\"')
    script = ('tell application "Terminal"\n'
              '  activate\n'
              '  set w to do script "%s"\n'
              '  delay 7\n'
              '  do script "%s" in w\n'
              'end tell' % (as_escaped, kick_escaped))
    return kickoff_file, shell_cmd, script


def launch_claude(cwd: str, kickoff: str, thread_name: str, model: str = "") -> bool:
    """Open a Terminal running a REAL interactive claude on the kickoff."""
    _, _, script = build_launch(cwd, kickoff, thread_name, model)
    try:
        subprocess.run(["osascript", "-e", script], check=True, timeout=40)  # the script itself waits 7s for claude to boot
        return True
    except Exception as e:
        print(f"  osascript error: {e}")
        return False


def launch_all(auto_open: bool = False):
    """Analyze, report archive candidates, and optionally launch live threads."""
    threads = find_threads()
    live = [t for t in threads if t.get("status") == "live" and t.get("kickoff")]
    archive_candidates = find_archive_candidates(threads)

    if not live and not archive_candidates:
        print("🧘 All settled. ✅ Nothing live, nothing to archive.")
        print("\n— powered by Claude —")
        return

    # ═══ ANALYSIS (always shown) ═══
    if live:
        print(f"🧘 Nirodha (Stillness) — {len(live)} live thread(s)\n")
        print(f"{'#':<3} {'Thread':<35} {'Memory':<30} {'Action'}")
        print("-" * 90)
        for i, t in enumerate(live):
            mem = t.get('memory', '-')[:28]
            action = "🔴 OPEN FIRST" if i == 0 else "🟡 Review then open"
            print(f"{i+1:<3} {t['thread']:<35} {mem:<30} {action}")
    else:
        print("🧘 No live threads to open.")

    if archive_candidates:
        print(f"\n📦 {len(archive_candidates)} session(s) fully settled — ready to archive:")
        for c in archive_candidates:
            print(f"   {c['session']} ({c['thread_count']} thread(s) done)")
        print("   This script does NOT archive them — that needs a live agent")
        print("   calling mcp__ccd_session_mgmt__archive_session (with your")
        print("   confirmation) after matching short_id above to a real")
        print("   sessionId via list_sessions. Run /meditate archive, or ask.")

    if not auto_open:
        if live:
            print(f"\n💡 To open live threads: python3 ~/.claude/skills/meditate/launch.py --open")
        print("\n— powered by Claude —")
        return

    # ═══ LAUNCH (--open flag) ═══
    if live:
        print(f"\n🚀 Opening {len(live)} Claude Code session(s)...\n")
        for i, t in enumerate(live):
            print(f"[{i+1}/{len(live)}] {t['thread']}")
            short_id = t["session"].split("-")[0]
            cwd = find_session_cwd(short_id)
            ok = launch_claude(cwd, t["kickoff"], t["thread"])
            status = "✅" if ok else f"❌ (manual: cat {t['path']})"
            print(f"  {status}")
            print()

    print("— powered by Claude —")


if __name__ == "__main__":
    launch_all(auto_open="--open" in sys.argv)
