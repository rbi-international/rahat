# Copy to config.sh (git-ignored) and edit.
RG=rahat-rg
LOC=eastus2                      # region for the app and the Foundry resource (gpt-5-mini GlobalStandard)
AI=rahat-foundry                 # RAHAT's own Foundry resource
AI_RG=rahat-rg                   # resource group of that Foundry resource
CS=rahat-safety                  # Content Safety, globally unique custom domain
CS_LOC=eastus                    # Prompt Shields region; may differ from LOC
CS_SKU=F0                        # F0 is the free tier; S0 if F0 is already used in your subscription
LAW=rahat-logs
APPI=rahat-appi
ACR=rahatacr0000                 # pick ONE fixed name (az acr check-name -n <name>); never $RANDOM, it changes every run
ENV_NAME=rahat-env
ST=rahatst0000                   # storage for learning-outcome snapshots; globally unique, lowercase + digits
APP=rahat
GITHUB_REPO=rbi-international/rahat

# Models: check the Lifecycle column in the Foundry model catalogue says Generally Available.
MODEL_FAST_NAME=gpt-5-mini
MODEL_STRONG_NAME=gpt-5-mini     # same model, more thinking; or a larger GA model if you have quota
REASONING_FAST=minimal
REASONING_STRONG=low
TPM_FAST_K=200                   # thousands of tokens per minute. The per-minute hard cap.
TPM_STRONG_K=100

ALLOCATION_TOKENS=2500000        # the Neerdhara allocation per class: the total hard cap
PRICE_INR_PER_M_TOKENS=60        # blended price, check the Azure pricing page for your model and region
WORKERS=16
