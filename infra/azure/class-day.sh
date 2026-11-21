#!/usr/bin/env bash
# Before class: warm one replica so the first phone never waits for a cold start.
# After class: scale to zero and drop the TPM to 1k so nothing can spend.
#
#   ./infra/azure/class-day.sh up
#   ./infra/azure/class-day.sh down
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"

case "${1:-}" in
  up)
    az containerapp update -n "$APP" -g "$RG" --min-replicas 1 --max-replicas 1 -o none
    for dep in rahat-fast:"$TPM_FAST_K" rahat-strong:"$TPM_STRONG_K"; do
      IFS=: read -r d cap <<<"$dep"
      az cognitiveservices account deployment update -n "$AI" -g "$AI_RG" --deployment-name "$d" \
        --sku-capacity "$cap" -o none 2>/dev/null || echo "  set $d capacity to ${cap}k in the portal"
    done
    FQDN=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
    curl -fs "https://$FQDN/healthz" && echo "  warm: https://$FQDN"
    ;;
  down)
    az containerapp update -n "$APP" -g "$RG" --min-replicas 0 --max-replicas 0 -o none 2>/dev/null \
      || az containerapp update -n "$APP" -g "$RG" --min-replicas 0 -o none
    for d in rahat-fast rahat-strong; do
      az cognitiveservices account deployment update -n "$AI" -g "$AI_RG" --deployment-name "$d" \
        --sku-capacity 1 -o none 2>/dev/null || echo "  lower $d to 1k TPM in the portal"
    done
    echo "  down: app scaled to zero, deployments at 1k TPM"
    ;;
  *) echo "usage: $0 up|down"; exit 2 ;;
esac
