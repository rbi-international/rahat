#!/usr/bin/env bash
# Persistent storage for learning-outcome snapshots: a private Blob container the app
# writes to with its managed identity. No storage key is created, stored or printed.
# Safe to re-run. provision.sh calls it; on an existing deployment run it on its own:
#
#   ./infra/azure/outcomes-storage.sh [--dry-run]
#
# Note: setting OUTCOMES_BLOB_URL on the app creates a new revision, which restarts it and
# clears the in-memory room. Run it before class, never during one.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
: "${ST:?set ST (storage account name, 3-24 lowercase letters and digits, globally unique) in config.sh}"
CONTAINER=${OUTCOMES_CONTAINER:-outcomes}
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
run() { if [ "$DRY" = 1 ]; then printf '  would run: %s\n' "$*"; else "$@"; fi; }

echo "== Storage account $ST in $RG ($LOC), private, TLS 1.2, no shared-key access"
az provider register -n Microsoft.Storage --wait -o none
if az storage account show -n "$ST" -g "$RG" -o none 2>/dev/null; then
  echo "  $ST already exists, reusing"
else
  run az storage account create -n "$ST" -g "$RG" -l "$LOC" --sku Standard_LRS --kind StorageV2 \
    --min-tls-version TLS1_2 --allow-blob-public-access false --allow-shared-key-access false -o none
fi
ST_ID=$(az storage account show -n "$ST" -g "$RG" --query id -o tsv 2>/dev/null || echo "<new account id>")

echo "== Container $CONTAINER"
run az storage container-rm create --storage-account "$ST" -g "$RG" -n "$CONTAINER" --public-access off -o none

echo "== Roles: the app writes, you read (for scripts/outcomes_report.py --blob)"
PRINCIPAL=$(az containerapp show -n "$APP" -g "$RG" --query identity.principalId -o tsv)
run az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" --scope "$ST_ID" -o none
ME=$(az ad signed-in-user show --query id -o tsv)
run az role assignment create --assignee-object-id "$ME" --assignee-principal-type User \
  --role "Storage Blob Data Reader" --scope "$ST_ID" -o none

echo "== App setting OUTCOMES_BLOB_URL (new revision: the app restarts)"
run az containerapp update -n "$APP" -g "$RG" \
  --set-env-vars OUTCOMES_BLOB_URL="https://$ST.blob.core.windows.net/$CONTAINER" -o none
[ "$DRY" = 1 ] && echo "  dry run: nothing changed" || echo "  done. Role assignments can take a few minutes to apply."
