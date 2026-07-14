#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/wiki-common.sh
source "${SCRIPT_DIR}/lib/wiki-common.sh"

input="$(cat)"
wiki_ensure_dirs

conversation_id="$(wiki_json_get "${input}" '.conversation_id // .conversationId // empty')"
workspace_roots="$(wiki_json_get "${input}" '.workspace_roots // .workspaceRoots // []')"
user_message="$(wiki_json_get "${input}" '.user_message // .userMessage // .prompt // empty')"
duration_ms="$(wiki_json_get "${input}" '.duration_ms // .durationMs // 0')"

paths_text="${workspace_roots}"
if [[ -z "${paths_text}" ]]; then
  paths_text="$(wiki_json_get "${input}" '.workspace_root // .workspaceRoot // empty')"
fi

topics="$(wiki_topics_from_paths "${paths_text}")"
transcript_path=""

if [[ -n "${conversation_id}" ]]; then
  transcript_path="$(find "${TRANSCRIPTS_ROOT}" -path "*/agent-transcripts/${conversation_id}.jsonl" -type f 2>/dev/null | head -n 1 || true)"
fi

substantive=0

if [[ -n "${user_message}" ]] && [[ ${#user_message} -gt 200 ]]; then
  substantive=1
fi

if [[ -n "${duration_ms}" ]] && [[ "${duration_ms}" =~ ^[0-9]+$ ]] && (( duration_ms > 120000 )); then
  substantive=1
fi

if [[ -n "${transcript_path}" ]] && [[ -f "${transcript_path}" ]]; then
  if grep -Eiq 'decision|runbook|architecture|agents|compass|treasury|airflow|infrastructure|bootstrap|wiki' "${transcript_path}" 2>/dev/null; then
    substantive=1
  fi
  if [[ $(wc -c < "${transcript_path}" | tr -d ' ') -gt 4000 ]]; then
    substantive=1
  fi
fi

if [[ ${substantive} -eq 0 ]]; then
  exit 0
fi

ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
transcript_excerpt=""
if [[ -n "${transcript_path}" ]] && [[ -f "${transcript_path}" ]]; then
  transcript_excerpt="$(tail -c 12000 "${transcript_path}" | wiki_redact_secrets)"
fi

export WIKI_QUEUE_FILE="${QUEUE_FILE}"
export WIKI_TS="${ts}"
export WIKI_CONVERSATION_ID="${conversation_id}"
export WIKI_PATHS_TEXT="${paths_text}"
export WIKI_TOPICS="${topics}"
export WIKI_TRANSCRIPT_PATH="${transcript_path}"
export WIKI_TRANSCRIPT_EXCERPT="${transcript_excerpt}"

python3 <<'PY'
import json
import os
from datetime import datetime, timezone

entry = {
    "ts": os.environ.get("WIKI_TS") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "conversation_id": os.environ.get("WIKI_CONVERSATION_ID", ""),
    "workspace": os.environ.get("WIKI_PATHS_TEXT", ""),
    "topics": [t for t in os.environ.get("WIKI_TOPICS", "").split() if t],
    "transcript_path": os.environ.get("WIKI_TRANSCRIPT_PATH", ""),
    "transcript_excerpt": os.environ.get("WIKI_TRANSCRIPT_EXCERPT", ""),
    "status": "pending",
}

excerpt = os.environ.get("WIKI_TRANSCRIPT_EXCERPT")
if excerpt is None:
    path = entry["transcript_path"]
    if path and os.path.isfile(path):
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 12000))
            excerpt = f.read().decode("utf-8", errors="replace")
        for pattern in ("api_key", "secret", "password", "token"):
            excerpt = excerpt.replace(pattern, "REDACTED")
        entry["transcript_excerpt"] = excerpt

queue_file = os.environ["WIKI_QUEUE_FILE"]
os.makedirs(os.path.dirname(queue_file), exist_ok=True)
with open(queue_file, "a", encoding="utf-8") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
PY

exit 0
