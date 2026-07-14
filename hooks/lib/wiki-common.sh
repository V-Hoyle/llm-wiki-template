#!/usr/bin/env bash
# Shared paths and helpers for local LLM wiki hooks.
# Customize wiki_topics_from_paths() for your workspace path keywords.

WIKI_ROOT="${HOME}/.cursor/llm-wiki"
WIKI_INDEX="${WIKI_ROOT}/wiki/index.md"
WIKI_DIR="${WIKI_ROOT}/wiki"
QUEUE_DIR="${WIKI_ROOT}/queue"
QUEUE_FILE="${QUEUE_DIR}/pending.jsonl"
STATE_DIR="${WIKI_ROOT}/.state"
TRANSCRIPTS_ROOT="${HOME}/.cursor/projects"

wiki_ensure_dirs() {
  mkdir -p "${QUEUE_DIR}" "${QUEUE_DIR}/processed" "${STATE_DIR}"
}

wiki_json_get() {
  local input="$1"
  local key="$2"
  if command -v jq >/dev/null 2>&1; then
    echo "${input}" | jq -r "${key} // empty" 2>/dev/null
    return
  fi
  python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    keys = '''${key}'''.strip('.').split('.')
    cur = data
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and k.isdigit():
            cur = cur[int(k)]
        else:
            cur = None
            break
    if cur is None:
        sys.exit(0)
    if isinstance(cur, (dict, list)):
        print(json.dumps(cur))
    else:
        print(cur)
except Exception:
    pass
" <<< "${input}"
}

wiki_topics_from_paths() {
  local paths="$1"
  local topics=""
  local path_lower

  # Customize: path substring → wiki topic folder (see schema/topics.json pathKeywords)
  for keyword_topic in \
    "my-app:my-app" \
    "frontend:my-app" \
    "backend:my-app" \
    "terraform:infra" \
    "k8s:infra" \
    "infrastructure:infra"; do
    local keyword="${keyword_topic%%:*}"
    local topic="${keyword_topic##*:}"
    path_lower=$(echo "${paths}" | tr '[:upper:]' '[:lower:]')
    if echo "${path_lower}" | grep -q "${keyword}"; then
      if ! echo " ${topics} " | grep -q " ${topic} "; then
        topics="${topics} ${topic}"
      fi
    fi
  done

  if [[ -z "${topics// }" ]]; then
    topics="decisions"
  fi

  echo "${topics}" | xargs
}

wiki_find_articles_for_topics() {
  local topics="$1"
  local topic
  local file

  for topic in ${topics}; do
    if [[ -d "${WIKI_DIR}/${topic}" ]]; then
      find "${WIKI_DIR}/${topic}" -maxdepth 1 -name '*.md' -type f 2>/dev/null | sort
    fi
  done
}

wiki_redact_secrets() {
  sed -E \
    -e 's/(api[_-]?key|secret|password|token|authorization)[[:space:]]*[:=][[:space:]]*[^[:space:]"'\''`]+/REDACTED/gi' \
    -e 's/sk-[A-Za-z0-9]{10,}/REDACTED/g' \
    -e 's/Bearer[[:space:]]+[A-Za-z0-9._-]+/Bearer REDACTED/g'
}

wiki_emit_json() {
  python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)))' <<< "$1"
}
