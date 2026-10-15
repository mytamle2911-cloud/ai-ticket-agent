"""Read-only retrieval of local historical case records."""

from __future__ import annotations

import json
import os
import re
from typing import Any


def _normalise(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _history_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "historical_cases.json")


def search_historical_cases(source_system: str, issue_type: str) -> dict[str, Any]:
    """Return exact system/issue matches without treating history as a diagnosis."""
    if not isinstance(source_system, str) or not source_system.strip():
        raise ValueError("source_system must be a non-empty string")
    if not isinstance(issue_type, str) or not issue_type.strip():
        raise ValueError("issue_type must be a non-empty string")

    try:
        with open(_history_path(), encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("Historical case data is unavailable or invalid.") from None

    cases = data.get("historical_resolved_ticket_cases") if isinstance(data, dict) else None
    if not isinstance(cases, list):
        raise RuntimeError("Historical case data has an invalid structure.")

    system_key = _normalise(source_system)
    issue_key = _normalise(issue_type)
    matches = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        if _normalise(str(case.get("source_system", ""))) != system_key:
            continue
        if _normalise(str(case.get("issue", ""))) != issue_key:
            continue
        outcome = case.get("resolution_outcome", {})
        cause = case.get("root_cause", {})
        matches.append({
            "case_id": case.get("case_id"),
            "incident_number": case.get("incident_number"),
            "source_system": case.get("source_system"),
            "issue": case.get("issue"),
            "match_type": "exact_system_and_issue",
            "case_state": case.get("case_state"),
            "resolution_state": outcome.get("state") if isinstance(outcome, dict) else None,
            "root_cause_state": cause.get("state") if isinstance(cause, dict) else None,
            "record": case,
        })

    return {
        "search_status": "matched" if matches else "no_match",
        "matches": matches,
        "limitations": [
            "Historical records provide comparison context only; they do not establish current product behavior or this incident's root cause.",
            "A recorded verification outcome applies only to its source incident.",
        ],
    }
