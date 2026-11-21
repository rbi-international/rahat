# Model lanes

Your code never names a provider. Set `PROVIDER` in `.env` and run. Each lane serves two tiers, `fast` and `strong`, which the router chooses between.

## fake (default)
Offline and deterministic. It answers with the SOP rules and charges pretend tokens. CI and every test use it. Start here to see the whole system without any key.

## groq (free)
1. console.groq.com/keys, sign in, create a key.
2. `.env`: `PROVIDER=groq`, `GROQ_API_KEY=...`
3. Defaults: fast `openai/gpt-oss-20b`, strong `openai/gpt-oss-120b`. Optional: `REASONING_FAST=low`.

The free tier has a daily request cap and a per-minute limit. Fine for one person, not for a class on one key.

## nvidia (free)
1. build.nvidia.com, sign in, generate an API key.
2. `.env`: `PROVIDER=nvidia`, `NVIDIA_API_KEY=...`
3. Defaults: fast `meta/llama-3.1-8b-instruct`, strong `meta/llama-3.3-70b-instruct`. Check the catalogue and set `MODEL_FAST` and `MODEL_STRONG` if those names change.

The free endpoint allows about 40 requests per minute, and NVIDIA does not raise it for free accounts. One SOS uses three model calls, so one key handles roughly a dozen SOS per minute. Set `WORKERS=4` on this lane so the queue absorbs bursts instead of producing 429 errors.

## local (Ollama)
```bash
ollama pull llama3.2
```
`.env`: `PROVIDER=local`. No key, no limit, slower and weaker.

## foundry (paid)
Microsoft Foundry deployments, named `rahat-fast` and `rahat-strong` by `infra/azure/provision.sh`. Locally:

```
PROVIDER=foundry
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/openai/v1/
AZURE_OPENAI_API_KEY=...
MODEL_FAST=rahat-fast
MODEL_STRONG=rahat-strong
REASONING_FAST=minimal
REASONING_STRONG=low
```

In Azure Container Apps the app uses its managed identity instead of a key (`AZURE_USE_MANAGED_IDENTITY=1`).

## A fallback lane
`FALLBACK_PROVIDER=groq` adds a second lane that is used only when both tiers of the main lane are throttled or down.

## Check before you rely on it
```bash
python scripts/check_lanes.py
```
One live call per tier, with latency, tokens and whether the reply parsed. A tier that fails here would silently drop to SOP rules during a run.
