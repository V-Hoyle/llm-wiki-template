#!/usr/bin/env bash
# Stage an external file for LLM wiki ingest (raw/ copy + queue entry).
#
# Usage:
#   ingest-adhoc.sh [--run] [--dry-run] --topic TOPIC /path/to/source.md [--slug name]
#
# Examples:
#   ingest-adhoc.sh --topic data-platform ~/Downloads/report.md
#   ingest-adhoc.sh --run --topic decisions --slug kroger-zones ~/Downloads/newsletter.md
set -euo pipefail

WIKI_ROOT="${HOME}/.cursor/llm-wiki"
QUEUE_FILE="${WIKI_ROOT}/queue/pending.jsonl"
SCRIPTS="${WIKI_ROOT}/scripts"

RUN=0
DRY_RUN=0
TOPIC=""
SLUG=""
SOURCE=""

usage() {
  sed -n '2,6p' "$0"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run) RUN=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --topic) TOPIC="${2:-}"; shift 2 ;;
    --slug) SLUG="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    -*) echo "Unknown option: $1" >&2; usage ;;
    *)
      if [[ -z "${SOURCE}" ]]; then
        SOURCE="$1"
      else
        echo "Unexpected extra argument: $1" >&2
        exit 1
      fi
      shift
      ;;
  esac
done

VALID_TOPICS="compass compass-mcp data-platform orchestration infrastructure decisions"
if [[ -z "${TOPIC}" ]] || [[ -z "${SOURCE}" ]]; then
  echo "Error: --topic and source file are required." >&2
  usage
fi
if ! echo " ${VALID_TOPICS} " | grep -q " ${TOPIC} "; then
  echo "Error: invalid topic '${TOPIC}'. Must be one of: ${VALID_TOPICS}" >&2
  exit 1
fi

SOURCE="$(cd "$(dirname "${SOURCE}")" && pwd)/$(basename "${SOURCE}")"
if [[ ! -f "${SOURCE}" ]]; then
  echo "Error: source file not found: ${SOURCE}" >&2
  exit 1
fi

if [[ -z "${SLUG}" ]]; then
  base="$(basename "${SOURCE}" .md)"
  base="$(basename "${base}" .markdown)"
  SLUG="$(echo "${base}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g' | cut -c1-60)"
fi

COLLECTED="$(date -u +%Y-%m-%d)"
RAW_DIR="${WIKI_ROOT}/raw/${TOPIC}"
RAW_DEST="${RAW_DIR}/${COLLECTED}-${SLUG}.md"
CONVERSATION_ID="adhoc-${SLUG}-$(date -u +%s)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "${RAW_DIR}" "$(dirname "${QUEUE_FILE}")"

if [[ -f "${RAW_DEST}" ]]; then
  n=2
  while [[ -f "${RAW_DIR}/${COLLECTED}-${SLUG}-${n}.md" ]]; do
    n=$((n + 1))
  done
  RAW_DEST="${RAW_DIR}/${COLLECTED}-${SLUG}-${n}.md"
  CONVERSATION_ID="adhoc-${SLUG}-${n}-$(date -u +%s)"
fi

if [[ ${DRY_RUN} -eq 1 ]]; then
  echo "DRY RUN"
  echo "  topic:       ${TOPIC}"
  echo "  source:      ${SOURCE}"
  echo "  raw dest:    ${RAW_DEST}"
  echo "  queue id:    ${CONVERSATION_ID}"
  echo "  run worker:  ${RUN}"
  exit 0
fi

{
  echo "---"
  echo "Source: ${SOURCE}"
  echo "Collected: ${COLLECTED}"
  echo "Published: Unknown"
  echo "Ingest: adhoc"
  echo "---"
  echo ""
  cat "${SOURCE}"
} > "${RAW_DEST}"

echo "WROTE ${RAW_DEST}"

EXCERPT="$(python3 -c "
import pathlib
text = pathlib.Path('${RAW_DEST}').read_text(encoding='utf-8', errors='replace')
for word in ('api_key', 'secret', 'password', 'token', 'CURSOR_API_KEY'):
    text = text.replace(word, 'REDACTED')
print(text[:10000])
")"

export WIKI_QUEUE_FILE="${QUEUE_FILE}"
export WIKI_TS="${TS}"
export WIKI_CONVERSATION_ID="${CONVERSATION_ID}"
export WIKI_TOPIC="${TOPIC}"
export WIKI_RAW_PATH="${RAW_DEST}"
export WIKI_SOURCE="${SOURCE}"
export WIKI_EXCERPT="${EXCERPT}"

python3 <<'PY'
import json
import os

entry = {
    "ts": os.environ["WIKI_TS"],
    "conversation_id": os.environ["WIKI_CONVERSATION_ID"],
    "workspace": os.environ["WIKI_SOURCE"],
    "topics": [os.environ["WIKI_TOPIC"]],
    "transcript_path": "",
    "transcript_excerpt": os.environ.get("WIKI_EXCERPT", ""),
    "ingest_kind": "adhoc",
    "raw_path": os.environ["WIKI_RAW_PATH"],
    "status": "pending",
}

with open(os.environ["WIKI_QUEUE_FILE"], "a", encoding="utf-8") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
PY

echo "QUEUED ${CONVERSATION_ID} → ${QUEUE_FILE}"

if [[ ${RUN} -eq 1 ]]; then
  echo "Running ingest worker..."
  bash "${SCRIPTS}/run-ingest-worker.sh"
fi
