"""
RAHAT HTTP API. One FastAPI app serves the citizen page, the officer console,
the control-room wall, the JSON API, the live event stream and /metrics.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from contextlib import asynccontextmanager
from importlib import resources
from typing import Literal

import segno
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from . import __version__, metrics
from .access import access
from .agents import specialists
from .config import settings
from .drill import scoring
from .drill.control import drill
from .events import bus
from .guardrails import pii
from .guardrails.policy import audit
from .lanes import get_lane
from .llm import breaker, ledger
from .models import SOS, Status
from .pipeline import pipeline, wall_stats
from .router import router
from .store import store
from .telemetry import setup_telemetry
from .workflow import gauges, registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("rahat.api")

WEB = resources.files("rahat").joinpath("web")
COOKIE_DEV, COOKIE_ROLE = "rahat_dev", "rahat_role"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_telemetry(app)
    await pipeline.start()
    log.info("RAHAT %s up. lane=%s fallback=%s allocation=%s", __version__, settings.provider,
             settings.fallback_provider or "-", settings.allocation_tokens)
    yield
    await pipeline.stop()


# App assets revalidate on every load (ETag, so an unchanged file is a 304) and a new deploy shows at once.
# Only the vendored libraries, which never change, may sit in the browser cache.
REVALIDATE = "no-cache"
VENDOR_CACHE = "public, max-age=31536000"


class AppStatic(StaticFiles):
    def file_response(self, full_path, stat_result, scope, status_code=200):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        rel = scope["path"].replace("\\", "/")
        if "/vendor/" in rel or rel.startswith("vendor/"):
            resp.headers["Cache-Control"] = VENDOR_CACHE
        elif rel.endswith((".js", ".css", ".html")):
            resp.headers["Cache-Control"] = REVALIDATE
        return resp


app = FastAPI(title="RAHAT", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
app.mount("/static", AppStatic(directory=str(WEB)), name="static")


@app.middleware("http")
async def count_requests(request: Request, call_next):
    resp = await call_next(request)
    route = request.scope.get("route")
    path = getattr(route, "path", "unmatched")
    if not path.startswith("/static"):
        metrics.HTTP_REQUESTS.labels(path, str(resp.status_code)).inc()
    return resp


# ------------------------------------------------------------------ auth helpers

def role_of(request: Request) -> str:
    raw = request.cookies.get(COOKIE_ROLE, "")
    if raw.startswith("admin:") and access.valid_admin(raw[6:]):
        return "admin"
    if raw.startswith("officer:") and access.valid_officer(raw[8:]):
        return "officer"
    return "public"


def need_officer(request: Request) -> str:
    r = role_of(request)
    if r not in ("admin", "officer"):
        raise HTTPException(401, "officer login required")
    return r


def need_admin(request: Request) -> str:
    if role_of(request) != "admin":
        raise HTTPException(401, "admin login required")
    return "admin"


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _page(name: str) -> HTMLResponse:
    return HTMLResponse(WEB.joinpath(name).read_text(encoding="utf-8"), headers={"Cache-Control": REVALIDATE})


# ------------------------------------------------------------------ pages

@app.get("/", include_in_schema=False)
def home():
    return _page("index.html")


@app.get("/j/{code}", include_in_schema=False)
def join(code: str, request: Request):
    if not access.valid_code(code):
        return RedirectResponse("/?closed=1", status_code=303)
    dev = access.join(request.cookies.get(COOKIE_DEV))
    resp = RedirectResponse("/sos", status_code=303)
    resp.set_cookie(COOKIE_DEV, dev.id, httponly=True, samesite="lax", secure=_secure(request),
                    max_age=settings.session_minutes * 60)
    return resp


@app.get("/sos", include_in_schema=False)
def sos_page():
    return _page("sos.html")


@app.get("/console", include_in_schema=False)
def console_page(request: Request):
    if role_of(request) == "public":
        return _page("login.html")
    return _page("console.html")


@app.get("/wall", include_in_schema=False)
def wall_page(request: Request):
    if role_of(request) == "public":
        return _page("login.html")
    return _page("wall.html")


class Login(BaseModel):
    secret: str = Field(min_length=1, max_length=200)


@app.post("/api/login")
def login(body: Login, request: Request, response: Response):
    s = body.secret.strip()
    if access.valid_admin(s):
        value, role = f"admin:{s}", "admin"
    elif access.valid_officer(s):
        value, role = f"officer:{s.upper()}", "officer"
    else:
        raise HTTPException(401, "wrong code")
    response.set_cookie(COOKIE_ROLE, value, httponly=True, samesite="strict", secure=_secure(request),
                        max_age=6 * 3600)
    audit.write("login", None, actor=role)
    return {"role": role}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_ROLE)
    return {"ok": True}


# ------------------------------------------------------------------ participant API

def _device(request: Request):
    dev = access.device(request.cookies.get(COOKIE_DEV))
    if not dev or not access.session or dev.session != access.session.code:
        raise HTTPException(403, "join the exercise by scanning the QR code")
    return dev


@app.get("/api/me")
def me(request: Request):
    dev = access.device(request.cookies.get(COOKIE_DEV))
    s = access.session
    live = bool(s and s.live)
    if not dev or not s or dev.session != s.code:
        return {"joined": False, "live": live}
    p = drill.persona(dev.persona_index)
    mine = [i for i in store.all() if i.sos.device_id == dev.id]
    return {
        "joined": True,
        "live": live,
        "exercise": s.name,
        "started": drill.started_at is not None and drill.ended_at is None,
        "ended": drill.ended_at is not None,
        "banner": drill.banner,
        "max_chars": 60 if drill.network_degraded else 600,
        "persona": p.public(),
        "sent": dev.sent,
        "limit": settings.sos_max_per_device,
        "messages": [_citizen_view(i) for i in sorted(mine, key=lambda i: i.sos.received_at, reverse=True)],
    }


class SOSIn(BaseModel):
    text: str = Field(min_length=3, max_length=600)


@app.post("/api/sos")
async def send_sos(body: SOSIn, request: Request):
    dev = _device(request)
    if not access.session.live or drill.ended_at:
        metrics.SOS_REJECTED.labels("closed").inc()
        raise HTTPException(409, "the exercise is closed")
    if drill.started_at is None:
        metrics.SOS_REJECTED.labels("not_started").inc()
        raise HTTPException(409, "the exercise has not started yet, wait for the siren")
    if drill.network_degraded and len(body.text) > 60:
        metrics.SOS_REJECTED.labels("network").inc()
        raise HTTPException(413, "network degraded: message must be under 60 characters")
    why = access.check_send(dev)
    if why:
        metrics.SOS_REJECTED.labels("rate_limit").inc()
        raise HTTPException(429, why)
    p = drill.persona(dev.persona_index)
    sos = SOS(text=body.text, device_id=dev.id, persona_id=p.id, channel="web")
    try:
        inc, pos = pipeline.submit(sos)
    except OverflowError:
        metrics.SOS_REJECTED.labels("queue_full").inc()
        raise HTTPException(503, "control room is at capacity, try again in a minute") from None
    access.mark_sent(dev)
    return {"id": sos.id, "position": pos}


def _citizen_view(inc) -> dict:
    stage = {
        Status.QUEUED: "Received. Waiting for the control room.",
        Status.PROCESSING: "Being read by the control room.",
        Status.AWAITING_APPROVAL: "Help is being arranged. An officer is confirming.",
        Status.DISPATCHED: "A rescue unit has been dispatched.",
        Status.INFO_SENT: "Information sent.",
        Status.REJECTED: "An officer reviewed this. Please send a new SOS if you are in danger.",
        Status.BLOCKED: "Under manual review by an officer.",
        Status.FAILED: "We could not process this. Please send it again.",
    }[inc.status]
    return {
        "id": inc.sos.id,
        "text": pii.redact(inc.sos.text)[0],
        "status": inc.status.value,
        "stage": stage,
        "position": pipeline.position(inc.sos.id),
        "reply": inc.plan.citizen_reply if inc.plan and inc.status != Status.REJECTED else None,
        "eta_min": inc.assignment.eta_min if inc.assignment and inc.status == Status.DISPATCHED else None,
    }


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=300)


@app.post("/api/ask")
async def ask(body: AskIn, request: Request):
    dev = _device(request)
    why = access.check_ask(dev)
    if why:
        raise HTTPException(429, why)
    access.mark_asked(dev)
    clean, n = pii.redact(body.question)
    st = store.stats()
    facts = {
        "received": st["received"],
        "dispatched": st["dispatched"],
        "units_available": sum(v.get("available", 0) for v in registry.summary().values()),
        "relief_camps": [c["name"] for c in registry.camps],
        "helpline": "1070",
        "river_gauges": gauges.state,
    }
    answer, res = await specialists.public_info(clean, facts)
    persona = drill.persona(dev.persona_index)
    leaked = pii.contains_pii(answer)
    audit.write("public_question", None, actor=persona.role, redacted=n, leaked=leaked)
    bus.publish("agent", {"sos_id": "INFO", "agent": "info_desk", "action": "answered",
                          "detail": answer[:140], "tokens": res.tokens, "ms": res.ms, "level": "info",
                          "lane": res.lane, "tier": res.tier, "ts": time.time()})
    return {"answer": answer}


# ------------------------------------------------------------------ officer API

@app.get("/api/incidents")
def incidents(request: Request, status: str | None = None, limit: int = 200, _: str = Depends(need_officer)):
    items = store.all()
    if status:
        wanted = set(status.split(","))
        items = [i for i in items if i.status.value in wanted]
    items.sort(key=lambda i: ((i.triage.priority.rank if i.triage else 5), i.sos.received_at))
    out = []
    for i in items[:limit]:
        d = i.model_dump(mode="json")
        d["sos"]["text"] = pii.redact(i.sos.text)[0]
        d["callback"] = pii.vault.masked(i.sos.id)
        d.pop("persona_id", None)
        out.append(d)
    return out


class Decision(BaseModel):
    approve: bool


@app.post("/api/incidents/{sos_id}/decision")
async def decide(sos_id: str, body: Decision, role: str = Depends(need_officer)):
    try:
        inc = pipeline.decide(sos_id, body.approve, role)
    except KeyError:
        raise HTTPException(404, "no such SOS") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"id": sos_id, "status": inc.status.value}


@app.get("/api/stream")
async def stream(request: Request, _: str = Depends(need_officer)):
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield bus.sse(msg)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/state")
def state(role: str = Depends(need_officer)):
    s = access.session
    lane = get_lane(settings.provider)
    return {
        "role": role,
        "org": settings.org_name,
        "lane": {"key": lane.key, "name": lane.name, "paid": lane.paid,
                 "fallback": settings.fallback_provider or None},
        "ledger": ledger.snapshot(),
        "breakers": breaker.state(),
        "assets": registry.snapshot(),
        "camps": registry.camps,
        "gauges": gauges.state,
        "stats": wall_stats(),
        "router": router.snapshot(),
        "drill": {"started_at": drill.started_at, "ended_at": drill.ended_at,
                  "network_degraded": drill.network_degraded, "injects": drill.injects[-20:]},
        "session": {"code": s.code, "live": s.live, "expires_at": s.expires_at,
                    "joined": len(access.devices)} if s else None,
        "incidents": [i.public_view() for i in store.all()],
        "queue": pipeline.queue.qsize() if pipeline.queue else 0,
        "in_flight": pipeline.in_flight,
    }


@app.get("/api/audit")
def audit_log(limit: int = 100, _: str = Depends(need_officer)):
    return audit.recent(limit)


# ------------------------------------------------------------------ admin API

class SessionIn(BaseModel):
    minutes: int = Field(default=90, ge=5, le=240)


@app.post("/api/admin/session")
async def open_session(body: SessionIn, request: Request, _: str = Depends(need_admin)):
    s = access.open_session(body.minutes)
    audit.write("session_opened", None, actor="admin", minutes=body.minutes)
    return {"code": s.code, "officer_code": s.officer_code, "join_url": _join_url(request, s.code),
            "expires_at": s.expires_at}


def _join_url(request: Request, code: str) -> str:
    base = settings.public_base_url or str(request.base_url).rstrip("/")
    return f"{base}/j/{code}"


@app.get("/api/admin/qr.svg")
def qr(request: Request, _: str = Depends(need_admin)):
    if not access.session:
        raise HTTPException(404, "no session")
    buf = io.BytesIO()
    segno.make(_join_url(request, access.session.code), error="m").save(buf, kind="svg", scale=10, border=2,
                                                                         dark="#111", light=None)
    return Response(buf.getvalue(), media_type="image/svg+xml")


@app.post("/api/admin/session/close")
def close_session(_: str = Depends(need_admin)):
    access.close_session()
    audit.write("session_closed", None, actor="admin")
    return {"ok": True}


InjectKind = Literal["start", "river_surge", "network_on", "network_off", "boat_failure", "vip",
                     "second_wave", "end"]


class InjectIn(BaseModel):
    river: str = "Ravi"
    count: int = Field(default=60, ge=1, le=300)


@app.post("/api/admin/inject/{kind}")
async def inject(kind: InjectKind, body: InjectIn | None = None, _: str = Depends(need_admin)):
    body = body or InjectIn()
    if kind == "start":
        drill.start()
        return {"ok": True}
    if kind == "end":
        drill.end()
        access.close_session()
        result = scoring.score(store.all(), drill.personas, ledger.snapshot())
        bus.publish("score", result)
        return result
    if drill.started_at is None:
        raise HTTPException(409, "start the exercise first")
    if kind == "river_surge":
        return drill.river_surge(gauges, body.river)
    if kind == "network_on":
        return drill.degrade_network(True)
    if kind == "network_off":
        return drill.degrade_network(False)
    if kind == "boat_failure":
        return await drill.boat_failure(pipeline)
    if kind == "vip":
        return drill.vip_pressure(pipeline)
    if kind == "second_wave":
        return await drill.second_wave(pipeline, body.count)
    raise HTTPException(400, "unknown inject")


@app.get("/api/admin/score")
def score_now(_: str = Depends(need_admin)):
    return scoring.score(store.all(), drill.personas, ledger.snapshot())


class ResetIn(BaseModel):
    allocation: int | None = Field(default=None, ge=10_000, le=50_000_000)


@app.post("/api/admin/reset")
async def reset(body: ResetIn | None = None, _: str = Depends(need_admin)):
    """Wipe the room for a fresh run. Never touches the code or the deployment."""
    store.clear()
    drill.reset()
    pii.vault.clear()
    router.reset()
    gauges.reset()
    bus.clear()
    ledger.reset(body.allocation if body else None)
    for a in registry.assets.values():
        a.status, a.assigned_to, a.trips = "available", None, 0
    audit.write("room_reset", None, actor="admin")
    return {"ok": True, "allocation": ledger.allocation}


# ------------------------------------------------------------------ ops

@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True, "version": __version__}


@app.get("/readyz", include_in_schema=False)
def readyz():
    ready = pipeline.queue is not None
    return JSONResponse({"ready": ready, "lane": settings.provider}, status_code=200 if ready else 503)


@app.get("/metrics", include_in_schema=False)
def prom(request: Request):
    if settings.metrics_token:
        if request.headers.get("authorization", "") != f"Bearer {settings.metrics_token}":
            raise HTTPException(401, "metrics token required")
    return Response(generate_latest(metrics.REGISTRY), media_type=CONTENT_TYPE_LATEST)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(str(WEB.joinpath("favicon.svg")), media_type="image/svg+xml")
