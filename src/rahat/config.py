"""
Runtime settings, read once from the environment.

Every knob that differs between a student laptop, CI and the live demo lives
here, so nothing else in the codebase reads os.environ directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Which model lane serves the agents: foundry, groq, nvidia, local, fake
    provider: str = field(default_factory=lambda: os.getenv("PROVIDER", "fake").strip().lower())
    # Optional second lane used when the primary is throttled or down
    fallback_provider: str = field(
        default_factory=lambda: os.getenv("FALLBACK_PROVIDER", "").strip().lower()
    )
    # Two model tiers. On Foundry these are DEPLOYMENT names, not model names.
    model_fast: str = field(default_factory=lambda: os.getenv("MODEL_FAST", ""))
    model_strong: str = field(default_factory=lambda: os.getenv("MODEL_STRONG", ""))
    # Reasoning models (gpt-5 family, gpt-oss) think before answering. Thinking tokens
    # are billed, so the fast tier thinks as little as possible.
    reasoning_fast: str = field(default_factory=lambda: os.getenv("REASONING_FAST", ""))
    reasoning_strong: str = field(default_factory=lambda: os.getenv("REASONING_STRONG", ""))
    json_mode: bool = field(default_factory=lambda: os.getenv("JSON_MODE", "1") == "1")
    request_timeout_s: float = field(default_factory=lambda: _float("REQUEST_TIMEOUT_S", 25.0))

    # The Neerdhara Systems token allocation. This is the real spend cap.
    org_name: str = field(default_factory=lambda: os.getenv("ORG_NAME", "Neerdhara Systems"))
    allocation_tokens: int = field(default_factory=lambda: _int("ALLOCATION_TOKENS", 1_500_000))
    low_budget_ratio: float = field(default_factory=lambda: _float("LOW_BUDGET_RATIO", 0.10))

    # Access control
    admin_token: str = field(default_factory=lambda: os.getenv("ADMIN_TOKEN", "change-me"))
    metrics_token: str = field(default_factory=lambda: os.getenv("METRICS_TOKEN", ""))
    session_minutes: int = field(default_factory=lambda: _int("SESSION_MINUTES", 90))
    sos_cooldown_s: int = field(default_factory=lambda: _int("SOS_COOLDOWN_S", 20))
    sos_max_per_device: int = field(default_factory=lambda: _int("SOS_MAX_PER_DEVICE", 6))

    # Concurrency: how many SOS messages are processed at once. The rest queue.
    workers: int = field(default_factory=lambda: _int("WORKERS", 12))
    queue_max: int = field(default_factory=lambda: _int("QUEUE_MAX", 800))

    # Allocator: never hold a unit further away than this. The case waits for a nearer unit instead.
    max_eta_min: int = field(default_factory=lambda: _int("MAX_ETA_MIN", 120))

    # Drill
    drill_seed: int = field(default_factory=lambda: _int("DRILL_SEED", 2026))
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", ""))

    # Learning-outcome snapshots: an Azure Blob container (live, managed identity) or a local folder.
    # With neither set, answers stay in memory only and the admin endpoint still works.
    outcomes_blob_url: str = field(default_factory=lambda: os.getenv("OUTCOMES_BLOB_URL", "").strip())
    outcomes_dir: str = field(default_factory=lambda: os.getenv("OUTCOMES_DIR", "").strip())
    outcomes_save_delay_s: float = field(default_factory=lambda: _float("OUTCOMES_SAVE_DELAY_S", 10.0))


settings = Settings()
