#!/usr/bin/env bash
# Install (or remove) the wiki doc-dependency git hooks into every tracked repo
# found under a top-level directory.
#
# Usage:
#   install-repo-hooks.sh [--tld ~/src] [--uninstall] [--dry-run]
#
# For each immediate subdirectory of --tld that is a git repo AND whose name is
# listed in the wiki's schema/topics.json sourceRepos, it installs post-commit,
# post-merge, and post-checkout hooks into that repo's git hooks dir. Existing
# hooks are preserved by moving them to <hook>.local and chaining to them.
# Idempotent and marker-guarded; --uninstall reverses it and restores backups.
set -euo pipefail

WIKI_ROOT="${WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SRC_HOOKS="${WIKI_ROOT}/hooks/repo-hooks"
TOPICS_JSON="${WIKI_ROOT}/schema/topics.json"
EVENTS=(post-commit post-merge post-checkout)
MARKER=">>> wiki-doc-deps hook >>>"

TLD="${HOME}/src"
UNINSTALL=0
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tld) TLD="${2:?}"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
TLD="${TLD/#\~/$HOME}"

command -v python3 >/dev/null 2>&1 || { echo "python3 required" >&2; exit 1; }
[[ -f "$TOPICS_JSON" ]] || { echo "No topics.json at $TOPICS_JSON" >&2; exit 1; }

# Tracked repo names (sourceRepos across all topics).
mapfile -t REPOS < <(python3 -c "
import json
d=json.load(open('${TOPICS_JSON}'))
s=set()
for t in d.get('topics',{}).values():
    s.update(t.get('sourceRepos',[]) or [])
print('\n'.join(sorted(s)))
")

say() { [[ $DRY_RUN -eq 1 ]] && echo "[dry-run] $*" || echo "$*"; }
run() { [[ $DRY_RUN -eq 1 ]] && echo "[dry-run] $*" || eval "$*"; }

hooks_dir_for() {
  # Absolute git hooks dir for a repo working tree (respects core.hooksPath/worktrees).
  local repo_dir="$1" hd
  hd="$(git -C "$repo_dir" rev-parse --git-path hooks 2>/dev/null)" || return 1
  case "$hd" in /*) : ;; *) hd="$repo_dir/$hd" ;; esac
  echo "$hd"
}

is_managed() { [[ -f "$1" ]] && grep -q "$MARKER" "$1" 2>/dev/null; }

install_repo() {
  local repo_dir="$1" name hd
  name="$(basename "$repo_dir")"
  hd="$(hooks_dir_for "$repo_dir")" || { echo "  skip $name (not a git repo)"; return; }
  run "mkdir -p '$hd'"
  run "cp '$SRC_HOOKS/_wiki-doc-deps-common.sh' '$hd/_wiki-doc-deps-common.sh'"
  for ev in "${EVENTS[@]}"; do
    local dst="$hd/$ev"
    if [[ -f "$dst" ]] && ! is_managed "$dst"; then
      if [[ ! -f "$dst.local" ]]; then
        say "  $name: backing up existing $ev -> $ev.local (chained)"
        run "mv '$dst' '$dst.local'"; run "chmod +x '$dst.local'"
      else
        say "  $name: WARNING existing $ev and $ev.local both present; leaving $ev.local, overwriting $ev"
        run "rm -f '$dst'"
      fi
    fi
    run "cp '$SRC_HOOKS/$ev' '$dst'"
    run "chmod +x '$dst'"
  done
  echo "  installed: $name ($hd)"
}

uninstall_repo() {
  local repo_dir="$1" name hd
  name="$(basename "$repo_dir")"
  hd="$(hooks_dir_for "$repo_dir")" || return
  for ev in "${EVENTS[@]}"; do
    local dst="$hd/$ev"
    if is_managed "$dst"; then
      run "rm -f '$dst'"
      if [[ -f "$dst.local" ]]; then
        say "  $name: restoring $ev.local -> $ev"
        run "mv '$dst.local' '$dst'"
      fi
    fi
  done
  run "rm -f '$hd/_wiki-doc-deps-common.sh'"
  echo "  uninstalled: $name"
}

echo "=== wiki-deps repo hooks ${UNINSTALL:+(uninstall) }in $TLD ==="
count=0
for repo_dir in "$TLD"/*/; do
  repo_dir="${repo_dir%/}"
  name="$(basename "$repo_dir")"
  # only tracked repos that are git repos
  printf '%s\n' "${REPOS[@]}" | grep -qx "$name" || continue
  git -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
  if [[ $UNINSTALL -eq 1 ]]; then uninstall_repo "$repo_dir"; else install_repo "$repo_dir"; fi
  count=$((count+1))
done
echo "=== done: $count tracked repo(s) processed ==="
[[ $UNINSTALL -eq 0 ]] && echo "Hooks call: ${WIKI_ROOT}/scripts/doc-deps-query.py (fails open if wiki is absent)."
