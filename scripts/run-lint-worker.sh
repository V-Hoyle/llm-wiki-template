#!/usr/bin/env bash
# Wrapper for llm-wiki weekly lint worker (launchd + manual runs).
set -euo pipefail

WIKI_ROOT="${HOME}/.cursor/llm-wiki"
SCRIPTS="${WIKI_ROOT}/scripts"
ENV_FILE="${WIKI_ROOT}/.env"
LOG_DIR="${WIKI_ROOT}/.state/logs"
LOG_FILE="${LOG_DIR}/lint-worker.log"

mkdir -p "${LOG_DIR}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
  set +a
fi

cd "${SCRIPTS}"

if [[ ! -d node_modules ]]; then
  echo "Installing worker dependencies..." >> "${LOG_FILE}"
  npm install --silent >> "${LOG_FILE}" 2>&1
fi

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  npm run lint --silent 2>&1
} >> "${LOG_FILE}" 2>&1
