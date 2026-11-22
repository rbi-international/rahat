#!/usr/bin/env bash
# Before class: warm one replica so the first phone never waits for a cold start, and raise TPM.
# After class: scale to zero and drop the TPM to 1k so nothing can spend.
#
#   ./infra/azure/class-day.sh up   [--dry-run]
#   ./infra/azure/class-day.sh down [--dry-run]
#
# --dry-run reads current state and prints every command that would change something, without running it.
# The app URL is never printed: health checks show status codes only.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
DRY=0; [ "${2:-}" = "--dry-run" ] && DRY=1

run() {
  if [ "$DRY" = 1 ]; then printf '  would run: %s\n' "$*"; else "$@"; fi
}

# There is no `deployment update`: create on an existing name changes it in place.
# Model, version and SKU are read from the deployment itself, so only the capacity changes.
set_tpm() {
  local d=$1 cap=$2 cur model ver sku
  local info
  info=$(az cognitiveservices account deployment show -n "$AI" -g "$AI_RG" --deployment-name "$d" \
    --query "[sku.capacity, properties.model.name, properties.model.version, sku.name]" -o tsv)
  read -r cur model ver sku <<<"$(echo $info)"
  echo "  $d: ${cur}k -> ${cap}k TPM ($model $ver, $sku)"
  [ "$cur" = "$cap" ] && return 0
  run az cognitiveservices account deployment create -n "$AI" -g "$AI_RG" --deployment-name "$d" \
    --model-name "$model" --model-version "$ver" --model-format OpenAI --sku-name "$sku" --sku-capacity "$cap" -o none
}

replicas() {
  az containerapp show -n "$APP" -g "$RG" --query "properties.template.scale.[minReplicas,maxReplicas]" -o tsv | tr -d '\r' | paste -sd/
}

case "${1:-}" in
  up)
    echo "  replicas now (min/max): $(replicas) -> 1/1"
    run az containerapp update -n "$APP" -g "$RG" --min-replicas 1 --max-replicas 1 -o none
    set_tpm rahat-fast "$TPM_FAST_K"
    set_tpm rahat-strong "$TPM_STRONG_K"
    FQDN=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
    if [ "$DRY" = 1 ]; then echo "  would check: /healthz and /readyz (status codes only)"; else
      for p in healthz readyz; do echo "  $p $(curl -s -o /dev/null -w '%{http_code}' "https://$FQDN/$p")"; done
    fi
    ;;
  down)
    echo "  replicas now (min/max): $(replicas) -> 0/1 (scales to zero when idle)"
    run az containerapp update -n "$APP" -g "$RG" --min-replicas 0 --max-replicas 1 -o none
    set_tpm rahat-fast 1
    set_tpm rahat-strong 1
    [ "$DRY" = 1 ] && echo "  dry run: nothing changed" || echo "  down: app scales to zero, deployments at 1k TPM"
    ;;
  *) echo "usage: $0 up|down [--dry-run]"; exit 2 ;;
esac
