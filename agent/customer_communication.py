"""Synthetic customer lookup, contact-time suggestions, and email drafts."""

from __future__ import annotations

import json
import os
import re
import requests
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent import demo_state, internal_api, llm_agent


class CustomerCommunicationError(ValueError):
    """A safe, user-facing customer communication error."""


def _data_path(name: str) -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", name)


def _load_records(name: str, key: str) -> list[dict]:
    try:
        with open(_data_path(name), encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        raise CustomerCommunicationError(f"Customer communication data file '{name}' is unavailable or invalid.") from None
    rows = data.get(key) if isinstance(data, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise CustomerCommunicationError(f"Customer communication data file '{name}' has an invalid structure.")
    return rows


def get_customer_for_ticket(ticket: dict) -> dict:
    customer_id = ticket.get("customer_id") if isinstance(ticket, dict) else None
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise CustomerCommunicationError("This ticket does not have a customer reference.")
    customers = _load_records("customers.json", "customers")
    customer = next((row for row in customers if row.get("customer_id") == customer_id), None)
    if not customer:
        raise CustomerCommunicationError("Customer information is unavailable for this ticket.")
    required = ("full_name", "email", "location", "timezone", "working_hours_start", "working_hours_end", "preferred_language")
    if any(not isinstance(customer.get(field), str) or not customer[field].strip() for field in required):
        raise CustomerCommunicationError("Customer information is incomplete. Check the local customer record.")
    return customer


def get_consultant_config() -> dict:
    rows = _load_records("customer_communication_config.json", "consultant")
    if len(rows) != 1:
        raise CustomerCommunicationError("The local consultant communication configuration is invalid.")
    consultant = rows[0]
    required = ("location", "timezone", "working_hours_start", "working_hours_end")
    if any(not isinstance(consultant.get(field), str) or not consultant[field].strip() for field in required):
        raise CustomerCommunicationError("The local consultant communication configuration is incomplete.")
    return consultant


def _working_time(value: str, field: str) -> time:
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        raise CustomerCommunicationError(f"Invalid configured working time for {field}.") from None


def _within_hours(value: datetime, start: time, end: time) -> bool:
    wall_time = value.timetz().replace(tzinfo=None)
    return start <= wall_time < end


def suggest_contact_time(
    customer: dict,
    consultant: dict | None = None,
    *,
    now: datetime | None = None,
    selected_date: date | str | None = None,
    duration_minutes: int = 30,
) -> dict:
    """Find the first future half-hour slot aligned to consultant local time.

    Iterating real UTC instants and converting them to each local zone handles
    daylight-saving gaps and repeated hours without inventing a wall-clock slot.
    """
    consultant = consultant or get_consultant_config()
    if not isinstance(duration_minutes, int) or duration_minutes != 30:
        raise CustomerCommunicationError("Contact suggestions must use a 30-minute slot.")
    try:
        customer_zone = ZoneInfo(customer["timezone"])
        consultant_zone = ZoneInfo(consultant["timezone"])
    except (KeyError, ZoneInfoNotFoundError):
        raise CustomerCommunicationError("A configured customer or consultant timezone is invalid.") from None
    customer_start = _working_time(customer["working_hours_start"], "customer")
    customer_end = _working_time(customer["working_hours_end"], "customer")
    consultant_start = _working_time(consultant["working_hours_start"], "consultant")
    consultant_end = _working_time(consultant["working_hours_end"], "consultant")
    if customer_start >= customer_end or consultant_start >= consultant_end:
        raise CustomerCommunicationError("Configured working hours must end after they start.")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise CustomerCommunicationError("The contact-time clock must include a timezone.")
    current_utc = current.astimezone(timezone.utc)
    if isinstance(selected_date, str):
        try:
            selected_date = date.fromisoformat(selected_date)
        except ValueError:
            raise CustomerCommunicationError("Use an ISO date (YYYY-MM-DD) for the contact date.") from None
    if selected_date is None:
        selected_date = current_utc.astimezone(consultant_zone).date()
    if not isinstance(selected_date, date):
        raise CustomerCommunicationError("The selected contact date is invalid.")

    day_start = datetime.combine(selected_date, time.min, consultant_zone).astimezone(timezone.utc)
    day_end = datetime.combine(selected_date + timedelta(days=1), time.min, consultant_zone).astimezone(timezone.utc)
    duration = timedelta(minutes=duration_minutes)
    has_overlap = False
    candidate = None
    instant = day_start
    while instant < day_end:
        customer_local = instant.astimezone(customer_zone)
        consultant_local = instant.astimezone(consultant_zone)
        overlap = _within_hours(customer_local, customer_start, customer_end) and _within_hours(consultant_local, consultant_start, consultant_end)
        has_overlap = has_overlap or overlap
        aligned = consultant_local.minute in (0, 30) and consultant_local.second == 0
        if overlap and aligned and instant >= current_utc and instant + duration <= day_end:
            valid_slot = True
            for minute in range(duration_minutes):
                check = instant + timedelta(minutes=minute)
                if not (
                    _within_hours(check.astimezone(customer_zone), customer_start, customer_end)
                    and _within_hours(check.astimezone(consultant_zone), consultant_start, consultant_end)
                ):
                    valid_slot = False
                    break
            if valid_slot:
                candidate = instant
                break
        instant += timedelta(minutes=1)

    customer_country = customer.get("location", "Customer local time").rsplit(",", 1)[-1].strip()
    consultant_country = consultant.get("location", "Consultant local time").rsplit(",", 1)[-1].strip()
    slot = None
    if candidate:
        end = candidate + duration
        customer_local = candidate.astimezone(customer_zone)
        consultant_local = candidate.astimezone(consultant_zone)
        slot = {
            "start_utc": candidate.isoformat().replace("+00:00", "Z"),
            "end_utc": end.isoformat().replace("+00:00", "Z"),
            "duration_minutes": duration_minutes,
            "customer": {
                "time": customer_local.strftime("%H:%M"),
                "date": customer_local.date().isoformat(),
                "timezone": customer["timezone"],
                "timezone_abbreviation": customer_local.tzname(),
                "label": customer_country,
            },
            "consultant": {
                "time": consultant_local.strftime("%H:%M"),
                "date": consultant_local.date().isoformat(),
                "timezone": consultant["timezone"],
                "timezone_abbreviation": consultant_local.tzname(),
                "label": consultant_country,
            },
            "is_confirmed_booking": False,
        }
    if not has_overlap:
        reason = "No common working-hours overlap was found on the selected date."
    elif not candidate:
        reason = "No future 30-minute slot remains in the working-hours overlap on the selected date."
    else:
        reason = None
    return {
        "selected_date": selected_date.isoformat(),
        "overlap_found": has_overlap,
        "available": candidate is not None,
        "duration_minutes": duration_minutes,
        "suggested_slot": slot,
        "reason": reason,
        "is_confirmed_booking": False,
    }


_ROLE_ID = re.compile(r"\bXX:(?:GATEWAY|BACKEND)_[A-Z0-9_]+:XX\b|\b(?:GATEWAY|BACKEND)_[A-Z0-9_]+\b|\bSYNTH_[A-Z0-9_]*(?:GATEWAY|BACKEND)[A-Z0-9_]*\b", re.IGNORECASE)
_OPERATIONAL_ID = re.compile(r"\b(?:SYNTH[-_])?(?:OPS|OPERATIONAL)[-_]?[A-Z0-9-]+\b", re.IGNORECASE)
_ACCESS_WORDS = re.compile(r"\b(access|authorization|authorisation|permission|role|visibility|visible)\b", re.IGNORECASE)


def _customer_safe_text(value: object, limit: int = 1600) -> str:
    if not isinstance(value, str):
        return ""
    clean = _ROLE_ID.sub("[internal role reference omitted]", value)
    clean = _OPERATIONAL_ID.sub("[internal incident reference omitted]", clean)
    clean = re.sub(r"\s*[—-]\s*synthetic scenario\s+\d+\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bscenario-defined expected cause\b", "scenario-based expected cause", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bin this synthetic scenario\b", "in this scenario", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bsynthetic catalogue\b", "source-system reference", clean, flags=re.IGNORECASE)
    return clean[:limit]


def _has_verified_authorization_assignment(record: dict | None) -> bool:
    evidence = record.get("authorization_assignment_verification") if isinstance(record, dict) else None
    return isinstance(evidence, dict) and evidence.get("verified") is True and isinstance(evidence.get("evidence_summary"), str) and bool(evidence["evidence_summary"].strip())


def _is_access_related(issue: str) -> bool:
    return bool(_ACCESS_WORDS.search(issue))


def _remove_unverified_authorization_claims(text: str) -> str:
    """Replace categorical missing-access sentences when assignments were not verified."""
    patterns = (
        r"[^.!?\\n]*(?:we|our review)[^.!?\\n]*(?:verified|confirmed|found|determined)[^.!?\\n]*(?:authorization|authori[sz]ation|permission|role)[^.!?\\n]*(?:missing|lacking|absent|not assigned)[^.!?\\n]*[.!?]?",
        r"[^.!?\\n]*(?:missing|lacking|absent|unassigned|not assigned)[^.!?\\n]*(?:authorization|authori[sz]ation|permission|role)[^.!?\\n]*[.!?]?",
        r"[^.!?\\n]*(?:authorization|authori[sz]ation|permission|role)[^.!?\\n]*(?:is|are|was|were)?\\s*(?:missing|lacking|absent|not assigned)[^.!?\\n]*[.!?]?",
    )
    safe = "One possible cause is an authorization issue; the relevant access permissions still need to be verified."
    for pattern in patterns:
        text = re.sub(pattern, safe, text, flags=re.IGNORECASE)
    return text


def _email_prompt_context(ticket: dict, customer: dict, contact_time: dict, investigation: dict, verified_resolution: dict | None) -> dict:
    issue = ticket.get("issue_type") or ticket.get("issue") or ticket.get("category") or "Support issue"
    source_system = ticket.get("source_system") or ticket.get("system") or ""
    gaps = investigation.get("information_gaps", []) if isinstance(investigation, dict) else []
    safe_gaps = [_customer_safe_text(item, 300) for item in gaps[:8] if isinstance(item, str)] if isinstance(gaps, list) else []
    slot = contact_time.get("suggested_slot") if contact_time.get("available") else None
    verified = verified_resolution if isinstance(verified_resolution, dict) and verified_resolution.get("verified") is True else None
    assessment = investigation.get("root_cause_assessment", {}) if isinstance(investigation, dict) else {}
    if not isinstance(assessment, dict):
        assessment = {}
    access_related = _is_access_related(str(issue))
    authorization_assignment_verified = _has_verified_authorization_assignment(verified)
    confirmed_resolution = verified if verified and (not access_related or authorization_assignment_verified) else None
    expected_cause = None
    hypotheses = []
    if access_related and not authorization_assignment_verified:
        hypotheses.append({
            "kind": "unverified_access_hypothesis",
            "status": "unverified",
            "text": "An authorization issue is one possible cause; the user's current access permissions have not been verified.",
        })
    elif assessment.get("verified") is not True:
        statement = assessment.get("statement")
        if isinstance(statement, str) and statement.strip():
            entry = {"status": assessment.get("state", "unverified"), "text": _customer_safe_text(statement, 400)}
            if assessment.get("source") == "synthetic_scenario":
                expected_cause = {**entry, "kind": "scenario_expected_unverified"}
            else:
                hypotheses.append({**entry, "kind": "investigation_hypothesis"})
    for item in investigation.get("hypotheses", []) if isinstance(investigation, dict) and isinstance(investigation.get("hypotheses"), list) and not (access_related and not authorization_assignment_verified) else []:
        if isinstance(item, dict) and isinstance(item.get("text"), str) and item.get("verified") is not True:
            hypotheses.append({"kind": "investigation_hypothesis", "status": item.get("status", "unverified"), "text": _customer_safe_text(item["text"], 400)})
    return {
        "incident_number": ticket.get("incident_number"),
        "source_system": _customer_safe_text(source_system, 100),
        "issue_type": _customer_safe_text(issue, 160),
        "customer_name": customer.get("full_name"),
        "customer_location": customer.get("location"),
        "preferred_language": customer.get("preferred_language"),
        "customer_report": _customer_safe_text(ticket.get("additional_comments") or ticket.get("description") or ticket.get("short_description") or ticket.get("summary")),
        "customer_error_message": _customer_safe_text(ticket.get("error_message") or ticket.get("test_detail") or ""),
        "investigation_resolution_path": investigation.get("resolution_path") if isinstance(investigation, dict) else None,
        "customer_safe_information_gaps": safe_gaps,
        "scenario_expected_root_cause_unverified": expected_cause,
        "investigation_hypotheses_unverified": hypotheses[:5],
        "consultant_confirmed_resolution_available": bool(confirmed_resolution),
        "consultant_confirmed_root_cause": _customer_safe_text(confirmed_resolution.get("confirmed_root_cause", ""), 400) if confirmed_resolution else None,
        "consultant_confirmed_actions": [_customer_safe_text(item, 300) for item in confirmed_resolution.get("actions_performed", [])[:5] if isinstance(item, str)] if confirmed_resolution and isinstance(confirmed_resolution.get("actions_performed"), list) else [],
        "consultant_confirmed_verification_evidence": _customer_safe_text(confirmed_resolution.get("verification_evidence", ""), 500) if confirmed_resolution else None,
        "authorization_assignment_verified": authorization_assignment_verified if access_related else None,
        "verified_resolution_available": bool(confirmed_resolution),
        "verified_resolution_summary": _customer_safe_text(confirmed_resolution.get("verification_evidence", ""), 500) if confirmed_resolution else None,
        "suggested_contact_slot": slot,
    }


def _verified_resolution_for_ticket(incident_number: str) -> dict | None:
    try:
        if demo_state.has_active_state():
            response = internal_api.get(f"/api/tickets/{incident_number}/resolutions", timeout=10)
            response.raise_for_status()
            rows = response.json().get("resolutions", [])
        else:
            rows = _load_records("resolution_records.json", "verified_resolutions")
    except (RuntimeError, ValueError, AttributeError, requests.RequestException):
        return None
    return next((row for row in rows if row.get("incident_number") == incident_number and row.get("verified") is True), None)


def generate_customer_email_draft(
    ticket: dict,
    customer: dict,
    contact_time: dict,
    *,
    investigation: dict | None = None,
    client=None,
) -> dict:
    """Ask GPT Luna for an editable customer email grounded in safe context."""
    investigation = investigation if isinstance(investigation, dict) else {}
    if client is None:
        try:
            client = llm_agent._get_client()
        except RuntimeError as exc:
            raise CustomerCommunicationError(str(exc)) from None
    try:
        context = _email_prompt_context(
            ticket, customer, contact_time, investigation,
            _verified_resolution_for_ticket(ticket["incident_number"]),
        )
        if len(json.dumps(context, ensure_ascii=False).encode("utf-8")) > 48 * 1024:
            raise CustomerCommunicationError("The customer-email context is too large for a safe AI request.")
        response = client.responses.create(
            model=llm_agent.MODEL,
            instructions=(
                "Write a concise, professional customer follow-up email in the customer's preferred language. "
                "Return only a JSON object with string keys subject and body. Summarize the reported incident, "
                "ask only for relevant missing information supported by the supplied context, and propose a call "
                "only if a suggested slot is present. Include both local times and state that the suggestion is "
                "subject to the customer's availability; it is not a booking. Never expose internal role IDs, "
                "internal operational incident IDs, backend details, or internal-only information. The current "
                "customer ticket number may appear in the subject. Keep scenario-expected causes, investigation hypotheses, "
                "verified evidence, and consultant-confirmed resolution separate. Never present a scenario expectation or "
                "hypothesis as verified. For access or authorization issues, do not say a role or permission is missing, "
                "or that authorization was verified as missing, unless explicit authorization_assignment_verified evidence is true. "
                "Do not claim that the issue is resolved unless a consultant-verified resolution is supplied; even then, state only the verified evidence. Do not invent actions, findings, "
                "or requested information. Sign as L2 Support Team."
            ),
            input=json.dumps(context, ensure_ascii=False),
            max_output_tokens=llm_agent.MAX_OUTPUT_TOKENS,
        )
        if not llm_agent._response_complete(response):
            raise CustomerCommunicationError("The AI email response was incomplete or failed. Retry; no email was saved.")
        output = llm_agent._item_value(response, "output_text", "")
        if not isinstance(output, str) or not output.strip():
            raise CustomerCommunicationError("GPT Luna returned an empty email draft. Please retry.")
        text = output.strip()
        if text.startswith(chr(96) * 3) and text.endswith(chr(96) * 3):
            text = text[3:-3].strip()
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
        try:
            draft = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            try:
                draft = json.loads(match.group(0)) if match else None
            except json.JSONDecodeError:
                draft = None
        if not isinstance(draft, dict) or any(not isinstance(draft.get(key), str) or not draft[key].strip() for key in ("subject", "body")):
            raise CustomerCommunicationError("GPT Luna returned an invalid email draft. Please retry.")
        subject = _customer_safe_text(draft["subject"].strip(), 300)
        body = _customer_safe_text(draft["body"].strip())
        issue = str(ticket.get("issue_type") or ticket.get("issue") or ticket.get("category") or "")
        if _is_access_related(issue) and not _has_verified_authorization_assignment(_verified_resolution_for_ticket(ticket["incident_number"])):
            subject = _remove_unverified_authorization_claims(subject)
            body = _remove_unverified_authorization_claims(body)
        return {
            "subject": subject,
            "body": body,
            "generated_by": llm_agent.MODEL,
            "ai_provenance": "live_gpt",
            "draft_status": "editable_unverified",
            "contact_time": contact_time,
        }
    except CustomerCommunicationError:
        raise
    except Exception as exc:
        safe_error = llm_agent._safe_openai_error(exc)
        raise CustomerCommunicationError(f"Customer email draft generation failed. {safe_error}") from None
