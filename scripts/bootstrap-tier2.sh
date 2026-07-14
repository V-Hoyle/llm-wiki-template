#!/usr/bin/env bash
# Bootstrap Tier 2: treasury package/docs → raw/data-platform/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bootstrap-common.sh
source "${SCRIPT_DIR}/bootstrap-common.sh"

DATASSEMBLY="${HOME}/Documents/Github/Datasembly"
TREASURY="${DATASSEMBLY}/treasury"
DOCS="${TREASURY}/package/docs"
TOPIC="data-platform"

mkdir -p "${RAW}/${TOPIC}"

echo "=== Tier 2 bootstrap: treasury ==="

bootstrap_copy_source "${TOPIC}" "${TREASURY}/README.md" "treasury-readme.md"
bootstrap_copy_tree "${TOPIC}" "${DOCS}/core" "*.md" "core-"
bootstrap_copy_tree "${TOPIC}" "${DOCS}/product" "*.md" "product-"
bootstrap_copy_tree "${TOPIC}" "${DOCS}/tenant" "*.md" "tenant-"
bootstrap_copy_tree "${TOPIC}" "${DOCS}/views" "*.md" "views-"
bootstrap_copy_tree "${TOPIC}" "${DOCS}/rollups" "*.md" "rollups-"
bootstrap_copy_tree "${TOPIC}" "${DOCS}/collection" "*.md" "collection-"
bootstrap_copy_source "${TOPIC}" "${DOCS}/columns.md" "columns.md"

echo "=== Done ==="
find "${RAW}/${TOPIC}" -type f ! -name '.gitkeep' | wc -l | awk '{print "data-platform raw files:", $1}'
