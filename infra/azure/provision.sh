#!/usr/bin/env bash
# One-time Azure setup for RAHAT. Run from Git Bash after `az login`.
# Every step prints what it is doing; re-running is safe for most steps.
#
#   cp infra/azure/config.example.sh infra/azure/config.sh   # edit names
#   ./infra/azure/provision.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "0. Resource providers (a new subscription has not registered these yet)"
for ns in Microsoft.CognitiveServices Microsoft.ContainerRegistry Microsoft.App Microsoft.OperationalInsights; do
  az provider register -n "$ns" --wait -o none
done

step "1. Resource group $RG in $LOC"
az group create -n "$RG" -l "$LOC" -o none

step "2. Microsoft Foundry resource $AI"
if ! az cognitiveservices account show -n "$AI" -g "$AI_RG" -o none 2>/dev/null; then
  az cognitiveservices account create -n "$AI" -g "$AI_RG" -l "$LOC" \
    --kind AIServices --sku S0 --custom-domain "$AI" --allow-project-management --yes -o none
fi
AI_ID=$(az cognitiveservices account show -n "$AI" -g "$AI_RG" --query id -o tsv)
AI_ENDPOINT="https://$AI.openai.azure.com/openai/v1/"

step "3. Model deployments, capped by TPM (the per-minute hard limit)"
for pair in "rahat-fast:$MODEL_FAST_NAME:$TPM_FAST_K" "rahat-strong:$MODEL_STRONG_NAME:$TPM_STRONG_K"; do
  IFS=: read -r dep model cap <<<"$pair"
  az cognitiveservices account deployment create -n "$AI" -g "$AI_RG" \
    --deployment-name "$dep" --model-name "$model" --model-format OpenAI \
    --sku-name GlobalStandard --sku-capacity "$cap" -o none
  echo "  $dep -> $model at ${cap}k TPM"
done

step "4. Azure AI Content Safety (Prompt Shields) $CS in ${CS_LOC:-$LOC}"
if az cognitiveservices account show -n "$CS" -g "$RG" -o none 2>/dev/null; then
  echo "  $CS already exists, skipping create"
else
  az cognitiveservices account create -n "$CS" -g "$RG" -l "${CS_LOC:-$LOC}" --kind ContentSafety --sku "$CS_SKU" \
    --custom-domain "$CS" --yes -o none
fi
CS_ENDPOINT=$(az cognitiveservices account show -n "$CS" -g "$RG" --query properties.endpoint -o tsv)
CS_KEY=$(az cognitiveservices account keys list -n "$CS" -g "$RG" --query key1 -o tsv)

step "5. Log Analytics and Application Insights (tracing)"
if az monitor log-analytics workspace show -g "$RG" -n "$LAW" -o none 2>/dev/null; then
  echo "  $LAW already exists, reusing"
else
  az monitor log-analytics workspace create -g "$RG" -n "$LAW" -l "$LOC" -o none
fi
LAW_ID=$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query customerId -o tsv)
LAW_KEY=$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LAW" --query primarySharedKey -o tsv)
az extension add --name application-insights --upgrade -o none 2>/dev/null || true
if az monitor app-insights component show --app "$APPI" -g "$RG" -o none 2>/dev/null; then
  echo "  $APPI already exists, reusing"
else
  az monitor app-insights component create --app "$APPI" -g "$RG" -l "$LOC" --kind web \
    --workspace "$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query id -o tsv)" -o none
fi
APPI_CONN=$(az monitor app-insights component show --app "$APPI" -g "$RG" --query connectionString -o tsv)

step "6. Container registry $ACR"
az acr create -n "$ACR" -g "$RG" -l "$LOC" --sku Basic -o none
ACR_ID=$(az acr show -n "$ACR" --query id -o tsv)

step "7. Container Apps environment"
az extension add --name containerapp --upgrade -o none 2>/dev/null || true
az containerapp env create -n "$ENV_NAME" -g "$RG" -l "$LOC" \
  --logs-workspace-id "$LAW_ID" --logs-workspace-key "$LAW_KEY" -o none

step "8. The app, with a managed identity (no Foundry key anywhere)"
ADMIN_TOKEN=${ADMIN_TOKEN:-$(openssl rand -hex 24)}
METRICS_TOKEN=${METRICS_TOKEN:-$(openssl rand -hex 24)}
az containerapp create -n "$APP" -g "$RG" --environment "$ENV_NAME" \
  --image mcr.microsoft.com/k8se/quickstart:latest --target-port 80 --ingress external \
  --min-replicas 1 --max-replicas 1 --cpu 1.0 --memory 2.0Gi --system-assigned -o none
# The identity exists only now: grant its roles, then pull from ACR with it (no admin user, no password).
PRINCIPAL=$(az containerapp show -n "$APP" -g "$RG" --query identity.principalId -o tsv)
az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services OpenAI User" --scope "$AI_ID" -o none
az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
  --role AcrPull --scope "$ACR_ID" -o none
az containerapp registry set -n "$APP" -g "$RG" --server "$ACR.azurecr.io" --identity system -o none

step "9. First image build (in Azure, no Docker needed locally)"
az acr build -r "$ACR" -t rahat:bootstrap "$HERE/../.." -o none

az containerapp secret set -n "$APP" -g "$RG" --secrets \
  admin-token="$ADMIN_TOKEN" metrics-token="$METRICS_TOKEN" cs-key="$CS_KEY" appi="$APPI_CONN" -o none
FQDN=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
# Port first, so the new revision is probed on the port uvicorn listens on (Dockerfile: 8000).
az containerapp ingress update -n "$APP" -g "$RG" --target-port 8000 -o none
# If this pull fails right after the role assignment, wait two minutes for AcrPull to apply and run it once more.
az containerapp update -n "$APP" -g "$RG" --image "$ACR.azurecr.io/rahat:bootstrap" \
  --set-env-vars PROVIDER=foundry AZURE_USE_MANAGED_IDENTITY=1 AZURE_OPENAI_ENDPOINT="$AI_ENDPOINT" \
    MODEL_FAST=rahat-fast MODEL_STRONG=rahat-strong REASONING_FAST="$REASONING_FAST" REASONING_STRONG="$REASONING_STRONG" \
    FALLBACK_PROVIDER="" ORG_NAME="Neerdhara Systems" ALLOCATION_TOKENS="$ALLOCATION_TOKENS" \
    PRICE_INR_PER_M_TOKENS="$PRICE_INR_PER_M_TOKENS" WORKERS="$WORKERS" PUBLIC_BASE_URL="https://$FQDN" \
    CONTENT_SAFETY_ENDPOINT="$CS_ENDPOINT" CONTENT_SAFETY_KEY=secretref:cs-key \
    APPLICATIONINSIGHTS_CONNECTION_STRING=secretref:appi \
    ADMIN_TOKEN=secretref:admin-token METRICS_TOKEN=secretref:metrics-token -o none

step "9b. Blob container for learning-outcome snapshots"
"$HERE/outcomes-storage.sh"

step "10. GitHub Actions identity (OpenID Connect, no stored password)"
# Least privilege: AcrPush on the registry and Contributor on this one app. Nothing at RG
# or subscription scope. AcrPush cannot run ACR Tasks, so deploy.yml builds on the runner.
APP_ID=$(az ad app list --display-name "rahat-github-deploy" --query "[0].appId" -o tsv)
[ -n "$APP_ID" ] || APP_ID=$(az ad app create --display-name "rahat-github-deploy" \
  --sign-in-audience AzureADMyOrg --query appId -o tsv)
az ad sp create --id "$APP_ID" -o none 2>/dev/null || true
# GitHub now puts owner and repo IDs in the OIDC subject: repo:OWNER@ID/REPO@ID:environment:production.
# The old name-only form fails with AADSTS700213. Read the IDs from the GitHub API.
OWNER_ID=$(gh api "repos/$GITHUB_REPO" --jq .owner.id); REPO_ID=$(gh api "repos/$GITHUB_REPO" --jq .id)
GITHUB_OIDC_SUBJECT="repo:${GITHUB_REPO%%/*}@$OWNER_ID/${GITHUB_REPO#*/}@$REPO_ID:environment:production"
SP_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv)
az ad app federated-credential create --id "$APP_ID" --parameters "{
  \"name\": \"github-production\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"$GITHUB_OIDC_SUBJECT\",
  \"audiences\": [\"api://AzureADTokenExchange\"]}" -o none 2>/dev/null || true
az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
  --role Contributor --scope "$(az containerapp show -n "$APP" -g "$RG" --query id -o tsv)" -o none
az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
  --role AcrPush --scope "$ACR_ID" -o none

cat <<OUT

Done. Keep these somewhere safe (not in the repo):
  App URL        https://$FQDN
  Console        https://$FQDN/console
  ADMIN_TOKEN    $ADMIN_TOKEN
  METRICS_TOKEN  $METRICS_TOKEN

GitHub repository variables (Settings > Secrets and variables > Actions > Variables):
  AZURE_CLIENT_ID=$APP_ID
  AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)
  AZURE_SUBSCRIPTION_ID=$(az account show --query id -o tsv)
  AZURE_RG=$RG  AZURE_ACR=$ACR  AZURE_APP=$APP
And create a GitHub environment named "production" (the federated credential trusts only it).
Required reviewers on it need a public repo or a paid GitHub plan.
OUT
