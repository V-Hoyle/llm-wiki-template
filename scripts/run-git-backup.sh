#!/usr/bin/env bash
# Local git backup for ~/.cursor/llm-wiki/ — commits changes; pushes if WIKI_GIT_REMOTE is set.
set -euo pipefail

WIKI_ROOT="${HOME}/.cursor/llm-wiki"
ENV_FILE="${WIKI_ROOT}/.env"
LOG_DIR="${WIKI_ROOT}/.state/logs"
LOG_FILE="${LOG_DIR}/git-backup.log"

mkdir -p "${LOG_DIR}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
  set +a
fi

cd "${WIKI_ROOT}"

if [[ ! -d .git ]]; then
  git init -b main >> "${LOG_FILE}" 2>&1
fi

# Safety: never stage secrets
if git check-ignore -q .env 2>/dev/null; then
  :
else
  echo "git-backup: WARNING — .env is not gitignored" >> "${LOG_FILE}"
fi

git add -A

if git diff --cached --quiet; then
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) — no changes ===" >> "${LOG_FILE}"
  exit 0
fi

MSG="${1:-wiki backup $(date -u +%Y-%m-%dT%H:%M:%SZ)}"
{
  echo "=== ${MSG} ==="
  git commit -m "${MSG}"
} >> "${LOG_FILE}" 2>&1

if [[ -n "${WIKI_GIT_REMOTE:-}" ]]; then
  if ! git remote get-url origin &>/dev/null; then
    git remote add origin "${WIKI_GIT_REMOTE}" >> "${LOG_FILE}" 2>&1
  elif [[ "$(git remote get-url origin)" != "${WIKI_GIT_REMOTE}" ]]; then
    git remote set-url origin "${WIKI_GIT_REMOTE}" >> "${LOG_FILE}" 2>&1
  fi
  git push -u origin main >> "${LOG_FILE}" 2>&1
  echo "pushed to origin" >> "${LOG_FILE}"
fi
