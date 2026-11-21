"""
Model lanes. One socket, several plugs.

Every provider used here speaks the OpenAI chat completions protocol, so a lane
is just three facts: a base URL, a credential, and model names for the two
tiers. Agents never name a provider. Change PROVIDER in .env, nothing else.

    foundry   Microsoft Foundry (paid, the live demo lane)
    groq      Groq free tier
    nvidia    NVIDIA build.nvidia.com free tier (40 requests per minute)
    local     Ollama on your own machine, no key, no limit
    fake      Deterministic offline model. Used by CI, tests and first runs.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from openai import AsyncOpenAI


class LaneError(RuntimeError):
    pass


@dataclass(frozen=True)
class Lane:
    key: str
    name: str
    base_url_env: str | None
    base_url: str | None
    key_env: str | None
    fast: str
    strong: str
    json_mode: bool
    paid: bool


LANES: dict[str, Lane] = {
    "foundry": Lane(
        key="foundry",
        name="Microsoft Foundry",
        base_url_env="AZURE_OPENAI_ENDPOINT",  # https://<resource>.openai.azure.com/openai/v1/
        base_url=None,
        key_env="AZURE_OPENAI_API_KEY",
        fast="rahat-fast",  # deployment names
        strong="rahat-strong",
        json_mode=True,
        paid=True,
    ),
    "groq": Lane(
        key="groq",
        name="Groq",
        base_url_env=None,
        base_url="https://api.groq.com/openai/v1",
        key_env="GROQ_API_KEY",
        fast="openai/gpt-oss-20b",
        strong="openai/gpt-oss-120b",
        json_mode=True,
        paid=False,
    ),
    "nvidia": Lane(
        key="nvidia",
        name="NVIDIA API Catalog",
        base_url_env=None,
        base_url="https://integrate.api.nvidia.com/v1",
        key_env="NVIDIA_API_KEY",
        fast="meta/llama-3.1-8b-instruct",
        strong="meta/llama-3.3-70b-instruct",
        json_mode=False,
        paid=False,
    ),
    "local": Lane(
        key="local",
        name="Ollama (local)",
        base_url_env=None,
        base_url="http://localhost:11434/v1",
        key_env=None,
        fast="llama3.2",
        strong="llama3.2",
        json_mode=True,
        paid=False,
    ),
    "fake": Lane(
        key="fake",
        name="Offline fake model",
        base_url_env=None,
        base_url=None,
        key_env=None,
        fast="fake-fast",
        strong="fake-strong",
        json_mode=True,
        paid=False,
    ),
}


def get_lane(key: str) -> Lane:
    if key not in LANES:
        raise LaneError(f"PROVIDER={key!r} is not a lane. Valid: {', '.join(LANES)}")
    return LANES[key]


class _ManagedIdentityToken:
    """Entra ID token for Foundry, so no API key lives in the container."""

    def __init__(self) -> None:
        from azure.identity import DefaultAzureCredential  # optional dependency

        self._cred = DefaultAzureCredential()
        self._token = None

    def get(self) -> str:
        if self._token is None or self._token.expires_on - time.time() < 300:
            self._token = self._cred.get_token("https://cognitiveservices.azure.com/.default")
        return self._token.token


def normalise_foundry_endpoint(url: str) -> str:
    """Accept whatever the portal shows (bare host, /responses, /openai/...) and return the v1 base."""
    url = url.strip().rstrip("/")
    for tail in ("/responses", "/chat/completions"):
        if url.endswith(tail):
            url = url[: -len(tail)]
    if "/openai" in url:
        url = url[: url.index("/openai")]
    return url + "/openai/v1/"


_clients: dict[str, tuple[AsyncOpenAI, float]] = {}
_mi: _ManagedIdentityToken | None = None


def client_for(lane: Lane, timeout_s: float) -> AsyncOpenAI:
    """Build or reuse the async client for a lane."""
    global _mi
    if lane.key == "fake":
        raise LaneError("the fake lane has no network client")

    use_mi = lane.key == "foundry" and os.getenv("AZURE_USE_MANAGED_IDENTITY") == "1"
    cached = _clients.get(lane.key)
    if cached and (not use_mi or time.time() < cached[1]):
        return cached[0]

    base_url = os.getenv(lane.base_url_env) if lane.base_url_env else lane.base_url
    if base_url and lane.key == "foundry":
        base_url = normalise_foundry_endpoint(base_url)
    if not base_url:
        raise LaneError(f"{lane.name}: set {lane.base_url_env} in .env")

    if use_mi:
        _mi = _mi or _ManagedIdentityToken()
        api_key = _mi.get()
        expires = time.time() + 600
    elif lane.key_env:
        api_key = os.getenv(lane.key_env, "")
        if not api_key:
            raise LaneError(f"{lane.name}: set {lane.key_env} in .env")
        expires = float("inf")
    else:
        api_key = "not-needed"
        expires = float("inf")

    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=0)
    _clients[lane.key] = (client, expires)
    return client
