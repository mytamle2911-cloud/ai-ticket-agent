"""Small deterministic parser for the status commands supported by the UI."""

from __future__ import annotations

import re


SUPPORTED_REQUEST_STATUSES = ("New", "In Progress", "On Hold", "Resolved", "Closed")
_COMMAND = re.compile(r"\b(change|set|update|mark|move)\b", re.IGNORECASE)
_INCIDENT = re.compile(r"\bINC\d{6}\b", re.IGNORECASE)
_TARGET_SEPARATOR = re.compile(r"\b(?:to|as)\b", re.IGNORECASE)


def parse_status_intent(message: str) -> dict | None:
    """Return a status command, including clarification state, or None for normal chat."""
    if not isinstance(message, str) or not message.strip():
        return None
    command = _COMMAND.search(message)
    if not command:
        return None
    tail = message[command.end():]
    has_target_reference = bool(
        re.search(r"\b(status|ticket|incident)\b", tail, re.IGNORECASE)
        or _INCIDENT.search(tail)
    )
    separator = _TARGET_SEPARATOR.search(tail)
    if not has_target_reference or (not separator and not re.search(r"\bstatus\b", tail, re.IGNORECASE)):
        return None

    requested_text = tail[separator.end():] if separator else ""
    requested_text = re.split(r"[.!?\n]", requested_text, maxsplit=1)[0].strip()
    matches = []
    for status in SUPPORTED_REQUEST_STATUSES:
        if re.search(rf"(?<![A-Za-z]){re.escape(status)}(?![A-Za-z])", requested_text, re.IGNORECASE):
            matches.append(status)
    incident = _INCIDENT.search(message)
    result = {
        "incident_number": incident.group(0).upper() if incident else None,
        "target_status": matches[0] if len(matches) == 1 else None,
        "requested_text": requested_text,
    }
    if len(matches) > 1:
        result["clarification"] = "I found more than one possible target status. Please specify one status: New, In Progress, On Hold, or Resolved."
    elif not requested_text:
        result["clarification"] = "Which status should I set? Choose New, In Progress, On Hold, or Resolved."
    elif not matches:
        result["clarification"] = f"'{requested_text}' is not supported. Choose New, In Progress, On Hold, or Resolved."
    return result
