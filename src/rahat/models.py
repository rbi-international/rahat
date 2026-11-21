"""
Typed contracts that flow between agents.

An agent's output is only trusted after it parses into one of these models.
A model reply that does not fit the schema is treated as a failure, never
passed downstream as free text.
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

WaterLevel = Literal["unknown", "ankle", "knee", "waist", "chest", "roof"]
Need = Literal["rescue", "medical", "food_water", "information", "other"]


class Priority(StrEnum):
    P1 = "P1"  # life threatening now
    P2 = "P2"  # serious, act within the hour
    P3 = "P3"  # needs help, not urgent
    P4 = "P4"  # information or no action

    @property
    def rank(self) -> int:
        return int(self.value[1])


class Status(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    BLOCKED = "blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    DISPATCHED = "dispatched"
    REJECTED = "rejected"
    INFO_SENT = "info_sent"
    FAILED = "failed"


class SOS(BaseModel):
    id: str = Field(default_factory=lambda: "SOS-" + uuid.uuid4().hex[:6].upper())
    text: str = Field(min_length=3, max_length=600)
    device_id: str = ""
    persona_id: str | None = None
    received_at: float = Field(default_factory=time.time)
    channel: Literal["web", "inject", "sim"] = "web"


class Extraction(BaseModel):
    """What the Intake agent reads out of a raw message."""

    language: str = "unknown"
    summary_en: str = ""
    people_count: int = Field(default=1, ge=0, le=500)
    vulnerable: list[str] = Field(default_factory=list)  # elderly, child, infant, pregnant, disabled, injured
    water_level: WaterLevel = "unknown"
    location_text: str = ""
    need: Need = "rescue"
    medical_detail: str = ""


class Location(BaseModel):
    resolved: bool = False
    village: str | None = None
    district: str | None = None
    lat: float | None = None
    lon: float | None = None
    river: str | None = None
    confidence: float = 0.0
    method: str = "none"


class Verification(BaseModel):
    genuine: bool = True
    duplicate_of: str | None = None
    flags: list[str] = Field(default_factory=list)


class Triage(BaseModel):
    priority: Priority = Priority.P3
    score: float = Field(default=0.0, ge=0, le=100)
    reason: str = ""
    model_tier: str = "rules"
    river_level: str = "normal"


class Assignment(BaseModel):
    asset_id: str | None = None
    asset_kind: str | None = None
    asset_label: str | None = None
    distance_km: float | None = None
    eta_min: int | None = None
    camp: str | None = None
    note: str = ""


class Plan(BaseModel):
    officer_brief: str = ""
    citizen_reply: str = ""


class AgentEvent(BaseModel):
    """One line on the live agent board."""

    ts: float = Field(default_factory=time.time)
    sos_id: str
    agent: str
    action: str
    detail: str = ""
    tokens: int = 0
    ms: int = 0
    level: Literal["info", "warn", "block", "ok"] = "info"


class Incident(BaseModel):
    sos: SOS
    status: Status = Status.QUEUED
    extraction: Extraction | None = None
    location: Location | None = None
    verification: Verification | None = None
    triage: Triage | None = None
    assignment: Assignment | None = None
    plan: Plan | None = None
    blocked_reason: str | None = None
    redactions: int = 0
    flags: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    llm_calls: int = 0
    degraded: bool = False
    finished_at: float | None = None
    decided_at: float | None = None

    def public_view(self) -> dict:
        """What the wall and the citizen see. Never includes raw text or PII."""
        loc = self.location
        return {
            "id": self.sos.id,
            "status": self.status.value,
            "priority": self.triage.priority.value if self.triage else None,
            "lat": loc.lat if loc and loc.resolved else None,
            "lon": loc.lon if loc and loc.resolved else None,
            "village": loc.village if loc else None,
            "people": self.extraction.people_count if self.extraction else None,
            "need": self.extraction.need if self.extraction else None,
            "asset": self.assignment.asset_label if self.assignment else None,
            "received_at": self.sos.received_at,
        }
