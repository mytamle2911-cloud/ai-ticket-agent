"""Server-side shared-code sessions for the hosted academic demo."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from flask import request


COOKIE_NAME = "ticket_agent_demo_session"
SESSION_SECONDS = 4 * 60 * 60
_DISPATCH_AUTH: ContextVar[bool] = ContextVar("demo_dispatch_auth", default=False)


class DemoAuthError(ValueError):
    """Missing or invalid hosted-demo authentication configuration."""


def _auth_secret() -> bytes:
    secret = os.getenv("DEMO_AUTH_SECRET", "")
    if len(secret) < 32:
        raise DemoAuthError("Hosted demo authentication is not configured. Set DEMO_AUTH_SECRET to at least 32 characters.")
    return secret.encode("utf-8")


def _access_code() -> str:
    code = os.getenv("DEMO_ACCESS_CODE", "")
    if len(code) < 16:
        raise DemoAuthError("Hosted demo access is not configured. Set DEMO_ACCESS_CODE to at least 16 characters.")
    return code


def verify_access_code(candidate: object) -> bool:
    if not isinstance(candidate, str) or len(candidate) > 256:
        candidate = ""
    # Validate configuration before comparison, and use constant-time comparison.
    expected = _access_code().encode("utf-8")
    supplied = candidate.encode("utf-8")
    return hmac.compare_digest(hashlib.sha256(expected).digest(), hashlib.sha256(supplied).digest())


def _sign(encoded_payload: str) -> str:
    return hmac.new(_auth_secret(), encoded_payload.encode("ascii"), hashlib.sha256).hexdigest()


def issue_session(now: int | None = None) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = json.dumps({"v": 1, "iat": issued_at, "exp": issued_at + SESSION_SECONDS,
                          "nonce": secrets.token_urlsafe(12)}, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"v1.{encoded}.{_sign('v1.' + encoded)}"


def validate_session(token: object, now: int | None = None) -> bool:
    if not isinstance(token, str) or len(token) > 512:
        return False
    try:
        version, encoded, signature = token.split(".")
        if version != "v1" or not hmac.compare_digest(_sign(f"{version}.{encoded}"), signature):
            return False
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw.decode("utf-8"))
        current = int(time.time() if now is None else now)
        issued_at, expires_at = payload.get("iat"), payload.get("exp")
        return (
            payload.get("v") == 1
            and type(issued_at) is int and type(expires_at) is int
            and issued_at <= current + 30
            and expires_at > current
            and expires_at - issued_at == SESSION_SECONDS
            and isinstance(payload.get("nonce"), str) and len(payload["nonce"]) >= 8
        )
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, DemoAuthError):
        return False


def request_is_authenticated() -> bool:
    if _DISPATCH_AUTH.get():
        return True
    try:
        return validate_session(request.cookies.get(COOKIE_NAME))
    except RuntimeError:  # Called outside a Flask request context.
        return False


@contextmanager
def trusted_dispatch() -> Iterator[None]:
    """Allow only the dispatcher to call protected Flask routes internally."""
    token = _DISPATCH_AUTH.set(True)
    try:
        yield
    finally:
        _DISPATCH_AUTH.reset(token)


def live_ai_enabled() -> bool:
    return os.getenv("ENABLE_LIVE_AI", "").strip().lower() == "true"


def validate_configuration() -> None:
    _access_code()
    _auth_secret()
