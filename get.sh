#!/bin/bash
# meditate bootstrap — the one-liner.
#
#   curl -fsSL https://meditate.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/prashantpandey-creator/claud-e/main/get.sh | bash
#
# All this does is put the skill where Claude Code looks for skills, then hand
# over to install.sh. It exists because the two-command form asked people to
# type an exact path — and the path is not cosmetic: Claude Code only
# discovers skills under ~/.claude/skills, so a clone one directory off
# produces a tool that installs fine and is never found.
#
# It is short on purpose. Read it before you run it; that is the whole point
# of keeping the bootstrap separate from the installer.
set -euo pipefail

REPO="${MEDITATE_REPO:-https://github.com/prashantpandey-creator/claud-e}"
DEST="${MEDITATE_DEST:-$HOME/.claude/skills/meditate}"
BRANCH="${MEDITATE_BRANCH:-main}"

echo
echo "  meditate — fetching"
echo "  ================================"

if ! command -v git >/dev/null 2>&1; then
    echo "  git is required and was not found."
    echo "  macOS: xcode-select --install     Debian/Ubuntu: apt install git"
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "  python3 is required and was not found."
    exit 1
fi

if [ -d "$DEST/.git" ]; then
    echo "  already installed at ${DEST/#$HOME/~} — updating"
    git -C "$DEST" fetch --quiet origin "$BRANCH"
    # Never clobber local edits: if the checkout is dirty, say so and stop
    # rather than throwing away someone's work to save a step.
    if [ -n "$(git -C "$DEST" status --porcelain)" ]; then
        echo "  [warn] you have local changes there — leaving them alone."
        echo "         git -C ${DEST/#$HOME/~} stash   then re-run to update."
    else
        git -C "$DEST" reset --quiet --hard "origin/$BRANCH"
        echo "  [ok]  updated to $(git -C "$DEST" rev-parse --short HEAD)"
    fi
elif [ -e "$DEST" ]; then
    echo "  $DEST exists but is not a git checkout."
    echo "  Move it aside and re-run, or set MEDITATE_DEST to another path."
    exit 1
else
    mkdir -p "$(dirname "$DEST")"
    echo "  cloning into ${DEST/#$HOME/~}"
    git clone --quiet --depth 1 --branch "$BRANCH" "$REPO" "$DEST"
    echo "  [ok]  fetched $(git -C "$DEST" rev-parse --short HEAD)"
fi

echo
exec bash "$DEST/install.sh"
