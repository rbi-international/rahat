"""
Prometheus metrics. Numbers only: counts, latencies, gauges.

Metrics tell you THAT something is wrong (alert). Logs and traces tell you
WHERE and WHY. Keep labels low-cardinality: never put an SOS id or a user id in
a label, or the time series count explodes.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

SOS_RECEIVED = Counter("rahat_sos_received_total", "SOS messages accepted", ["channel"], registry=REGISTRY)
SOS_REJECTED = Counter("rahat_sos_rejected_total", "SOS requests refused at the door", ["reason"], registry=REGISTRY)
SOS_OUTCOME = Counter("rahat_sos_outcome_total", "Final pipeline outcome", ["outcome"], registry=REGISTRY)
PRIORITY = Counter("rahat_triage_priority_total", "Triage decisions by priority", ["priority"], registry=REGISTRY)
GUARD_BLOCKS = Counter("rahat_guardrail_blocks_total", "Guardrail interventions", ["guard"], registry=REGISTRY)
PII_REDACTIONS = Counter("rahat_pii_redactions_total", "PII spans redacted", ["kind"], registry=REGISTRY)

LLM_CALLS = Counter("rahat_llm_calls_total", "Model calls", ["agent", "lane", "tier", "result"], registry=REGISTRY)
LLM_TOKENS = Counter("rahat_llm_tokens_total", "Tokens charged to the allocation", ["agent", "lane"], registry=REGISTRY)
LLM_LATENCY = Histogram(
    "rahat_llm_latency_seconds",
    "Model call latency",
    ["agent", "lane"],
    buckets=(0.25, 0.5, 1, 2, 3, 5, 8, 13, 21),
    registry=REGISTRY,
)
PIPELINE_LATENCY = Histogram(
    "rahat_pipeline_seconds",
    "SOS received to plan ready",
    buckets=(1, 2, 4, 8, 15, 30, 60, 120, 240),
    registry=REGISTRY,
)
BREAKER_OPEN = Gauge("rahat_circuit_open", "1 when a lane/tier circuit is open", ["lane", "tier"], registry=REGISTRY)
QUEUE_DEPTH = Gauge("rahat_queue_depth", "SOS waiting for a worker", registry=REGISTRY)
IN_FLIGHT = Gauge("rahat_in_flight", "SOS being processed", registry=REGISTRY)
ALLOCATION_REMAINING = Gauge("rahat_allocation_remaining_tokens", "Neerdhara allocation left", registry=REGISTRY)
ALLOCATION_USED = Gauge("rahat_allocation_used_tokens", "Neerdhara allocation used", registry=REGISTRY)
ROUTER_CHOICE = Counter("rahat_router_choice_total", "Bandit router tier picks", ["context", "tier"], registry=REGISTRY)
OFFICER_DECISION = Counter("rahat_officer_decision_total", "Human approvals and rejections", ["decision"], registry=REGISTRY)
HTTP_REQUESTS = Counter("rahat_http_requests_total", "HTTP requests", ["route", "code"], registry=REGISTRY)
