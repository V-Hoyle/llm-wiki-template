#!/usr/bin/env bash
# Shared helpers for the wiki doc-dependency git hooks.
# Installed into each tracked repo's .git/hooks/ by scripts/install-repo-hooks.sh.
# Design principle: NEVER block or slow a git operation — always fail open.

WIKI_ROOT="${WIKI_ROOT:-$HOME/.cursor/llm-wiki}"
WIKI_DEPS_QUERY="${WIKI_ROOT}/scripts/doc-deps-query.py"

# wiki_deps_report <git-diff-range...>
# Prints a reminder listing wiki docs that depend on the files changed in the range.
wiki_deps_report() {
  command -v python3 >/dev/null 2>&1 || return 0
  [ -f "$WIKI_DEPS_QUERY" ] || return 0
  local repo ns
  repo="$(basename "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null)" || return 0
  [ -n "$repo" ] || return 0
  ns="$(git diff --name-status "$@" 2>/dev/null)" || return 0
  [ -n "$ns" ] || return 0
  printf '%s\n' "$ns" | WIKI_ROOT="$WIKI_ROOT" python3 "$WIKI_DEPS_QUERY" --repo "$repo" --name-status 2>/dev/null || true
}

# wiki_deps_run_local_chain <displaced-hook-path> [args...]
# Runs a pre-existing hook that the installer displaced, preserving prior behavior.
wiki_deps_run_local_chain() {
  local hook="$1"
  shift
  [ -x "$hook" ] && "$hook" "$@"
  return 0
}
