#!/bin/bash
set -e

MODULES=("01-networking" "02-cosmos" "03-blob-storage" "04-stateless")

for module in "${MODULES[@]}"; do
  echo "=========================================="
  echo "Applying $module"
  echo "=========================================="
  (cd "$module" && terraform init && terraform apply -auto-approve)
  echo ""
done

echo "All modules applied successfully."