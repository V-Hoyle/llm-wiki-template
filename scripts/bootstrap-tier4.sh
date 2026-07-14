#!/usr/bin/env bash
# Bootstrap Tier 4: exports + halfpipe READMEs → raw/data-platform/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bootstrap-common.sh
source "${SCRIPT_DIR}/bootstrap-common.sh"

DATASSEMBLY="${HOME}/Documents/Github/Datasembly"
EXPORTS="${DATASSEMBLY}/exports"
HALFPIPE="${DATASSEMBLY}/halfpipe"
TOPIC="data-platform"

mkdir -p "${RAW}/${TOPIC}"

echo "=== Tier 4 bootstrap: exports + halfpipe ==="

bootstrap_copy_source "${TOPIC}" "${EXPORTS}/README.md" "exports-readme.md"
bootstrap_copy_source "${TOPIC}" "${EXPORTS}/hdtv/readme.md" "exports-hdtv-readme.md"
bootstrap_copy_source "${TOPIC}" "${EXPORTS}/Marketing Analytics/readme.md" "exports-marketing-analytics-readme.md"
bootstrap_copy_source "${TOPIC}" "${HALFPIPE}/README.md" "halfpipe-readme.md"

echo "=== Done ==="
find "${RAW}/${TOPIC}" -type f -name 'exports-*' -o -name 'halfpipe-*' 2>/dev/null | wc -l | awk '{print "tier4 raw files:", $1}'
