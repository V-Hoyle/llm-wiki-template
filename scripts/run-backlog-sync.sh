#!/usr/bin/env bash
# Weekly backlog sync: branch doc bootstrap + transcript export + index regen.
set -euo pipefail

WIKI_ROOT="${HOME}/.cursor/llm-wiki"
SCRIPTS="${WIKI_ROOT}/scripts"
LOG_DIR="${WIKI_ROOT}/.state/logs"
LOG_FILE="${LOG_DIR}/backlog-sync.log"
SINCE_DAYS="${WIKI_BACKLOG_SINCE_DAYS:-60}"

mkdir -p "${LOG_DIR}"

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) backlog sync (since ${SINCE_DAYS}d) ==="

  python3 "${SCRIPTS}/bootstrap-branches.py" --since-days "${SINCE_DAYS}" 2>&1

  python3 "${SCRIPTS}/export-transcripts.py" \
    --since-days "${SINCE_DAYS}" \
    --workspace Datasembly \
    --min-bytes 2000 2>&1

  python3 "${SCRIPTS}/export-transcripts.py" \
    --since-days "${SINCE_DAYS}" \
    --all-workspaces \
    --min-bytes 8000 2>&1

  python3 "${SCRIPTS}/export-cursor-deliverables.py" --queue-new 2>&1

  python3 "${SCRIPTS}/generate-backlog-index.py" 2>&1

  bash "${SCRIPTS}/run-git-backup.sh" 2>&1 || true

  echo "=== done ==="
} >> "${LOG_FILE}" 2>&1
