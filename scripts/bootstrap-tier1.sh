#!/usr/bin/env bash
# Bootstrap Tier 1: copy compass-mcp-server + compass docs into raw/ (read-only from repos).
set -euo pipefail

WIKI_ROOT="${HOME}/.cursor/llm-wiki"
RAW="${WIKI_ROOT}/raw"
DATASSEMBLY="${HOME}/Documents/Github/Datasembly"
MCP="${DATASSEMBLY}/compass-mcp-server"
COMPASS="${DATASSEMBLY}/compass"
MANIFEST="${WIKI_ROOT}/schema/bootstrap-manifest.jsonl"
COLLECTED="$(date -u +%Y-%m-%d)"

mkdir -p "${RAW}/compass-mcp" "${RAW}/compass"
: > "${MANIFEST}"

copy_source() {
  local topic="$1"
  local src="$2"
  local dest_name="$3"

  if [[ ! -f "${src}" ]]; then
    echo "SKIP missing: ${src}" >&2
    return 0
  fi

  if echo "${src}" | grep -qiE '\.env|credentials|secret|\.pem|id_rsa'; then
    echo "SKIP secret pattern: ${src}" >&2
    return 0
  fi

  local dest_dir="${RAW}/${topic}"
  local dest="${dest_dir}/${dest_name}"
  local sha
  sha="$(shasum -a 256 "${src}" | awk '{print $1}')"

  mkdir -p "${dest_dir}"

  {
    echo "---"
    echo "Source: ${src}"
    echo "Collected: ${COLLECTED}"
    echo "Published: Unknown"
    echo "SHA256: ${sha}"
    echo "---"
    echo ""
    cat "${src}"
  } > "${dest}"

  python3 -c "
import json, sys
print(json.dumps({'topic':'${topic}','src':'${src}','dest':'${dest}','sha256':'${sha}'}))
" >> "${MANIFEST}"

  echo "COPIED ${dest_name}"
}

copy_tree() {
  local topic="$1"
  local src_dir="$2"
  local name_pattern="$3"
  local prefix="${4:-}"

  if [[ ! -d "${src_dir}" ]]; then
    echo "SKIP missing dir: ${src_dir}" >&2
    return 0
  fi

  while IFS= read -r -d '' file; do
    local rel="${file#${src_dir}/}"
    local slug
    slug="$(echo "${prefix}${rel}" | tr '/' '-' | tr '[:upper:]' '[:lower:]' | sed 's/\.mdc$/.md/')"
    copy_source "${topic}" "${file}" "${slug}"
  done < <(find "${src_dir}" -type f -name "${name_pattern}" -print0 2>/dev/null)
}

echo "=== Tier 1 bootstrap: compass-mcp-server ==="

copy_source "compass-mcp" "${MCP}/SKILL.md" "skill.md"
copy_source "compass-mcp" "${MCP}/README.md" "readme.md"
copy_source "compass-mcp" "${MCP}/docs/AGENT-GUIDANCE.md" "agent-guidance.md"
copy_source "compass-mcp" "${MCP}/docs/TOOLS.md" "tools.md"
copy_source "compass-mcp" "${MCP}/docs/TELEMETRY.md" "telemetry.md"
copy_source "compass-mcp" "${MCP}/docs/ONBOARDING-CHECKLIST.md" "onboarding-checklist.md"
copy_source "compass-mcp" "${MCP}/docs/ONBOARDING-TEAMS-M365.md" "onboarding-teams-m365.md"
copy_source "compass-mcp" "${MCP}/docs/installable/compass-mcp-user.mdc" "compass-mcp-user.mdc"
copy_source "compass-mcp" "${MCP}/docs/skills/COMPASS_MCP_SKILL.md" "compass-mcp-skill.md"

copy_tree "compass-mcp" "${MCP}/docs/agents" "*.md" "agents-"
copy_tree "compass-mcp" "${MCP}/docs/resources" "*.md" "resources-"
copy_tree "compass-mcp" "${MCP}/docs/internal" "*.md" "internal-"

while IFS= read -r -d '' file; do
  rel="${file#${MCP}/docs/reference/}"
  slug="reference-$(echo "${rel}" | tr '/' '-' | tr '[:upper:]' '[:lower:]')"
  copy_source "compass-mcp" "${file}" "${slug}"
done < <(find "${MCP}/docs/reference" -type f -name '*.md' ! -name '*.generated.md' -print0 2>/dev/null)

echo "=== Tier 1 bootstrap: compass ==="

copy_source "compass" "${COMPASS}/README.md" "readme.md"
copy_source "compass" "${COMPASS}/REGRESSION_PLAN.md" "regression-plan.md"

if [[ -f "${COMPASS}/app/chat/CHAT_MCP_SETUP.md" ]]; then
  copy_source "compass" "${COMPASS}/app/chat/CHAT_MCP_SETUP.md" "chat-mcp-setup.md"
fi

copy_tree "compass" "${COMPASS}/.cursor/rules" "*.mdc" "rules-"

echo "=== Done ==="
wc -l "${MANIFEST}" | awk '{print "Manifest entries:", $1}'
