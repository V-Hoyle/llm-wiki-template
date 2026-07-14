#!/usr/bin/env bash
# Export Cursor plans + canvases to raw/ (wrapper).
set -euo pipefail
exec python3 "${HOME}/.cursor/llm-wiki/scripts/export-cursor-deliverables.py" "$@"
