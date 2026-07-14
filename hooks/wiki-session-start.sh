#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/wiki-common.sh
source "${SCRIPT_DIR}/lib/wiki-common.sh"

input="$(cat)"

if [[ ! -f "${WIKI_INDEX}" ]]; then
  exit 0
fi

workspace_roots="$(wiki_json_get "${input}" '.workspace_roots // .workspaceRoots // []')"
conversation_id="$(wiki_json_get "${input}" '.conversation_id // .conversationId // empty')"

paths_text="${workspace_roots}"
if [[ -z "${paths_text}" ]]; then
  paths_text="$(wiki_json_get "${input}" '.workspace_root // .workspaceRoot // empty')"
fi

topics="$(wiki_topics_from_paths "${paths_text}")"
articles="$(wiki_find_articles_for_topics "${topics}")"

context="# Work Brain (local wiki)\n\n"
context+="Wiki: ${WIKI_ROOT}\n"
context+="Matched topics: ${topics}\n"
if [[ -n "${conversation_id}" ]]; then
  context+="Conversation: ${conversation_id}\n"
fi
context+="\n"

char_budget=8000
char_used=0
article_count=0
max_articles=5

while IFS= read -r article; do
  [[ -z "${article}" ]] && continue
  [[ ${article_count} -ge ${max_articles} ]] && break

  rel_path="${article#${WIKI_ROOT}/}"
  content="$(head -c 2000 "${article}" 2>/dev/null || true)"
  [[ -z "${content}" ]] && continue

  block="## ${rel_path}\n\n${content}\n\n---\n\n"
  block_len=${#block}

  if (( char_used + block_len > char_budget )); then
    break
  fi

  context+="${block}"
  char_used=$((char_used + block_len))
  article_count=$((article_count + 1))
done <<< "${articles}"

if [[ ${article_count} -eq 0 ]]; then
  index_excerpt="$(head -c 1500 "${WIKI_INDEX}" 2>/dev/null || true)"
  if [[ -n "${index_excerpt}" ]]; then
    context+="## wiki/index.md (excerpt)\n\n${index_excerpt}\n"
  else
    exit 0
  fi
fi

python3 -c '
import json, sys
context = sys.stdin.read()
print(json.dumps({"additional_context": context}))
' <<< "$(printf '%b' "${context}")"

exit 0
