# Architecture

## The problem in one paragraph

A district control room during a flood is a queue of people in danger that is longer than the room can read. The bottleneck is not boats alone, it is reading: working out from a panicked Hinglish text where someone is, how many people, how high the water, who cannot walk. RAHAT automates the reading and the bookkeeping, and leaves the decision to send a unit with a human.

## Request path

1. **Gateway** (`api.py`, `access.py`). The phone holds a device cookie issued when it joined through the session QR code. Rate limits key on that device, not on IP, because a whole campus or village sits behind one NAT address. Accepted messages enter a bounded queue.
2. **Workers** (`pipeline.py`). A fixed pool (16 by default) runs the workflow. The pool size, not the crowd size, sets the load on the model provider. When the pool is busy, people see their queue position. That is backpressure, and it is the difference between slowing down and falling over.
3. **Workflow** (`workflow.py`). A Microsoft Agent Framework graph:
   - switch-case routing after the guard (injection goes to a human review queue) and after triage (information requests go to the info desk),
   - fan-out of geo-location and verification in parallel, then fan-in to triage,
   - the incident record as the shared blackboard, one field per agent so no agent overwrites another,
   - the commander composing the plan from the whole board.
4. **Human in the loop.** Every plan stops at `awaiting_approval`. The officer approves (the unit is dispatched) or rejects (the unit is released). Both outcomes are the router's reward.
5. **Scheduler.** A dispatched unit returns after its trip (time-compressed in the exercise) and is given to the most urgent case still waiting for one.

## Model gateway (`llm.py`)

Every model call passes through one function, which:

- reserves tokens from the organisation's allocation before the call and charges the true usage after (`ledger.py`). Pay As You Go Azure has no hard spending limit, so this is the real cap;
- tries the chosen tier, then the other tier, then the fallback lane, skipping any lane whose circuit breaker is open after repeated throttling or errors;
- falls back to the SOP rules (`rules.py`) when the allocation is spent or every lane is down. The room degrades, it does not stop;
- rejects replies that do not parse as JSON, so free text never flows downstream;
- meters tokens, latency and result per agent and lane for Prometheus.

## Why three agents and not nine

| Work | Who does it | Why |
|---|---|---|
| Read free text in four languages | Model | Language is what models are for |
| Find the village | Code | Exact lookup; a model would guess |
| Spot duplicates | Code | String similarity is deterministic |
| Score danger | Code rubric, model may adjust one level | The rubric is policy; the model adds judgement within a fence |
| Pick a boat | Code | Distance and availability are facts |
| Write the reply | Model | Tone, language, a safety instruction that fits |

Each model call costs money, latency and a chance of being wrong. Spend them only where judgement or language is needed.

## Guardrails (`guardrails/`)

| Risk | Control | Where |
|---|---|---|
| Personal numbers reaching a model or a reply | Redact on the way in, keep in an officer-only vault; scan every reply on the way out | `pii.py`, `policy.clean_reply` |
| Prompt injection | Azure Prompt Shields plus a local multilingual screen; flagged messages go to a human, not the bin | `injection.py` |
| A model over-ruling policy | Priority clamped to one level from the SOP rubric | `policy.bound_priority` |
| Influence ("MLA office calling") | Detected, logged, ignored for priority | `policy.pressure_detected` |
| Casualty figures and promises | Stripped from replies; prompts forbid them | `policy.clean_reply`, `prompts.py` |
| Runaway spend | Allocation ledger plus TPM cap on each deployment | `ledger.py`, deployment quota |
| Unaccountable decisions | Append-only audit log, also emitted as JSON lines to Log Analytics | `policy.AuditLog` |

## Observability

- **Metrics** (`/metrics`, Prometheus, Grafana): traffic, refusals, tokens per agent, model latency, SOS-to-plan latency, guardrail hits, circuit state, queue depth, allocation remaining. Metrics tell you that something is wrong.
- **Logs** (stdout to Log Analytics): one JSON audit line per decision. Logs tell you where.
- **Traces** (Azure Monitor, shown in Foundry): one trace per request. Traces tell you why.

## Known limitations

- Single replica: the incident board, ledger and router live in memory. To scale out, move them to Redis or Cosmos DB and make the ledger an atomic counter.
- The gazetteer is a small exercise dataset with approximate coordinates.
- The verifier cannot detect a convincing lie. Only a human callback can.
- The router's reward is officer approval, so careless approvals train it badly.
