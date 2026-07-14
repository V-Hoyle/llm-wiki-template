#!/usr/bin/env bash
# Cursor postToolUse hook shim: reminds the agent to update wiki docs when it
# edits a tracked source file. Thin wrapper over the versioned python logic in
# the wiki repo. Fails open (no output) if the wiki is absent.
input="$(cat)"
WIKI_ROOT="${WIKI_ROOT:-$HOME/.cursor/llm-wiki}"
HOOK_PY="${WIKI_ROOT}/scripts/cursor_doc_deps_hook.py"
[ -f "$HOOK_PY" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
printf '%s' "$input" | WIKI_ROOT="$WIKI_ROOT" SRC_TLD="${SRC_TLD:-$HOME/src}" python3 "$HOOK_PY" 2>/dev/null || exit 0
