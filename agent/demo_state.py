"""Signed, browser-carried mutable demo state for stateless deployments.

The deployment filesystem contains read-only seed data. In production, every
request carries this signed snapshot and the server validates it before use.
Local development continues to use the existing JSON read/write functions.
"""

from __future__ import annotations

from agent.knowledge_governance import METADATA_KEYS, validate_metadata

import base64
import hashlib
import hmac
import json
import os
import re
import zlib
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator


_ROOT = Path(__file__).resolve().parents[1]
_STATE: ContextVar[dict | None] = ContextVar("ticket_agent_demo_state", default=None)
_STATUS_CODES = {1, 2, 3, 4, 5, 6}
_STATUS_LABELS = {"New", "In Progress", "On Hold", "Resolved", "Closed", "Cancelled"}
_MAX_TOKEN_CHARS = 1_500_000
_RESOLUTION_KEYS = {
    "resolution_id", "incident_number", "confirmed_root_cause", "actions_performed",
    "verification_evidence", "consultant_id", "confirmation_timestamp", "verified",
    "verification_source",
}
_ARTICLE_KEYS = {
    "article_id", "title", "source_incident", "source_resolution_id", "source_system",
    "issue_category", "error_signature", "symptoms", "confirmed_root_cause",
    "verified_troubleshooting_steps", "required_roles", "verification_method", "created_at",
    "created_by", "review_status", "approved_by", "approved_at", "published_at",
    "knowledge_source",
}


class DemoStateError(ValueError):
    """Invalid or unavailable browser demo state."""


def active_state() -> dict | None:
    return _STATE.get()


@contextmanager
def use_state(state: dict) -> Iterator[dict]:
    token = _STATE.set(state)
    try:
        yield state
    finally:
        _STATE.reset(token)


def has_active_state() -> bool:
    return active_state() is not None


def _read_seed(filename: str, key: str) -> list[dict]:
    try:
        value = json.loads((_ROOT / "data" / filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoStateError(f"The {filename} seed data is unavailable or invalid.") from exc
    records = value.get(key) if isinstance(value, dict) else None
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise DemoStateError(f"The {filename} seed data has an invalid structure.")
    return records


def initial_state() -> dict:
    """Build a fresh per-browser overlay from current immutable seed data."""
    tickets = _read_seed("tickets.json", "tickets")
    resolutions = _read_seed("resolution_records.json", "verified_resolutions")
    articles = _read_seed("knowledge_articles.json", "articles")
    return {
        "version": 1,
        "revision": 0,
        "ticket_statuses": {ticket["incident_number"]: ticket.get("status") for ticket in tickets
                            if isinstance(ticket.get("incident_number"), str)},
        "verified_resolutions": deepcopy(resolutions),
        "articles": deepcopy(articles),
    }


def _reference_data() -> tuple[dict[str, dict], dict[str, dict[str, dict]]]:
    tickets = {row["incident_number"]: row for row in _read_seed("tickets.json", "tickets")
               if isinstance(row.get("incident_number"), str)}
    try:
        catalogue_rows = _read_seed("roles.json", "roles")
    except DemoStateError:
        catalogue_rows = []
    roles: dict[str, dict[str, dict]] = {}
    for system in catalogue_rows:
        source = system.get("source_system")
        if not isinstance(source, str):
            continue
        by_id: dict[str, dict] = {}
        for group in ("gateway_roles", "backend_roles"):
            entries = system.get(group, [])
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and isinstance(entry.get("role_id"), str):
                        by_id[entry["role_id"]] = entry
        roles[source] = by_id
    return tickets, roles


def _nonempty_text(value: Any, max_length: int = 10_000) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= max_length


def validate_state(value: Any) -> dict:
    """Validate state shape and its references to trusted seed records."""
    if not isinstance(value, dict) or set(value) != {
        "version", "revision", "ticket_statuses", "verified_resolutions", "articles"
    }:
        raise DemoStateError("Demo state has an unsupported structure.")
    if value.get("version") != 1 or type(value.get("revision")) is not int or value["revision"] < 0:
        raise DemoStateError("Demo state version or revision is invalid.")
    tickets, role_catalogue = _reference_data()
    statuses = value.get("ticket_statuses")
    if (not isinstance(statuses, dict) or set(statuses) != set(tickets)
            or any(type(status) is not int or status not in _STATUS_CODES for status in statuses.values())):
        raise DemoStateError("Demo ticket status state is invalid.")

    resolutions = value.get("verified_resolutions")
    if not isinstance(resolutions, list) or len(resolutions) > 500:
        raise DemoStateError("Demo resolution state is invalid.")
    resolution_ids: set[str] = set()
    for row in resolutions:
        if not isinstance(row, dict) or set(row) != _RESOLUTION_KEYS:
            raise DemoStateError("A resolution record has an unsupported structure.")
        if (not _nonempty_text(row.get("resolution_id"), 100)
                or row["resolution_id"] in resolution_ids
                or row.get("incident_number") not in tickets
                or row.get("verified") is not True
                or not _nonempty_text(row.get("confirmed_root_cause"))
                or not isinstance(row.get("actions_performed"), list)
                or not row["actions_performed"]
                or any(not _nonempty_text(item) for item in row["actions_performed"])
                or not _nonempty_text(row.get("verification_evidence"))
                or not _nonempty_text(row.get("consultant_id"), 200)
                or not _nonempty_text(row.get("confirmation_timestamp"), 100)
                or row.get("verification_source") != "explicit_consultant_confirmation"):
            raise DemoStateError("A resolution record is incomplete or not consultant-verified.")
        resolution_ids.add(row["resolution_id"])

    articles = value.get("articles")
    if not isinstance(articles, list) or len(articles) > 500:
        raise DemoStateError("Demo knowledge state is invalid.")
    article_ids: set[str] = set()
    resolutions_by_id = {row["resolution_id"]: row for row in resolutions}
    for row in articles:
        if not isinstance(row, dict) or (not _ARTICLE_KEYS.issubset(row) or set(row) - (_ARTICLE_KEYS | METADATA_KEYS) or not validate_metadata(row)):
            raise DemoStateError("A knowledge article has an unsupported structure.")
        article_id = row.get("article_id")
        resolution = resolutions_by_id.get(row.get("source_resolution_id"))
        incident = row.get("source_incident")
        if (not _nonempty_text(article_id, 100) or article_id in article_ids
                or incident not in tickets or not resolution
                or resolution.get("incident_number") != incident
                or row.get("source_system") != (tickets[incident].get("source_system") or tickets[incident].get("system"))
                or row.get("issue_category") != (tickets[incident].get("category") or tickets[incident].get("issue_type"))
                or row.get("review_status") not in {"Draft", "Approved"}
                or row.get("knowledge_source") != "consultant_verified_resolution"
                or not all(_nonempty_text(row.get(field)) for field in ("title", "symptoms", "confirmed_root_cause", "verification_method", "created_at", "created_by"))
                or not isinstance(row.get("verified_troubleshooting_steps"), list)
                or not row["verified_troubleshooting_steps"]
                or any(not _nonempty_text(item) for item in row["verified_troubleshooting_steps"])
                or not isinstance(row.get("required_roles"), list)):
            raise DemoStateError("A knowledge article has invalid provenance or fields.")
        if row.get("review_status") == "Approved" and not all(
            _nonempty_text(row.get(field), 200) for field in ("approved_by", "approved_at", "published_at")
        ):
            raise DemoStateError("An approved knowledge article is missing reviewer metadata.")
        reference = tickets[incident]
        source = reference.get("source_system") or reference.get("system")
        valid_roles = role_catalogue.get(source, {})
        for role in row["required_roles"]:
            if (not isinstance(role, dict) or role.get("role_id") not in valid_roles
                    or not all(isinstance(role.get(field), str) for field in ("role_id", "role_name", "description"))):
                raise DemoStateError("A knowledge article contains a role outside its source-system catalogue.")
        article_ids.add(article_id)

    # Copy to detach the request context from caller-owned mutable objects.
    return deepcopy(value)


def _secret() -> bytes:
    secret = os.getenv("DEMO_STATE_SECRET", "")
    if len(secret) < 32:
        raise DemoStateError("Browser demo state is unavailable: DEMO_STATE_SECRET must be configured with at least 32 characters.")
    return secret.encode("utf-8")


def encode_state(value: dict) -> str:
    state = validate_state(value)
    compressed = zlib.compress(json.dumps(state, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), 9)
    payload = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
    signed = f"v1.{payload}"
    signature = hmac.new(_secret(), signed.encode("ascii"), hashlib.sha256).hexdigest()
    token = f"{signed}.{signature}"
    if len(token) > _MAX_TOKEN_CHARS:
        raise DemoStateError("Browser demo state exceeded the supported size.")
    return token


def decode_state(token: Any) -> dict:
    if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_CHARS:
        raise DemoStateError("Browser demo state is missing or too large.")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1" or not re.fullmatch(r"[0-9a-f]{64}", parts[2]):
        raise DemoStateError("Browser demo state token is malformed.")
    signed = ".".join(parts[:2])
    expected = hmac.new(_secret(), signed.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts[2]):
        raise DemoStateError("Browser demo state signature is invalid.")
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        if len(raw) > _MAX_TOKEN_CHARS:
            raise DemoStateError("Browser demo state is too large.")
        inflater = zlib.decompressobj()
        decoded = inflater.decompress(raw, _MAX_TOKEN_CHARS + 1)
        if (len(decoded) > _MAX_TOKEN_CHARS or inflater.unconsumed_tail
                or inflater.unused_data or not inflater.eof):
            raise DemoStateError("Browser demo state is too large.")
        result = json.loads(decoded.decode("utf-8"))
    except (ValueError, zlib.error, json.JSONDecodeError) as exc:
        raise DemoStateError("Browser demo state payload is malformed.") from exc
    return validate_state(result)


def save_statuses(tickets_data: dict) -> None:
    state = active_state()
    if state is None:
        return
    tickets = tickets_data.get("tickets") if isinstance(tickets_data, dict) else None
    if not isinstance(tickets, list):
        raise DemoStateError("Updated ticket state is invalid.")
    statuses = {}
    for ticket in tickets:
        if not isinstance(ticket, dict) or ticket.get("incident_number") not in state["ticket_statuses"]:
            raise DemoStateError("Updated ticket state contains an unknown incident.")
        status = ticket.get("status")
        if type(status) is not int or status not in _STATUS_CODES:
            raise DemoStateError("Updated ticket status is invalid.")
        statuses[ticket["incident_number"]] = status
    if set(statuses) != set(state["ticket_statuses"]):
        raise DemoStateError("Updated ticket state is incomplete.")
    state["ticket_statuses"] = statuses
    state["revision"] += 1


def save_resolutions(data: dict) -> None:
    state = active_state()
    if state is None:
        return
    validated = validate_state({**state, "verified_resolutions": data.get("verified_resolutions") if isinstance(data, dict) else None})
    state.update(validated)
    state["revision"] += 1


def save_articles(data: dict) -> None:
    state = active_state()
    if state is None:
        return
    validated = validate_state({**state, "articles": data.get("articles") if isinstance(data, dict) else None})
    state.update(validated)
    state["revision"] += 1
