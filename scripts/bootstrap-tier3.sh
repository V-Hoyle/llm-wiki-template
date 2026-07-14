#!/usr/bin/env bash
# Bootstrap Tier 3: airflow, ops, infrastructure → raw/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bootstrap-common.sh
source "${SCRIPT_DIR}/bootstrap-common.sh"

DATASSEMBLY="${HOME}/Documents/Github/Datasembly"
AIRFLOW="${DATASSEMBLY}/airflow"
OPS="${DATASSEMBLY}/ops"
INFRA="${DATASSEMBLY}/infrastructure"

mkdir -p "${RAW}/orchestration" "${RAW}/infrastructure"

echo "=== Tier 3 bootstrap: airflow ==="
bootstrap_copy_source "orchestration" "${AIRFLOW}/README.md" "airflow-readme.md"
bootstrap_copy_tree "orchestration" "${AIRFLOW}/docs" "*.md" "airflow-docs-"

echo "=== Tier 3 bootstrap: ops ==="
bootstrap_copy_source "infrastructure" "${OPS}/README.md" "ops-readme.md"
bootstrap_copy_source "infrastructure" "${OPS}/astronomer/README.md" "ops-astronomer-readme.md"
bootstrap_copy_tree "infrastructure" "${OPS}/astronomer/docs" "*.md" "ops-astronomer-docs-"
bootstrap_copy_source "infrastructure" "${OPS}/deployment/backend-cluster/README.md" "ops-backend-cluster-readme.md"
bootstrap_copy_source "infrastructure" "${OPS}/deployment/backend-cluster/docs/upgrade-node-pool.md" "ops-backend-cluster-upgrade-node-pool.md"
bootstrap_copy_source "infrastructure" "${OPS}/deployment/frontend-cluster/README.md" "ops-frontend-cluster-readme.md"
bootstrap_copy_source "infrastructure" "${OPS}/dataproc/README.md" "ops-dataproc-readme.md"
bootstrap_copy_source "infrastructure" "${OPS}/strimzi-kafka/README.md" "ops-strimzi-kafka-readme.md"

echo "=== Tier 3 bootstrap: infrastructure ==="
bootstrap_copy_source "infrastructure" "${INFRA}/README.md" "infrastructure-readme.md"
bootstrap_copy_source "infrastructure" "${INFRA}/datasembly_iac/modules/ccloud/README.md" "infrastructure-ccloud-readme.md"
bootstrap_copy_source "infrastructure" "${INFRA}/general/cicd_server/README.md" "infrastructure-cicd-server-readme.md"

echo "=== Done ==="
echo -n "orchestration raw: "; find "${RAW}/orchestration" -type f ! -name '.gitkeep' | wc -l | tr -d ' '
echo -n "infrastructure raw: "; find "${RAW}/infrastructure" -type f ! -name '.gitkeep' | wc -l | tr -d ' '
