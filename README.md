# RAHAT

**Real-time Agentic Help And Triage.** A multi-agent control room for district flood response, built on Microsoft Foundry and Microsoft Agent Framework.

During the August and September 2025 floods along the Ravi, Beas and Sutlej, district control rooms in Punjab received far more distress messages than people could read. Messages arrive in Punjabi, Hindi, Hinglish and English, often without an address. Sorting them by hand means the boats go to whoever is loudest, not to whoever is in the most danger.

RAHAT reads every message, works out where the person is, ranks them by danger using the district's own rubric, holds the nearest suitable boat, NDRF team or medical unit, and writes a plan. **A human duty officer approves every dispatch.** The system never sends a boat on its own.

> Live demo: `https://rahat-demo.example.org` (placeholder, the real link is shared in class only)

![RAHAT control room wall](docs/img/wall.png)

---

## What happens to one SOS

```
 phone ──► gateway ──► guard ──┬─► review queue        (possible prompt injection: a human reads it)
          (queue,              │
          rate limit)          └─► intake ──┬─► geo-locator ──┐
                                            └─► verifier ─────┴─► triage ──┬─► info desk
                                                                           └─► allocate ──► commander ──► officer
```

| Agent | Job | Model or code |
|---|---|---|
| Guard | Masks phone and Aadhaar numbers, screens for prompt injection, flags influence | Code + Azure Prompt Shields |
| Intake | Reads the message in any of four languages into a typed record | Model |
| Geo-locator | Finds the village in the district gazetteer | Code (exact work) |
| Verifier | Catches duplicates and implausible claims | Code |
| Router | Picks the cheap or the strong model for this case, learning from officer decisions | Thompson-sampling bandit |
| Triage | Applies the SOP rubric, may move it one level with a stated reason | Model, bounded by code |
| Resource | Holds the nearest suitable unit | Code (optimisation) |
| Commander | Writes the officer brief and the citizen's reply | Model |
| Info desk | Answers public questions from aggregate facts only | Model |

Three agents use a model. Everything that is exact work is code, because code is cheaper, faster and right every time. That split is the main design decision in this repository. The details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Run it in five minutes, no key needed

```bash
git clone https://github.com/rbi-international/rahat.git
cd rahat
conda env create -f environment.yml
conda activate rahat
python -m ipykernel install --user --name rahat --display-name "RAHAT (Python 3.12)"

cp .env.example .env          # PROVIDER=fake runs fully offline
uvicorn rahat.api:app --reload
```

Open `http://localhost:8000/console`, sign in with the `ADMIN_TOKEN` from your `.env`, press **Open session**, then **Start exercise**. In a second terminal, play a whole class:

```bash
python scripts/simulate_class.py --admin <ADMIN_TOKEN> --students 60 --minutes 3
```

Open `http://localhost:8000/wall` to watch it.

## Use a real model for free

Change one line in `.env`. No code changes. See [docs/LANES.md](docs/LANES.md).

| Lane | Cost | Get a key |
|---|---|---|
| `groq` | Free tier, daily cap | console.groq.com/keys |
| `nvidia` | Free tier, 40 requests per minute | build.nvidia.com |
| `local` | Free, runs on your machine | ollama.com |
| `foundry` | Paid, Microsoft Foundry | your Azure subscription |

```bash
python scripts/check_lanes.py    # one live call per model tier, before you rely on it
```

## Repository layout

```
src/rahat/
  agents/        model-backed specialists and their versioned prompts
  tools/         gazetteer, resource registry, river gauges (plain code)
  guardrails/    PII, prompt injection, fairness and output policy, audit log
  drill/         exercise personas, injects, live scoring against ground truth
  workflow.py    the Agent Framework graph
  llm.py         model gateway: allocation, circuit breaker, fallbacks, metering
  ledger.py      the organisation's token allocation, enforced in code
  router.py      the model-tier bandit
  pipeline.py    queue, workers, the officer's decision, the dispatch scheduler
  api.py         FastAPI: pages, JSON API, event stream, /metrics
  web/           citizen page, officer console, control-room wall
infra/           Azure provisioning, class-day scaling, Prometheus and Grafana
scripts/         class simulator, lane check, eval gate
tests/           offline tests, run on every push
```

## Quality gates

Every push runs lint, the offline test suite, and an **eval gate**: a short rehearsal with simulated citizens that fails the build if location accuracy, priority accuracy or under-triage cross their thresholds, or if any prompt injection gets through. Tagged releases deploy to Azure Container Apps through OpenID Connect, with no Azure password stored in GitHub. See [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Known limitations

- The incident board is held in memory, so the app runs as a single replica. Scaling out means moving the board to Redis or Cosmos DB.
- Gazetteer coordinates are approximate town centres for an exercise, not survey data.
- A convincing false SOS cannot be caught by a model. That is what the officer's callback is for, and the exercise shows it.
- The router learns from officer approvals. Officers who rubber-stamp plans teach it the wrong lesson.

## Disclaimer

RAHAT is a teaching system for exercises. It is not connected to any government emergency service. In a real emergency in India call 112, or the disaster helpline 1070.

MIT licence.
