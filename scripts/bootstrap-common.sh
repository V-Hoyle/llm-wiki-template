#!/usr/bin/env bash
# Shared helpers for llm-wiki bootstrap scripts.
set -euo pipefail

WIKI_ROOT="${HOME}/.cursor/llm-wiki"
RAW="${WIKI_ROOT}/raw"
MANIFEST="${WIKI_ROOT}/schema/bootstrap-manifest.jsonl"

bootstrap_copy_source() {
  local topic="$1"
  local src="$2"
  local dest_name="$3"
  local collected="${4:-$(date -u +%Y-%m-%d)}"

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
    echo "Collected: ${collected}"
    echo "Published: Unknown"
    echo "SHA256: ${sha}"
    echo "---"
    echo ""
    cat "${src}"
  } > "${dest}"

  python3 -c "
import json
print(json.dumps({'topic':'${topic}','src':'${src}','dest':'${dest}','sha256':'${sha}'}))
" >> "${MANIFEST}"

  echo "COPIED ${dest_name}"
}

bootstrap_copy_tree() {
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
    slug="$(echo "${prefix}${rel}" | tr '/' '-' | tr '[:upper:]' '[:lower:]')"
    bootstrap_copy_source "${topic}" "${file}" "${slug}"
  done < <(find "${src_dir}" -type f -name "${name_pattern}" -print0 2>/dev/null)
}
