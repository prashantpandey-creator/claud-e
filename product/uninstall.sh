#!/usr/bin/env bash
# Remove meditate's wiring. Your sessions, memories and readings stay.
set -euo pipefail
SKILLS="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
BIN="${MEDITATE_BIN:-$HOME/.local/bin}"

removed=0
if [ -L "$SKILLS/meditate" ]; then rm -f "$SKILLS/meditate"; echo "  [ok]  skill link removed"; removed=1; fi
if [ -L "$BIN/meditate" ];    then rm -f "$BIN/meditate";    echo "  [ok]  command removed"; removed=1; fi
[ "$removed" = 1 ] || echo "  nothing to remove"

echo
echo "  Left alone on purpose: ~/.meditation/ (your readings and continuation"
echo "  chats) and every transcript. This script never deletes your work."
