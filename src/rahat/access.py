"""
Who may send what, and how often.

  session code   The QR on the slide carries a code that expires. A photo of
                 the slide forwarded after class opens nothing.
  device id      A random cookie per phone. Rate limits key on this, NOT on
                 IP address: the whole campus sits behind one NAT address, so
                 an IP limit would lock out the entire class at once.
  roles          admin (instructor), officer (approves plans), participant.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field

from .config import settings


@dataclass
class Session:
    code: str
    officer_code: str
    created_at: float
    expires_at: float
    open: bool = True
    name: str = "Exercise JALPRALAY"

    @property
    def live(self) -> bool:
        return self.open and time.time() < self.expires_at


@dataclass
class Device:
    id: str
    session: str
    persona_index: int
    joined_at: float = field(default_factory=time.time)
    sent: int = 0
    last_sent: float = 0.0
    asked: int = 0
    last_asked: float = 0.0


class Access:
    def __init__(self) -> None:
        self.session: Session | None = None
        self.devices: dict[str, Device] = {}
        self._next_index = 0
        self._lock = threading.Lock()

    # sessions -------------------------------------------------------------
    def open_session(self, minutes: int | None = None) -> Session:
        mins = minutes or settings.session_minutes
        code = "JAL-" + "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(4))
        officer = "OFF-" + "".join(secrets.choice("23456789") for _ in range(4))
        now = time.time()
        with self._lock:
            self.session = Session(code, officer, now, now + mins * 60)
            self.devices.clear()
            self._next_index = 0
        return self.session

    def close_session(self) -> None:
        if self.session:
            self.session.open = False

    def valid_code(self, code: str) -> bool:
        s = self.session
        return bool(s and s.live and hmac.compare_digest(code.strip().upper(), s.code))

    def valid_officer(self, code: str) -> bool:
        s = self.session
        return bool(s and s.live and hmac.compare_digest(code.strip().upper(), s.officer_code))

    @staticmethod
    def valid_admin(token: str) -> bool:
        return bool(token) and hmac.compare_digest(token, settings.admin_token)

    # devices --------------------------------------------------------------
    def join(self, device_id: str | None) -> Device:
        with self._lock:
            if device_id and device_id in self.devices:
                return self.devices[device_id]
            did = secrets.token_urlsafe(12)
            dev = Device(did, self.session.code if self.session else "", self._next_index)
            self._next_index += 1
            self.devices[did] = dev
            return dev

    def device(self, device_id: str | None) -> Device | None:
        return self.devices.get(device_id or "")

    def check_send(self, dev: Device) -> str | None:
        """None if allowed, else a human reason."""
        now = time.time()
        if dev.sent >= settings.sos_max_per_device:
            return f"limit reached: {settings.sos_max_per_device} messages per phone in this exercise"
        wait = settings.sos_cooldown_s - (now - dev.last_sent)
        if dev.sent and wait > 0:
            return f"please wait {int(wait) + 1}s before sending again"
        return None

    def mark_sent(self, dev: Device) -> None:
        dev.sent += 1
        dev.last_sent = time.time()

    def check_ask(self, dev: Device) -> str | None:
        if dev.asked >= 12:
            return "question limit reached for this exercise"
        if dev.asked and time.time() - dev.last_asked < 8:
            return "please wait a few seconds"
        return None

    def mark_asked(self, dev: Device) -> None:
        dev.asked += 1
        dev.last_asked = time.time()


access = Access()
