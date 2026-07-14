#!/usr/bin/env bash
# Verify Notion wiki one-time export files in raw/notion-migration/.
# Content was populated from Notion MCP fetch (2026-06-29).
# Session log row export requires Notion Business+ for query_data_sources SQL.
set -euo pipefail

WIKI_ROOT="${HOME}/.cursor/llm-wiki"
RAW_DIR="${WIKI_ROOT}/raw/notion-migration"
PREFIX="2026-06-29"

EXPECTED=(
  "${PREFIX}-notion-wiki-hub.md"
  "${PREFIX}-notion-index.md"
  "${PREFIX}-notion-wiki-schema.md"
  "${PREFIX}-notion-workspace-repos.md"
  "${PREFIX}-notion-session-log-rolling.md"
  "${PREFIX}-notion-assistant-session-log-schema.md"
)

missing=0
for f in "${EXPECTED[@]}"; do
  if [[ -f "${RAW_DIR}/${f}" ]]; then
    echo "OK  ${f}"
  else
    echo "MISSING ${f}"
    missing=$((missing + 1))
  fi
done

if [[ "${missing}" -gt 0 ]]; then
  echo "=== ${missing} file(s) missing ===" >&2
  exit 1
fi

echo "=== Notion export complete (${#EXPECTED[@]} files) ==="
