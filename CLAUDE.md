# RAHAT, notes for Claude Code

A multi-agent flood control room. Python 3.12, FastAPI, Microsoft Agent Framework, OpenAI-compatible model lanes.

## Commands
- Run locally: `uvicorn rahat.api:app --reload` (PROVIDER=fake needs no key)
- Tests: `pytest -q` (all offline, must stay offline)
- Lint: `ruff check src tests scripts` (ruff is pinned in pyproject.toml)
- Rehearsal: `python scripts/simulate_class.py --admin $ADMIN_TOKEN --students 60 --minutes 3`
- Eval gate: `python scripts/eval_gate.py rehearsal.json`
- Lane check: `python scripts/check_lanes.py`

## Rules for changes
- Exact work stays in code. Only reading free text and writing text go to a model.
- Every model call goes through `llm.complete_json()` with a rules fallback. Never call a client directly.
- Every agent output is validated into a pydantic model before it is used.
- No PII reaches a model or a reply: redact on the way in, `clean_reply()` on the way out.
- The Triage agent may move the SOP priority by one level at most (`bound_priority`). Do not widen this.
- Influence, rank or money never changes priority.
- Prompts are versioned in `agents/prompts.py`; bump `PROMPT_VERSION` when you change one.
- Metrics labels stay low-cardinality. Never put an SOS id or device id in a label.
- New behaviour comes with a test that runs on the fake lane.

## Never
- Commit `.env`, keys, tokens, or `infra/azure/config.sh`.
- Put the live demo URL in the repository. The README carries a placeholder on purpose.
- Add AI co-author trailers to commits (a commit-msg hook strips them).
