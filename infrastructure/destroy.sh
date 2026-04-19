#!/bin/bash
set -e

MODULES=("04-stateless" "03-blob-storage" "02-cosmos" "01-networking")

for module in "${MODULES[@]}"; do
  echo "=========================================="
  echo "Destroying $module"
  echo "=========================================="
  (cd "$module" && terraform init && terraform destroy -auto-approve)
  echo ""
done

echo "All modules destroyed successfully."