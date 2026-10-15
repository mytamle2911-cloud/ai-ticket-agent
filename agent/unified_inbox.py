"""Read-only source-system, capability, and operational evidence for demo tickets."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any

from agent.scenario_knowledge import match_scenarios


def _read_store(filename: str, root_key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, "data", filename), encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}, []
    records = data.get(root_key) if isinstance(data, dict) else None
    if not isinstance(records, list):
        return data if isinstance(data, dict) else {}, []
    return data, [row for row in records if isinstance(row, dict)]


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _effective_incident_status(record: dict[str, Any], simulation_timestamp: Any) -> tuple[str, str]:
    """Use status timestamps together; a stale Ongoing value is never called current."""
    stored = record.get("incident_status_as_recorded")
    sim = _parse_time(simulation_timestamp)
    start = _parse_time(record.get("scheduled_start_at"))
    recovery = _parse_time(record.get("expected_recovery_at"))
    actual = _parse_time(record.get("actual_resolution_at"))
    as_of = _parse_time(record.get("status_as_of"))

    if stored == "Resolved":
        if actual is not None:
            return "Resolved", "The record includes an actual resolution timestamp."
        return "Resolved", "The record is marked Resolved, but an actual recovery timestamp is not recorded."
    if stored == "Cancelled":
        return "Cancelled", "The record is marked cancelled."
    if not sim:
        return "status_unconfirmed", "The recorded reference time is missing; current operational state cannot be evaluated."
    if stored == "Scheduled":
        if start and sim >= start:
            return "status_unconfirmed", "The scheduled start has passed but no current status or actual resolution is recorded."
        return "Scheduled", "The recorded maintenance window has not started at the reference time."
    if stored == "Ongoing":
        if start and sim < start:
            return "not_yet_started", "The recorded start is later than the reference time; do not report an active incident."
        if recovery and sim > recovery and not actual:
            return "status_unconfirmed", "The recorded expected recovery time has passed without an actual resolution timestamp; verify current status before advising a retry."
        if as_of and sim - as_of > timedelta(hours=1):
            return "status_unconfirmed", "The status record is older than the reference time; verify current status before advising a retry."
        return "Ongoing", "The incident start has passed and its expected recovery time has not passed at the reference time."
    return "status_unconfirmed", "The incident status is not supported by the operational record."


def collect_ticket_support_evidence(ticket: dict[str, Any]) -> dict[str, Any]:
    """Retrieve matching scenario, source catalogue and linked downtime without mutation."""
    source = ticket.get("source_system") or ticket.get("system") or ""
    scenarios = match_scenarios(ticket)
    scenario = scenarios[0] if len(scenarios) == 1 else None
    system_data, systems = _read_store("systems.json", "systems")
    system = next((row for row in systems if row.get("source_system") == source), None)
    roles_data, roles = _read_store("roles.json", "roles")
    role_catalogue = next((row for row in roles if row.get("source_system") == source), None)

    operational = None
    operational_state = None
    operational_guidance = None
    op_ref = ticket.get("operational_incident_id")
    if op_ref:
        op_data, incidents = _read_store("operational_incidents.json", "operational_incidents")
        operational = next((row for row in incidents
                            if row.get("operational_incident_id") == op_ref
                            and row.get("source_system") == source), None)
        if operational:
            operational_state, operational_guidance = _effective_incident_status(
                operational, op_data.get("simulation_timestamp")
            )
            operational = {
                **operational,
                "effective_status_at_simulation": operational_state,
                "status_interpretation": operational_guidance,
                "simulation_timestamp": op_data.get("simulation_timestamp"),
            }

    workflow = None
    if system and ticket.get("workflow_id"):
        workflow = next((row for row in system.get("workflow_capabilities", [])
                         if isinstance(row, dict) and row.get("workflow_id") == ticket.get("workflow_id")), None)

    return {
        "scenario": scenario,
        "scenario_matches": scenarios,
        "source_system_record": system,
        "source_system_catalogue_found": system is not None,
        "role_catalogue": role_catalogue,
        "role_catalogue_found": role_catalogue is not None,
        "workflow_capability": workflow,
        "supported_task_content_languages": system.get("supported_task_content_languages", []) if system else [],
        "operational_incident": operational,
        "operational_incident_state": operational_state,
        "operational_incident_guidance": operational_guidance,
        "catalogue_notice": system_data.get("synthetic_data_notice"),
        "role_catalogue_notice": roles_data.get("synthetic_data_notice"),
    }


def investigation_guidance(
    ticket: dict[str, Any], role_check: dict[str, Any],
    verified_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build deterministic recommendations from matched synthetic scenario evidence."""
    evidence = collect_ticket_support_evidence(ticket)
    scenario = evidence.get("scenario")
    if not isinstance(scenario, dict):
        return {"unified_inbox_evidence": evidence, "scenario_matches": [], "recommended_troubleshooting_steps": []}

    issue = scenario.get("issue_type")
    steps = scenario.get("recommended_actions", [])
    steps = [step for step in steps if isinstance(step, dict)][:3]
    gaps: list[str] = []
    if issue == "Task Not Visible" and not verified_resolution:
        gaps.append("The user's current Gateway and Backend role assignments have not been independently verified.")
    elif issue == "Loading Time Too Slow":
        incident = evidence.get("operational_incident")
        if not incident:
            gaps.append("No matching operational incident record was retrieved for the source system.")
        elif evidence.get("operational_incident_state") == "status_unconfirmed":
            gaps.append("The operational status is stale or unconfirmed; obtain a current status from the incident owner before advising a retry.")
        if incident and incident.get("incident_status_as_recorded") == "Scheduled":
            gaps.append("The recorded window is scheduled; it does not establish that an outage is currently affecting the ticket.")
    elif issue == "Cannot See Specific Language":
        gaps.append("Confirm that the reported language concerns source-provided task content rather than the Unified Inbox interface language.")
    elif issue == "Cannot Set Up Substitution" and not evidence.get("workflow_capability"):
        gaps.append("The exact workflow capability entry could not be matched; request confirmation from source-system support.")

    if issue == "Task Not Visible":
        reference = scenario.get("required_ticket_reference")
        if isinstance(reference, str) and reference.strip():
            verification = f"After any separately authorized access change, confirm that task {reference} is visible. No assignment change is performed by this agent."
        else:
            verification = "After any separately authorized access change, ask the reporter to reopen the same task and confirm it is visible. No assignment change is performed by this agent."
        next_steps = [step.get("instruction", "") for step in steps]
    elif issue == "Loading Time Too Slow":
        verification = "After a confirmed recovery, retry the same Unified Inbox request and record the result with timestamp and timezone."
        next_steps = [step.get("instruction", "") for step in steps]
    elif issue == "Cannot Set Up Substitution":
        verification = "Confirm that source-system support has provided an approved alternative for the named workflow; this prototype cannot enable substitution."
        next_steps = [step.get("instruction", "") for step in steps]
    else:
        verification = "Confirm which language is rendered in the task content after selecting a language listed for this source system; the UI language does not translate task content."
        next_steps = [step.get("instruction", "") for step in steps]

    scenario_matches = [{"scenario_id": scenario.get("scenario_id"), "source_status": scenario.get("source_status")}]
    result = {
        "unified_inbox_evidence": evidence,
        "scenario_matches": scenario_matches,
        "scenario_evidence": scenario,
        "root_cause_assessment": ({
            "state": "consultant_verified",
            "statement": verified_resolution.get("confirmed_root_cause", ""),
            "source": "verified_resolution",
            "source_id": verified_resolution.get("resolution_id"),
            "verified": True,
        } if verified_resolution else {
            "state": "scenario_expected_unverified",
            "statement": scenario.get("root_cause", {}).get("statement", "Scenario expectation is unavailable."),
            "source": "synthetic_scenario",
            "source_id": scenario.get("scenario_id"),
            "verified": False,
        }),
        "recommended_troubleshooting_steps": steps,
        "hypotheses": [] if verified_resolution else [{
            "text": scenario.get("root_cause", {}).get("statement", "Scenario expectation is unavailable."),
            "status": "scenario_expected_unverified", "source": "synthetic_scenario",
            "source_id": scenario.get("scenario_id"),
        }],
        "diagnostic_steps": steps,
        "information_gaps": gaps,
        "recommended_next_steps": next_steps,
        "verification_steps": [{
            "document_id": scenario.get("scenario_id"),
            "step_id": f"{scenario.get('scenario_id')}-VERIFY",
            "instruction": verification,
            "expected_observation": "The reporter confirms the expected task behavior or reports the exact remaining symptom.",
            "handling_when_unclear": "Record the exact observation, timestamp, and source system; keep the cause and outcome unverified.",
        }],
        "resolution_path": "HUMAN_SUPPORT",
        "decision_reason": "Scenario-guided recommendations are available, but no reviewed resolution procedure or verified user outcome makes this ticket KB_RESOLVABLE.",
        "proposed_resolution_steps": [],
        "expected_outcomes": [],
        "resolution_outcome_status": "not_applicable",
        "approval_needed": False,
        "approval_required": False,
        "proposed_actions": [],
        "recommendation": {
            "action": "human_investigation",
            "text": "Use the scenario-grounded next steps; the expected cause remains unverified and no system or access changes were made.",
        },
    }

    # Role assignments are never loaded from the ticket or inferred from catalogue presence.
    result["evidence_extension"] = {
        "configured_gateway_roles": role_check.get("configured_gateway_roles", []),
        "configured_backend_roles": role_check.get("configured_backend_roles", []),
        "user_assignments_verified": False,
        "role_lookup_source": "source_system_role_catalogue",
    }
    return result
