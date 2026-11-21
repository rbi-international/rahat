# Copy to config.sh (git-ignored) and edit.
RG=rahat-rg
LOC=eastus                       # a region where your subscription has model quota
AI=cse476-foundry                # reuse your existing Foundry resource, or a new unique name
AI_RG=cse476-rg                  # resource group of that Foundry resource
CS=rahat-safety-$RANDOM          # Content Safety, globally unique
CS_SKU=F0                        # F0 is the free tier; S0 if F0 is already used in your subscription
LAW=rahat-logs
APPI=rahat-appi
ACR=rahatacr$RANDOM              # letters and digits only, globally unique
ENV_NAME=rahat-env
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
