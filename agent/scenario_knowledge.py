"""Retrieval of synthetic, scenario-defined Unified Inbox evidence."""

from __future__ import annotations

import json
import os
import re
from typing import Any


def _normalise(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _data_path(filename: str) -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", filename)


def load_scenarios() -> list[dict[str, Any]]:
    try:
        with open(_data_path("troubleshooting_scenarios.json"), encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return []
    scenarios = data.get("synthetic_scenarios") if isinstance(data, dict) else None
    return [item for item in scenarios if isinstance(item, dict)] if isinstance(scenarios, list) else []


def ticket_evidence_text(ticket: dict[str, Any]) -> str:
    """Join user-provided evidence and scenario references, never the incident ID."""
    keys = (
        "short_description", "summary", "additional_comments", "error_message",
        "test_detail", "test", "description", "scenario_reference",
        "workflow_id", "requested_language", "operational_incident_id",
    )
    return " ".join(
        str(ticket.get(key, "")) for key in keys
        if isinstance(ticket.get(key), str)
    )


def match_scenarios(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    """Match by source, issue, reported signature, and scenario context (not ticket ID)."""
    if not isinstance(ticket, dict):
        return []
    source = ticket.get("source_system") or ticket.get("system") or ""
    issue = ticket.get("issue_type") or ticket.get("issue") or ticket.get("category") or ""
    evidence = _normalise(ticket_evidence_text(ticket))
    if not evidence:
        return []

    matches = []
    for scenario in load_scenarios():
        if _normalise(str(scenario.get("source_system", ""))) != _normalise(str(source)):
            continue
        if _normalise(str(scenario.get("issue_type", ""))) != _normalise(str(issue)):
            continue
        signature = scenario.get("error_signature")
        reference = scenario.get("required_ticket_reference")
        if not isinstance(signature, str) or not isinstance(reference, str):
            continue
        signature_text = _normalise(signature)
        reference_text = _normalise(reference)
        if signature_text and signature_text in evidence and reference_text and reference_text in evidence:
            matches.append(scenario)
    return matches
