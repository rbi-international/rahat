# Runbook

## One-time setup (about 40 minutes)

Prerequisites: Azure CLI (`az login`), Git Bash, a subscription with model quota.

```bash
cp infra/azure/config.example.sh infra/azure/config.sh   # edit names, region, TPM, allocation
./infra/azure/provision.sh
```

The script creates or reuses:

1. the Foundry resource and two deployments, `rahat-fast` and `rahat-strong`, each with a TPM cap,
2. Azure AI Content Safety for Prompt Shields,
3. Log Analytics and Application Insights for logs and traces,
4. a container registry and a Container Apps environment,
5. the app with a system-assigned managed identity, granted *Cognitive Services OpenAI User* on the Foundry resource (no key in the container),
6. a GitHub deploy identity using OpenID Connect, limited to the `production` environment of this repository.

At the end it prints the URL, the admin token, the metrics token, and the GitHub variables to set.

### Two limits, and why both
- **TPM on each deployment** caps the per-minute rate. Requests over it are throttled, not billed.
- **The allocation** (`ALLOCATION_TOKENS`) caps the total. When it runs out the app switches to SOP rules and spends nothing more.

Pay As You Go has no spending limit and budget alerts arrive about a day late, so neither of these is optional.

## Release

```bash
git tag v1.0.0
git push origin v1.0.0
```

`deploy.yml` runs the tests and the eval gate, builds the image in the registry, rolls out a new revision and waits for `/healthz` on the live URL. A failing gate stops the release.

## Class day

| When | Do |
|---|---|
| Evening before | `./infra/azure/class-day.sh up`, then `python scripts/simulate_class.py --base https://<app> --admin <token> --students 140 --minutes 5` as the load test. Then **Reset room**. |
| 30 min before | `./infra/azure/class-day.sh up` again (warm replica). `python scripts/check_lanes.py` against Foundry. Start Grafana: `./infra/observability.sh <app-host> <metrics-token>`. |
| Start | Console: **Open session**. Put the QR on the projector. Read the officer code to the officers. |
| Siren | **Start exercise**. Inject in this order: Ravi surge, Network degraded, Boat failure, Network restored, VIP pressure call, Second wave. |
| End | **Terminate exercise**. The wall shows the findings. Screenshot it. |
| After | `./infra/azure/class-day.sh down`. Optionally regenerate the Foundry key. |

## When something goes wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| Agent lines show `rules` instead of a lane | Allocation spent, or both tiers failing | Console ledger; `check_lanes.py`; Grafana "Model calls by result" |
| Many `RateLimitError` | TPM too low for the burst | Raise TPM on the deployment, or lower `WORKERS` |
| Queue keeps growing | More arrivals than workers can process | Raise `WORKERS` if TPM allows, otherwise let the queue absorb it |
| Phones get 403 | Session expired or closed | **Open session** again; the new QR replaces the old |
| 404 from Foundry | `MODEL_FAST` is a model name, not a deployment name | Use `rahat-fast` |
