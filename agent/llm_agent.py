"""Evidence-led ticket investigation for the local support prototype."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests
from dotenv import load_dotenv
from openai import OpenAI
from agent.unified_inbox import investigation_guidance
from agent.scenario_knowledge import match_scenarios

load_dotenv()

# Mock ServiceNow API
BASE_URL = "http://127.0.0.1:5000"

# OpenAI configuration
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
MAX_ITERATIONS = 10

# Ticket validation
ALLOWED_STATUSES = {
    "New", "In Progress", "On Hold",
    "Resolved", "Closed", "Cancelled"
}
_INCIDENT_PATTERN = re.compile(r"^INC\d{6}$", re.IGNORECASE)

# Initialize only when GPT is requested
client: Any = None

class ToolInputError(ValueError):
    """Raised when a model supplied tool call does not match its schema."""


def get_data_path(filename: str) -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "data", filename)


def _validate_incident_number(incident_number: Any) -> str:
    if not isinstance(incident_number, str) or not _INCIDENT_PATTERN.fullmatch(incident_number.strip()):
        raise ToolInputError("Enter a valid incident number such as INC100001.")
    return incident_number.strip().upper()


def _safe_api_error(exc: Exception, operation: str) -> str:
    """Return useful error text without echoing response bodies or request data."""
    if isinstance(exc, requests.Timeout):
        return f"The ticket service timed out while {operation}. Check that it is running and retry."
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        if status == 404:
            return "Ticket not found. Check the incident number and try again."
        if status == 409:
            return "The ticket status changed after review. Refresh the ticket and request a new confirmation."
        if status is not None:
            return f"The ticket service returned HTTP {status} while {operation}. Check the service and retry."
    if isinstance(exc, requests.RequestException):
        return f"Could not reach the ticket service while {operation}. Start it with `python -m mock_api.server` and retry."
    return f"The ticket service returned an unexpected response while {operation}. Check the service logs and retry."


def get_ticket(incident_number: str) -> dict:
    incident_number = _validate_incident_number(incident_number)
    try:
        response = requests.get(f"{BASE_URL}/api/tickets/{incident_number}", timeout=10)
        response.raise_for_status()
        ticket = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(_safe_api_error(exc, "retrieving the ticket")) from None
    if not isinstance(ticket, dict) or ticket.get("incident_number") != incident_number:
        raise RuntimeError("The ticket service returned an invalid ticket record.")
    return ticket


def _load_json(filename: str, root_key: str) -> list:
    try:
        with open(get_data_path(filename), "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read {filename}. Check that the local data file is present and valid JSON.") from None
    values = data.get(root_key) if isinstance(data, dict) else None
    if not isinstance(values, list):
        raise RuntimeError(f"{filename} has an invalid data structure; expected a '{root_key}' list.")
    return values


def _text(value: Any, field: str, *, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if not isinstance(value, str):
        raise ToolInputError(f"'{field}' must be a string.")
    if not value.strip() and not optional:
        raise ToolInputError(f"'{field}' cannot be empty.")
    return value.strip()


def _normalise(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _ticket_search_fields(ticket: dict) -> dict[str, str]:
    """Normalize available ticket fields without replacing or inferring evidence."""
    aliases = {
        "ticket_id": "incident_number", "source_system": "source_system", "issue_type": "issue_type",
        "error": "error_message", "summary": "short_description", "test": "test",
        "description": "description",
    }
    output = {name: ticket.get(key, "") if isinstance(ticket.get(key, ""), str) else "" for name, key in aliases.items()}
    fallbacks = {"source_system": "system", "issue_type": "issue", "error": "test_detail", "summary": "summary", "description": "additional_comments"}
    for name, key in fallbacks.items():
        if not output[name] and isinstance(ticket.get(key), str):
            output[name] = ticket[key]
    if not output["error"] and isinstance(ticket.get("additional_comments"), str):
        output["error"] = ticket["additional_comments"]
    return output


def _structured_retrieval(source_system: str, issue_type: str = "", ticket_fields: dict | None = None) -> list[dict]:
    """Return source-constrained KB retrievals; a match is never a diagnosis."""
    source_system = _text(source_system, "source_system")
    if issue_type is not None and not isinstance(issue_type, str):
        raise ToolInputError("'issue_type' must be a string.")
    if ticket_fields is None:
        ticket_fields = {}
    if not isinstance(ticket_fields, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in ticket_fields.items()):
        raise ToolInputError("'ticket_fields' must map strings to strings.")
    field_text = {key: value.strip() for key, value in ticket_fields.items() if value.strip()}
    issue_norm = _normalise(issue_type or field_text.get("issue_type", ""))
    all_text = " ".join(field_text.values())
    normalized_text = _normalise(all_text)
    hits = []
    for doc in _load_json("product_docs.json", "documents"):
        if not isinstance(doc, dict) or _normalise(str(doc.get("source_system", ""))) != _normalise(source_system):
            continue
        signatures = doc.get("error_signatures", [])
        if not isinstance(signatures, list):
            signatures = []
        matched = []
        for signature in signatures:
            if not isinstance(signature, dict) or signature.get("type") not in {"message", "code"} or not isinstance(signature.get("value"), str):
                continue
            signature_text = _normalise(signature["value"])
            if not signature_text or not normalized_text:
                continue
            exact_field = any(_normalise(value) == signature_text for key, value in field_text.items() if key in {"error", "error_code"})
            exact_code = (
                signature.get("type") == "code"
                and isinstance(signature.get("value"), str)
                and any(
                    key in {"error", "error_code"}
                    and re.search(rf"(?<![A-Za-z0-9]){re.escape(signature['value'])}(?![A-Za-z0-9])", value, re.IGNORECASE)
                    for key, value in field_text.items()
                )
            )
            partial_field = any(signature_text in _normalise(value) or _normalise(value) in signature_text for value in field_text.values() if _normalise(value))
            if exact_field or exact_code:
                match_type = "candidate" if signature.get("mapping_status") == "candidate_retrieval_mapping" else "exact"
                matched.append({"type": signature["type"], "value": signature["value"], "match_type": match_type, "mapping_status": signature.get("mapping_status")})
            elif partial_field or signature_text in normalized_text:
                matched.append({"type": signature["type"], "value": signature["value"], "match_type": "candidate", "mapping_status": signature.get("mapping_status")})
        doc_issue = _normalise(str(doc.get("issue_type", "")))
        issue_matches = bool(issue_norm and doc_issue and (issue_norm == doc_issue or issue_norm in doc_issue or doc_issue in issue_norm))
        if matched:
            match_type = "exact" if any(item["match_type"] == "exact" for item in matched) else "candidate"
            hits.append({"document": doc, "document_id": doc.get("document_id"), "match_type": match_type, "matched_signatures": matched, "issue_type_match": issue_matches})
        elif issue_matches:
            hits.append({"document": doc, "document_id": doc.get("document_id"), "match_type": "candidate", "matched_signatures": [], "issue_type_match": True})
    return hits


def search_product_docs(source_system: str, error_message: str = "", issue_type: str = "", ticket_fields: dict | None = None) -> list[dict]:
    """Compatibility helper returning matched KB documents with retrieval metadata."""
    if error_message and not isinstance(error_message, str):
        raise ToolInputError("'error_message' must be a string.")
    if ticket_fields is not None and (not isinstance(ticket_fields, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in ticket_fields.items())):
        raise ToolInputError("'ticket_fields' must map strings to strings.")
    fields = dict(ticket_fields or {})
    if error_message:
        fields.setdefault("error", error_message)
    return [dict(hit["document"], _retrieval={key: hit[key] for key in ("match_type", "matched_signatures", "issue_type_match")}) for hit in _structured_retrieval(source_system, issue_type, fields)]


def verify_roles(
    source_system: str,
    gateway_roles: list[dict],
    backend_roles: list[dict],
) -> dict:
    source_system = _text(source_system, "source_system")
    if not isinstance(gateway_roles, list) or not isinstance(backend_roles, list):
        raise ToolInputError("'gateway_roles' and 'backend_roles' must be lists.")
    catalogues = _load_json("roles.json", "roles")
    catalogue = next(
        (item for item in catalogues if isinstance(item, dict) and _normalise(str(item.get("source_system", ""))) == _normalise(source_system)),
        None,
    )

    def classify(proposed: list[dict], kind: str) -> tuple[list[dict], list[dict]]:
        entries = catalogue.get(kind, []) if catalogue else []
        by_id = {entry.get("role_id"): entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get("role_id"), str)}
        verified, unverified = [], []
        for role in proposed:
            if not isinstance(role, dict) or not isinstance(role.get("role_id"), str) or not role["role_id"].strip():
                raise ToolInputError(f"Each proposed {kind} item must include a non-empty role_id.")
            role_id = role["role_id"].strip()
            if role_id in by_id:
                # Return catalogue values rather than model supplied descriptions.
                verified.append(by_id[role_id])
            else:
                unverified.append({"role_id": role_id, "reason": "Not present in this source system's role catalogue."})
        return verified, unverified

    verified_gateway, unverified_gateway = classify(gateway_roles, "gateway_roles")
    verified_backend, unverified_backend = classify(backend_roles, "backend_roles")
    return {
        "source_system": catalogue.get("source_system", source_system) if catalogue else source_system,
        "catalogue_found": catalogue is not None,
        # These are configured catalogue entries for the source system. Their
        # presence does not prove that a user has them or that they are missing.
        "configured_gateway_roles": catalogue.get("gateway_roles", []) if catalogue else [],
        "configured_backend_roles": catalogue.get("backend_roles", []) if catalogue else [],
        "gateway_roles": verified_gateway,
        "backend_roles": verified_backend,
        "unverified_gateway_roles": unverified_gateway,
        "unverified_backend_roles": unverified_backend,
    }


def _validate_tool_arguments(name: str, arguments: Any) -> dict:
    schemas = {
        "get_ticket": {"incident_number": str},
        "search_product_docs": {"source_system": str, "error_message": str},
        "verify_roles": {"source_system": str, "gateway_roles": list, "backend_roles": list},
    }
    schema = schemas.get(name)
    if schema is None:
        raise ToolInputError(f"Unsupported tool '{name}'.")
    if name == "search_product_docs" and isinstance(arguments, dict):
        allowed = {"source_system", "error_message", "issue_type", "ticket_fields"}
        if not {"source_system"}.issubset(arguments) or set(arguments) - allowed:
            raise ToolInputError("Invalid arguments for 'search_product_docs'; expected source_system and optional ticket evidence fields.")
        if "error_message" not in arguments and "ticket_fields" not in arguments:
            raise ToolInputError("'search_product_docs' requires error_message or ticket_fields.")
        for key in ("source_system", "error_message", "issue_type"):
            if key in arguments and not isinstance(arguments[key], str):
                raise ToolInputError(f"'{key}' for 'search_product_docs' must be a string.")
        if "ticket_fields" in arguments and (not isinstance(arguments["ticket_fields"], dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in arguments["ticket_fields"].items())):
            raise ToolInputError("'ticket_fields' must map strings to strings.")
        return arguments
    if not isinstance(arguments, dict) or set(arguments) != set(schema):
        raise ToolInputError(f"Invalid arguments for '{name}'; expected exactly: {', '.join(schema)}.")
    for key, expected_type in schema.items():
        if not isinstance(arguments[key], expected_type):
            raise ToolInputError(f"'{key}' for '{name}' must be {expected_type.__name__}.")
    return arguments


def execute_tool(name: str, arguments: Any) -> Any:
    arguments = _validate_tool_arguments(name, arguments)
    if name == "get_ticket":
        return get_ticket(arguments["incident_number"])
    if name == "search_product_docs":
        return search_product_docs(arguments["source_system"], arguments.get("error_message", ""), arguments.get("issue_type", ""), arguments.get("ticket_fields"))
    if name == "verify_roles":
        return verify_roles(arguments["source_system"], arguments["gateway_roles"], arguments["backend_roles"])
    raise ToolInputError(f"Unsupported tool '{name}'.")


TOOLS = [
    {
        "type": "function", "name": "get_ticket",
        "description": "Retrieve a support ticket using its incident number.",
        "parameters": {"type": "object", "properties": {"incident_number": {"type": "string"}}, "required": ["incident_number"], "additionalProperties": False},
    },
    {
        "type": "function", "name": "search_product_docs",
        "description": "Retrieve local KB candidates using source system, issue type, signatures, and available ticket fields; retrieval is not a diagnosis.",
        "parameters": {"type": "object", "properties": {"source_system": {"type": "string"}, "error_message": {"type": "string"}, "issue_type": {"type": "string"}, "ticket_fields": {"type": "object", "additionalProperties": {"type": "string"}}}, "required": ["source_system"], "additionalProperties": False},
    },
    {
        "type": "function", "name": "verify_roles",
        "description": "Check role IDs against the local catalogue. This does not check a user's assigned roles.",
        "parameters": {
            "type": "object",
            "properties": {
                "source_system": {"type": "string"},
                "gateway_roles": {
                    "type": "array", "items": {
                        "type": "object", "properties": {"role_id": {"type": "string"}},
                        "required": ["role_id"], "additionalProperties": False,
                    },
                },
                "backend_roles": {
                    "type": "array", "items": {
                        "type": "object", "properties": {"role_id": {"type": "string"}},
                        "required": ["role_id"], "additionalProperties": False,
                    },
                },
            },
            "required": ["source_system", "gateway_roles", "backend_roles"],
            "additionalProperties": False,
        },
    },
]


def update_ticket(incident_number: str, status: str, *, approved: bool = False) -> dict:
    """Update a ticket only when the existing UI/CLI human approval was given."""
    incident_number = _validate_incident_number(incident_number)
    if not approved:
        raise PermissionError("An explicit human approval is required before updating a ticket.")
    if status not in ALLOWED_STATUSES:
        raise ToolInputError("Unsupported ticket status. Choose a valid status from the ticket workflow.")
    try:
        current = get_ticket(incident_number)
        if status == "On Hold" and current.get("status") in {"Resolved", "Closed"}:
            raise PermissionError("Resolved or Closed tickets cannot be moved to On Hold.")
        response = requests.patch(
            f"{BASE_URL}/api/tickets/{incident_number}",
            json={"status": status, "expected_current_status": current.get("status"), "confirmed": True},
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
    except PermissionError:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(_safe_api_error(exc, "updating the ticket")) from None
    if not isinstance(result, dict) or not isinstance(result.get("ticket"), dict):
        raise RuntimeError("The ticket service returned an invalid update response.")
    return result


def _get_client() -> Any:
    global client

    if client is None:
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "OpenAI API key is missing. "
                "Set OPENAI_API_KEY in your .env file."
            )

        client = OpenAI(api_key=api_key)

    return client


def _safe_openai_error(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "The OpenAI request timed out. Check the connection and retry the investigation."
    if "authentication" in name or "permission" in name:
        return "OpenAI authentication failed. Check OPENAI_API_KEY configuration without sharing its value."
    if "connection" in name:
        return "Could not connect to OpenAI. Check the internet connection and retry."
    if "ratelimit" in name:
        return "OpenAI rate limit reached. Wait briefly and retry."
    return "The OpenAI investigation request failed. Check the OpenAI service and app configuration, then retry."


def _item_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


_MODEL_INTERNAL_KEYS = {
    "scenario_id", "source_status", "synthetic_data_notice", "review_metadata",
    "review_scope", "catalogue_status", "evidence_state", "mapping_status",
    "operational_incident_id", "document_id", "step_id",
}


def _professional_model_text(value: Any, *, parent: dict | None = None) -> Any:
    """Remove developer labels from model context while preserving evidence state."""
    if isinstance(value, dict):
        return {
            key: _professional_model_text(item, parent=value)
            for key, item in value.items() if key not in _MODEL_INTERNAL_KEYS
        }
    if isinstance(value, list):
        return [_professional_model_text(item, parent=parent) for item in value]
    if isinstance(value, str):
        for old, new in (
            ("Scenario-defined expected cause:", "Scenario-based expected cause:"),
            ("synthetic capability catalogue", "source-system capability reference"),
            ("synthetic catalogue", "source-system reference"),
            ("Synthetic catalogue", "Source-system reference"),
            ("synthetic scenario", "scenario"),
            ("Synthetic scenario", "Scenario"),
            ("synthetic examples", "historical case records"),
            ("Synthetic examples", "Historical case records"),
            ("Synthetic data only.", "Demonstration records; not validated against a live product."),
            ("scenario_expected_unverified", "scenario-based cause expectation; unverified"),
            ("matched_synthetic_scenario", "scenario match"),
            ("verified_synthetic_historical_case", "verified for the source incident"),
            ("verified_synthetic_demo", "verified for the source incident"),
            ("Synthetic demo role identifier only; not a production permission or evidence of a user assignment.",
             "Role catalogue reference only; this does not establish a user's current assignment."),
        ):
            value = value.replace(old, new)
        if parent and parent.get("role_name") == value and value.startswith("Synthetic "):
            value = value.removeprefix("Synthetic ")
        if parent and parent.get("description") == value and value == "Synthetic demo role identifier only; not a production permission or evidence of a user assignment.":
            value = "Role catalogue reference only; this does not establish a user's current assignment."
        return value
    return value


def _tool_candidates(docs: list[dict]) -> tuple[list[dict], list[dict]]:
    gateway, backend = [], []
    for doc in docs:
        references = doc.get("role_references", {})
        if not isinstance(references, dict):
            continue
        for key, dest in (("gateway_role_ids", gateway), ("backend_role_ids", backend)):
            values = references.get(key, [])
            if isinstance(values, list):
                for role_id in values:
                    if isinstance(role_id, str):
                        dest.append({"role_id": role_id})
    return gateway, backend


def _ticket_understanding(ticket: dict) -> dict:
    values = {
        "ticket_id": ticket.get("incident_number"), "source_system": ticket.get("source_system") or ticket.get("system"),
        "issue_type": ticket.get("issue_type") or ticket.get("issue") or ticket.get("category"),
        "error_message_or_code": ticket.get("error_message") or ticket.get("test_detail") or ticket.get("additional_comments"),
        "description": ticket.get("description") or ticket.get("short_description"), "relevant_context": {
            key: ticket.get(key) for key in ("summary", "test", "category", "subcategory") if ticket.get(key) is not None
        },
        "priority": ticket.get("priority"), "status": ticket.get("status"),
        "impact": ticket.get("impact"), "urgency": ticket.get("urgency"),
    }
    missing = [key for key, value in values.items() if key != "relevant_context" and (value is None or value == "")]
    conflicts = []
    if ticket.get("system") and ticket.get("source_system") and _normalise(str(ticket["system"])) != _normalise(str(ticket["source_system"])):
        conflicts.append("Ticket contains conflicting system and source_system values.")
    if ticket.get("issue") and ticket.get("issue_type") and _normalise(str(ticket["issue"])) != _normalise(str(ticket["issue_type"])):
        conflicts.append("Ticket contains conflicting issue and issue_type values.")
    values["missing_information"] = missing
    values["conflicting_information"] = conflicts
    return values


def _condition_matches(condition: Any, ticket: dict) -> bool:
    """Evaluate the small, explicit condition language used by reviewed KB entries."""
    if not isinstance(condition, dict) or not isinstance(condition.get("field"), str):
        return False
    field = condition["field"]
    actual = ticket.get(field)
    operator = condition.get("operator")
    if operator == "present":
        return actual is not None and bool(str(actual).strip())
    if not isinstance(actual, str):
        return False
    actual_norm = _normalise(actual)
    if operator == "equals":
        return isinstance(condition.get("value"), str) and actual_norm == _normalise(condition["value"])
    if operator == "contains":
        return isinstance(condition.get("value"), str) and bool(_normalise(condition["value"])) and _normalise(condition["value"]) in actual_norm
    if operator in {"contains_all", "contains_any"}:
        values = condition.get("values")
        if not isinstance(values, list) or not values or not all(isinstance(value, str) and _normalise(value) for value in values):
            return False
        checks = [_normalise(value) in actual_norm for value in values]
        return all(checks) if operator == "contains_all" else any(checks)
    if operator == "in":
        values = condition.get("values")
        return isinstance(values, list) and bool(values) and all(isinstance(value, str) for value in values) and actual_norm in {_normalise(value) for value in values}
    return False


def _conditions_are_valid(conditions: Any) -> bool:
    if not isinstance(conditions, list):
        return False
    for condition in conditions:
        if not isinstance(condition, dict) or not isinstance(condition.get("field"), str):
            return False
        operator = condition.get("operator")
        if operator == "present":
            continue
        if operator in {"equals", "contains"}:
            if not isinstance(condition.get("value"), str) or not condition["value"].strip():
                return False
        elif operator in {"contains_all", "contains_any", "in"}:
            values = condition.get("values")
            if not isinstance(values, list) or not values or not all(isinstance(value, str) and value.strip() for value in values):
                return False
        else:
            return False
    return True


def _condition_evidence(conditions: list, ticket: dict) -> list[dict]:
    return [
        {
            "field": condition["field"],
            "operator": condition["operator"],
            "expected": condition.get("value", condition.get("values")),
            "observed": ticket.get(condition["field"]),
            "matched": _condition_matches(condition, ticket),
        }
        for condition in conditions
    ]


def _same_ticket_verified_resolution(ticket: dict) -> dict | None:
    """Read consultant-verified resolution data scoped to this incident."""
    try:
        records = _load_json("resolution_records.json", "verified_resolutions")
    except RuntimeError:
        return None
    return next((record for record in records if isinstance(record, dict)
                 and record.get("incident_number") == ticket.get("incident_number")
                 and record.get("verified") is True), None)


def _resolution_eligibility(hit: dict, ticket: dict) -> tuple[bool, str]:
    """Require explicit reviewed status, applicability, evidence, resolution and verification."""
    if ticket.get("status") in {"Resolved", "Closed", 4, 5}:
        return False, "Resolved or Closed tickets are not eligible for a KB resolution recommendation."
    doc = hit.get("document")
    if not isinstance(doc, dict) or doc.get("source_status") != "demo_reviewed":
        return False, "The matched document is not explicitly demo_reviewed."
    metadata = doc.get("review_metadata")
    if (not isinstance(metadata, dict) or metadata.get("review_state") != "demo_reviewed"
            or metadata.get("review_scope") != "synthetic_demo_only"
            or metadata.get("real_product_validation") is not False):
        return False, "This guidance has not been reviewed for use as a resolution procedure."
    if hit.get("match_type") != "exact":
        return False, "Only a candidate retrieval matched; exact scenario evidence is required."
    applicability = doc.get("applicability")
    conditions = applicability.get("structured_conditions") if isinstance(applicability, dict) else None
    if not _conditions_are_valid(conditions) or not conditions or not all(_condition_matches(item, ticket) for item in conditions):
        return False, "Structured applicability conditions are missing or not satisfied."
    evidence = doc.get("required_ticket_evidence")
    if not _conditions_are_valid(evidence) or not evidence or not all(_condition_matches(item, ticket) for item in evidence):
        return False, "One or more required ticket evidence checks are missing or unsatisfied."
    exclusions = applicability.get("exclusion_conditions", [])
    if not _conditions_are_valid(exclusions) or any(_condition_matches(item, ticket) for item in exclusions):
        return False, "An exclusion condition applies or cannot be evaluated safely."
    escalations = doc.get("escalation_conditions", [])
    if not _conditions_are_valid(escalations) or any(_condition_matches(item, ticket) for item in escalations):
        return False, "An escalation condition applies or cannot be evaluated safely."
    resolutions = doc.get("resolution_steps")
    verifications = doc.get("verification_steps")
    complete_step = lambda step: isinstance(step, dict) and all(isinstance(step.get(key), str) and step[key].strip() for key in ("step_id", "instruction", "expected_observation", "handling_when_unclear"))
    if not isinstance(resolutions, list) or not resolutions or not all(complete_step(step) for step in resolutions):
        return False, "Reviewed resolution steps are missing or incomplete."
    if not isinstance(doc.get("expected_outcome"), str) or not doc["expected_outcome"].strip():
        return False, "The expected outcome is missing."
    if not isinstance(verifications, list) or not verifications or not all(complete_step(step) for step in verifications):
        return False, "Verification steps are missing or incomplete."
    if not isinstance(doc.get("escalation_criteria"), list) or not doc["escalation_criteria"]:
        return False, "Escalation criteria are missing."
    if not isinstance(doc.get("allowed_actions"), list):
        return False, "The allowed-actions field is invalid."
    return True, "The demo-reviewed scenario, ticket evidence, applicability, resolution, verification, and escalation checks are complete."


def _facts_and_decision(ticket: dict, retrievals: list[dict], verified: dict, model_error: str | None, candidate: str = "") -> dict:
    exact = [hit for hit in retrievals if hit["match_type"] == "exact"]
    match_state = "exact_match" if exact else "candidate_match" if retrievals else "no_match"
    resolution_docs = []
    eligibility_reasons = []
    understanding = _ticket_understanding(ticket)
    has_conflicts = bool(understanding["conflicting_information"])
    scenarios = match_scenarios(ticket)
    scenario = scenarios[0] if scenarios else None
    verified_resolution = _same_ticket_verified_resolution(ticket)
    for hit in retrievals:
        eligible, eligibility_reason = _resolution_eligibility(hit, ticket)
        eligibility_reasons.append((hit, eligible, eligibility_reason))
        if eligible and hit["match_type"] == "exact" and not has_conflicts:
            resolution_docs.append(hit)
    if scenario:
        # The generic reviewed visibility procedure does not apply to this
        # more specific access-role scenario. Keep retrieval, require L2 checks.
        resolution_docs = []
    resolution_path = "KB_RESOLVABLE" if resolution_docs and not has_conflicts else "HUMAN_SUPPORT"
    if not retrievals:
        reason = "No structured KB signature or source-and-issue candidate matched the ticket. A human investigation is required."
    elif not exact:
        reason = "Only candidate KB retrievals were found; they are not sufficient to establish applicability or diagnosis."
    elif scenario and verified_resolution:
        reason = "A consultant-verified resolution is recorded for this incident and takes precedence over the scenario expectation. The outcome and any further status change remain separate human-controlled steps."
    elif scenario:
        reason = "A scenario-based signal points to access roles as one possible cause, but user role assignments are unavailable and the generic visibility procedure does not apply. Verify the configured roles through the authorized process; do not infer a missing role."
    elif resolution_path == "HUMAN_SUPPORT":
        failed_reasons = [why for hit, eligible, why in eligibility_reasons if not eligible]
        detail = failed_reasons[0] if failed_reasons else "Ticket information conflicts or only candidate retrievals are available."
        reason = f"The deterministic KB_RESOLVABLE checks did not pass: {detail} A KB match does not confirm root cause."
    else:
        reason = "The scenario's applicability and required evidence checks pass, with complete proposed resolution, expected outcome, verification, and escalation guidance. The user-reported outcome remains unverified."

    ticket_understanding = _ticket_understanding(ticket)
    missing = list(ticket_understanding["missing_information"])
    required_prompts = [
        item for hit in retrievals if hit["match_type"] == "exact" and hit not in resolution_docs
        for item in hit["document"].get("required_information", [])
        if isinstance(item, str)
    ]
    if resolution_docs:
        required_prompts.extend(
            item for hit in resolution_docs
            for item in hit["document"].get("required_information", [])
            if isinstance(item, str) and ("if known" in item.lower())
        )
    information_gaps = missing + [f"Not verified from the ticket: {item}" for item in required_prompts]
    diagnostic_hits = resolution_docs if resolution_docs else retrievals
    diagnostic_steps = [
        {"document_id": hit["document_id"], "retrieval_match_type": hit["match_type"],
         "applicability": "requires_human_review" if hit["match_type"] == "candidate" else "retrieved_document_only",
         **step}
        for hit in diagnostic_hits
        for step in hit["document"].get("diagnostic_steps", [])
        if isinstance(step, dict)
    ]
    if scenario and not verified_resolution:
        # The scenario is more specific than the shared visibility article.
        # Keep retrieval visible, but don't let generic filter/view guidance lead.
        diagnostic_steps = [
            {"document_id": scenario["scenario_id"], "retrieval_match_type": "scenario_match",
             "applicability": "matched_synthetic_scenario", **step}
            for step in scenario.get("recommended_actions", []) if isinstance(step, dict)
        ]
        information_gaps = [
            "The user's current Gateway and Backend role assignments have not been verified."
        ]
    elif scenario and verified_resolution:
        diagnostic_steps = []
        information_gaps = []
    evidence_limitations = [
        "KB retrieval does not confirm root cause or prove that a resolution will succeed.",
        "Role catalogue membership does not establish a user's assigned roles.",
        "Demonstration procedures have not been independently validated against product behavior.",
        "The investigation cannot verify the user's reported recovery outcome.",
    ]
    if model_error:
        evidence_limitations.append("AI candidate analysis was unavailable; deterministic retrieval and decision output remain available.")
    evidence = {
        "ticket_facts": {key: value for key, value in ticket.items()},
        "kb_retrievals": [{key: hit[key] for key in ("document_id", "match_type", "matched_signatures", "issue_type_match")} for hit in retrievals],
        "role_catalogue_validation": verified,
        "role_assignment_evidence": "No user-role assignments are present in the retrieved data; catalogue membership does not establish a user's access.",
        "kb_information_to_confirm": required_prompts,
        "kb_required_information_machine_checkable": False,
        "resolution_eligibility_checks": [
            {
                "document_id": hit["document_id"],
                "applicability": _condition_evidence(hit["document"]["applicability"]["structured_conditions"], ticket),
                "required_ticket_evidence": _condition_evidence(hit["document"]["required_ticket_evidence"], ticket),
                "exclusions": _condition_evidence(hit["document"]["applicability"].get("exclusion_conditions", []), ticket),
                "escalations": _condition_evidence(hit["document"].get("escalation_conditions", []), ticket),
            }
            for hit in resolution_docs
        ],
        "limitations": evidence_limitations,
        "retrieval_is_diagnosis": False,
    }
    if verified_resolution:
        root_cause_assessment = {
            "state": "consultant_verified",
            "statement": verified_resolution.get("confirmed_root_cause", ""),
            "source": "verified_resolution",
            "source_id": verified_resolution.get("resolution_id"),
            "verified": True,
        }
    elif scenario:
        cause = scenario.get("root_cause", {})
        root_cause_assessment = {
            "state": cause.get("state", "likely_scenario_defined"),
            "statement": cause.get("statement", "The scenario provides a likely cause, but it is not verified for this user."),
            "source": "synthetic_scenario",
            "source_id": scenario.get("scenario_id"),
            "verified": False,
        }
    elif candidate:
        root_cause_assessment = {
            "state": "unverified_hypothesis", "statement": candidate,
            "source": "model_hypothesis", "source_id": None, "verified": False,
        }
    else:
        root_cause_assessment = {
            "state": "insufficient_evidence",
            "statement": "The available ticket and applicable knowledge do not establish a likely root cause.",
            "source": None, "source_id": None, "verified": False,
        }
    hypotheses = [] if root_cause_assessment["state"] == "insufficient_evidence" else [{
        "text": root_cause_assessment["statement"], "status": root_cause_assessment["state"],
        "source": root_cause_assessment["source"], "source_id": root_cause_assessment["source_id"],
    }]
    if model_error:
        next_steps = ["Retry the AI analysis if useful; continue with a human consultant using the retrieved ticket and KB evidence."]
    elif resolution_path == "HUMAN_SUPPORT":
        next_steps = ["Have a human consultant review the ticket and applicable diagnostic steps.", "Request any listed missing observations from the reporter."]
    else:
        next_steps = ["Offer the proposed user-guided steps from the matched knowledge document.", "Ask the user to report the retest result; keep the outcome unverified until a consultant records and reviews it."]
    status = "no_matching_evidence" if not retrievals else "confirmed" if exact else "insufficient_evidence"
    recommendation = "Continue with human support; no status change or access action is proposed." if resolution_path == "HUMAN_SUPPORT" else "Offer the proposed user-guided recovery steps; record the user's report and keep the outcome unverified until separately confirmed."
    resolution_steps = [
        {"document_id": hit["document_id"], **step}
        for hit in resolution_docs
        for step in hit["document"].get("resolution_steps", [])
    ]
    if scenario:
        # Scenario actions are investigation guidance, not a reviewed KB
        # resolution procedure and therefore do not make this KB_RESOLVABLE.
        resolution_steps = []
    verification_steps = [
        {"document_id": hit["document_id"], **step}
        for hit in resolution_docs
        for step in hit["document"].get("verification_steps", [])
    ]
    if scenario and not verified_resolution:
        verification_steps = [{
            "document_id": scenario["scenario_id"], "step_id": f"{scenario['scenario_id']}-V1",
            "instruction": scenario.get("verification_instruction", "Ask the reporter to retest and record the result."),
            "expected_observation": "The reporter can view and open the referenced approval item.",
            "handling_when_unclear": "Record the exact observed result; do not mark the outcome verified.",
        }]
    elif scenario and verified_resolution:
        evidence_text = verified_resolution.get("verification_evidence", "")
        verification_steps = [{
            "document_id": verified_resolution.get("resolution_id"), "step_id": "VERIFIED-RESOLUTION-EVIDENCE",
            "instruction": str(evidence_text) if evidence_text else "A consultant-recorded verification result is available for this incident.",
            "expected_observation": "Use the persisted consultant verification record.",
            "handling_when_unclear": "Review the saved resolution record with the consultant.",
        }]
    expected_outcomes = [hit["document"]["expected_outcome"] for hit in resolution_docs]
    escalation_criteria = [
        {"document_id": hit["document_id"], "criteria": hit["document"].get("escalation_criteria", [])}
        for hit in resolution_docs
    ]
    supporting_evidence_lines = [
        f"{check['field']}: {check['observed']}"
        for item in evidence["resolution_eligibility_checks"]
        for section in ("applicability", "required_ticket_evidence")
        for check in item[section]
        if check["matched"]
    ]
    final_analysis = (
        f"Retrieval outcome: {match_state.replace('_', ' ').title()}\n\n"
        f"Resolution path: {resolution_path}\n\n"
        f"Decision: {reason}\n\n"
        + ("Supporting ticket evidence:\n" + "\n".join(f"- {line}" for line in supporting_evidence_lines) + "\n\n" if supporting_evidence_lines else "")
        + (("Proposed user-guided resolution (not yet performed):\n" if resolution_path == "KB_RESOLVABLE" else "Recommended investigation actions (not performed):\n") + "\n".join(f"{index}. {step['instruction']}" for index, step in enumerate(resolution_steps or diagnostic_steps, 1)) + "\n\n" if resolution_steps or (scenario and not verified_resolution) else "")
        + ("Expected outcome (not verified): " + " ".join(expected_outcomes) + "\n\n" if expected_outcomes else "")
        + ("Verification steps:\n" + "\n".join(f"- {step['instruction']}" for step in verification_steps) + "\n\n" if verification_steps else "")
        + ("Escalate if:\n" + "\n".join(f"- {criterion}" for group in escalation_criteria for criterion in group["criteria"]) + "\n\n" if escalation_criteria else "")
        + "A retrieval match is not proof of root cause or of the user's assigned roles. No ticket update, role request, or access change was performed. The outcome remains unverified."
    )
    if scenario:
        final_analysis += "\n\nScenario-based expected cause; not independently verified."
        if verified_resolution:
            final_analysis += " A consultant-verified resolution for this incident takes precedence over the scenario expectation."
    facts = {"ticket": ticket, "matching_documents": [hit["document"] for hit in retrievals], "role_catalogue_check": verified,
             "user_assigned_roles": "Not present in the available ticket or data files."}
    return {
        "ticket_understanding": ticket_understanding,
        "root_cause_assessment": root_cause_assessment,
        "verified_resolution_record": verified_resolution,
        "scenario_matches": [{"scenario_id": scenario.get("scenario_id"), "source_status": scenario.get("source_status")} ] if scenario else [],
        "scenario_evidence": scenario,
        "kb_matches": [{key: hit[key] for key in ("document_id", "match_type", "matched_signatures", "issue_type_match")} for hit in retrievals],
        "evidence": evidence, "hypotheses": hypotheses, "information_gaps": information_gaps,
        "evidence_limitations": evidence_limitations,
        "diagnostic_steps": diagnostic_steps, "recommended_troubleshooting_steps": diagnostic_steps,
        "resolution_path": resolution_path,
        "proposed_resolution_steps": resolution_steps, "expected_outcomes": expected_outcomes,
        "verification_steps": verification_steps, "escalation_criteria": escalation_criteria,
        "resolution_outcome_status": "proposed_unverified" if resolution_steps else "not_applicable",
        "decision_reason": reason, "recommended_next_steps": next_steps,
        "proposed_actions": [], "approval_required": False,
        "evidence_status": status, "retrieved_facts": facts,
        "candidate_findings": candidate,
        "recommendation": {"action": "human_investigation" if resolution_path == "HUMAN_SUPPORT" else "review_kb_steps", "text": recommendation},
        "final_analysis": final_analysis, "approval_needed": False,
        "already_on_hold": ticket.get("status") == "On Hold",
    }


def _preflight(incident_number: str) -> tuple[dict, list[dict], dict, list[dict], list[dict]]:
    ticket = get_ticket(incident_number)
    source_system = ticket.get("source_system") or ticket.get("system")
    fields = _ticket_search_fields(ticket)
    error_message = fields.get("error", "")
    if not isinstance(source_system, str) or not source_system.strip():
        raise RuntimeError("The ticket is missing a valid source system; a human must investigate it.")
    retrievals = _structured_retrieval(source_system, str(ticket.get("issue", "")), fields)
    docs = [hit["document"] for hit in retrievals]
    gateway, backend = _tool_candidates(docs)
    verified = verify_roles(source_system, gateway, backend)
    calls = [
        {"name": "get_ticket", "arguments": {"incident_number": incident_number}, "result": ticket},
        {"name": "search_product_docs", "arguments": {"source_system": source_system, "issue_type": ticket.get("issue", ""), "ticket_fields": fields}, "result": search_product_docs(source_system, error_message, str(ticket.get("issue", "")), fields)},
        {"name": "verify_roles", "arguments": {"source_system": source_system, "gateway_roles": gateway, "backend_roles": backend}, "result": verified},
    ]
    return ticket, docs, verified, calls, retrievals


def run_investigation(incident_number: str, *, use_llm: bool = True) -> dict:
    """Retrieve evidence deterministically, then use the model for candidate analysis."""
    result = {
        "ticket": None, "steps": [], "final_analysis": "", "candidate_findings": "",
        "retrieved_facts": {}, "recommendation": {"action": "human_investigation", "text": ""},
        "evidence_status": "insufficient_evidence", "approval_needed": False,
        "already_on_hold": False, "error": None, "ticket_understanding": {}, "kb_matches": [],
        "evidence": {}, "hypotheses": [], "information_gaps": [], "diagnostic_steps": [],
        "captured_knowledge_matches": [], "captured_knowledge_used": [], "captured_knowledge_error": None,
        "resolution_path": "HUMAN_SUPPORT", "decision_reason": "Investigation has not completed.",
        "recommended_next_steps": [], "proposed_actions": [], "approval_required": False,
    }
    try:
        incident_number = _validate_incident_number(incident_number)
        ticket, docs, verified, calls, retrievals = _preflight(incident_number)
    except ToolInputError as exc:
        result["error"] = str(exc)
        result["final_analysis"] = "Investigation could not start. " + str(exc)
        return result
    except RuntimeError as exc:
        result["error"] = str(exc)
        result["final_analysis"] = "Investigation could not retrieve its required evidence. " + str(exc)
        return result

    try:
        from agent.captured_knowledge import search_approved_articles
        captured, captured_error = search_approved_articles(ticket)
    except Exception:
        captured, captured_error = [], "Approved captured knowledge could not be retrieved."
    result["captured_knowledge_matches"] = captured
    result["captured_knowledge_error"] = captured_error

    result["ticket"] = ticket
    result["steps"].append({"iteration": 1, "tool_calls": calls})
    candidate = ""
    model_error = None
    input_items: list[Any] = [{"role": "user", "content": (
        "Review the following retrieved facts. Treat them as the only factual evidence. "
        "Return candidate findings only; do not claim a user's roles are missing, a root cause is confirmed, "
        "or a status change has occurred. State any uncertainty.\n\n"
        + json.dumps({
            "ticket": {
                key: ticket.get(key) for key in (
                    "incident_number", "source_system", "system", "issue_type", "issue",
                    "category", "subcategory", "short_description", "summary", "description",
                    "additional_comments", "error_message", "test_detail", "test",
                    "priority", "status", "impact", "urgency",
                ) if ticket.get(key) not in (None, "")
            },
            "matching_documents": _professional_model_text([
                {
                    **{key: doc[key] for key in (
                        "source_system", "issue_type", "topic", "title", "symptoms",
                        "possible_causes", "required_information", "diagnostic_steps",
                        "escalation_criteria", "known_limitations",
                    ) if key in doc},
                    "review_status": "Draft; not independently validated against product behavior.",
                }
                for doc in docs if isinstance(doc, dict)
            ]),
            "role_catalogue_check": _professional_model_text(verified),
            "approved_captured_knowledge": _professional_model_text(captured),
        }, ensure_ascii=False)
    )}]
    instructions = (
        "You are a cautious Level 2 support investigation assistant. Write in professional enterprise support language. "
        "Separate retrieved facts from hypotheses. Do not mention developer labels such as mock, synthetic, fixture, "
        "or scenario numbers in user-facing findings. When describing an expected cause, label it scenario-based and "
        "unverified; never imply validation against a live product. Do not expose internal scenario or operational record IDs. "
        "The role catalogue only says which role IDs exist for a source system; no user assignments are available. "
        "Approved captured articles are verified records for their source incidents only. Use matching approved articles "
        "as supporting evidence, cite article ID and source incident, and do not transfer their confirmed cause or "
        "resolution outcome to the current ticket without current-ticket verification. "
        "Never claim a missing role, confirmed root cause, access granted, or status update. "
        "Use tools only for additional read-only lookup. Do not recommend an action unsupported by the provided documents."
    )

    api = None
    if use_llm:
        try:
            api = _get_client()
        except RuntimeError as exc:
            model_error = str(exc)

    if api is not None:
        for iteration in range(MAX_ITERATIONS):
            try:
                response = api.responses.create(model=MODEL, instructions=instructions, tools=TOOLS, input=input_items)
            except Exception as exc:
                model_error = _safe_openai_error(exc)
                break
            output = _item_value(response, "output")
            if not isinstance(output, (list, tuple)):
                model_error = "OpenAI returned an unexpected response. Retry the investigation."
                break
            output_text = _item_value(response, "output_text", "")
            if not isinstance(output_text, str):
                model_error = "OpenAI returned malformed analysis text. Retry the investigation."
                break
            try:
                input_items.extend(output)
                step_calls = []
                for item in output:
                    item_type = _item_value(item, "type")
                    if item_type != "function_call":
                        continue
                    name = _item_value(item, "name")
                    raw_arguments = _item_value(item, "arguments")
                    call_id = _item_value(item, "call_id")
                    if not isinstance(name, str) or not isinstance(raw_arguments, str) or not isinstance(call_id, str):
                        raise ValueError("Malformed function call.")
                    arguments = {}
                    try:
                        arguments = json.loads(raw_arguments)
                        tool_result = execute_tool(name, arguments)
                    except (json.JSONDecodeError, ToolInputError, RuntimeError) as exc:
                        tool_result = {"error": str(exc) if isinstance(exc, (ToolInputError, RuntimeError)) else "Tool arguments must be valid JSON."}
                    step_calls.append({"name": name, "arguments": arguments, "result": tool_result})
                    input_items.append({"type": "function_call_output", "call_id": call_id, "output": json.dumps(_professional_model_text(tool_result), ensure_ascii=False)})
                if step_calls:
                    result["steps"].append({"iteration": iteration + 2, "tool_calls": step_calls})
                    continue
                if not output_text.strip():
                    model_error = "OpenAI returned empty analysis text. Retry the investigation."
                    break
                candidate = output_text.strip()
                break
            except (TypeError, ValueError, AttributeError) as exc:
                model_error = "OpenAI returned a malformed tool response. Retry the investigation."
                break
        else:
            model_error = "The agent reached its tool-call limit without completing analysis. Retry or investigate manually."

    decision = _facts_and_decision(ticket, retrievals, verified, model_error, candidate)
    result.update(decision)
    # The new Unified Inbox workflow is grounded in explicit scenario,
    # source-system, role, capability, and operational records. These
    # deterministic findings supplement the generic KB result and cannot
    # upgrade draft knowledge to a resolved ticket.
    guidance = investigation_guidance(ticket, verified, result.get("verified_resolution_record"))
    if guidance.get("scenario_matches"):
        result.update({key: value for key, value in guidance.items() if key != "evidence_extension"})
        evidence = result.setdefault("evidence", {})
        if isinstance(evidence, dict):
            evidence["unified_inbox"] = guidance.get("unified_inbox_evidence", {})
            evidence["role_assignment_evidence"] = (
                "The presence of a source-system catalogue role does not establish a user's access; the user's assignments are not present or verified."
            )
        result["final_analysis"] = (
            f"Issue: {ticket.get('issue_type') or ticket.get('issue')}.\n\n"
            f"Scenario expectation (unverified): {result['root_cause_assessment']['statement']}\n\n"
            f"Resolution path: {result['resolution_path']}\n\n"
            "A knowledge or scenario match is not proof of root cause. The scenario and role catalogue provide investigation guidance; they do not verify a root cause, user assignment, or successful outcome."
        )
        if result.get("verified_resolution_record"):
            result["final_analysis"] = (
                f"Issue: {ticket.get('issue_type') or ticket.get('issue')}.\n\n"
                f"Consultant-verified root cause: {result['root_cause_assessment']['statement']}\n\n"
                "The saved consultant resolution is scoped to this incident; scenario expectations are not used as its cause."
            )
    # Approved articles are supporting evidence only. They never enter the
    # deterministic KB_RESOLVABLE eligibility inputs above.
    used_articles = []
    for article in captured:
        source_steps = article.get("verified_troubleshooting_steps", [])
        used = {
            "article_id": article.get("article_id"),
            "source_incident": article.get("source_incident"),
            "title": article.get("title"),
            "knowledge_source": "approved_captured_article",
            "review_status": "Approved",
            "matching_criteria": article.get("retrieval_match", {}),
            "source_verified_cause": article.get("confirmed_root_cause"),
            "source_verified_resolution": source_steps if isinstance(source_steps, list) else [],
        }
        used_articles.append(used)
    result["captured_knowledge_used"] = used_articles
    if used_articles:
        evidence = result.setdefault("evidence", {})
        if isinstance(evidence, dict):
            evidence["approved_captured_knowledge"] = used_articles
        citations = []
        for article in used_articles:
            steps = "; ".join(article["source_verified_resolution"])
            citations.append(
                f"Based on approved knowledge: {article['article_id']} · {article['source_incident']}. "
                f"That source ticket recorded the verified cause: {article['source_verified_cause']}. "
                f"Its verified resolution guidance: {steps}. This is supporting evidence from the source ticket only; "
                "the current user's role assignments, cause, and outcome remain unverified."
            )
        result["final_analysis"] += "\n\n" + "\n\n".join(citations)
    result["error"] = model_error
    return result


def analyze_ticket(incident_number: str) -> None:
    """CLI wrapper; status changes still require an explicit yes/no approval."""
    result = run_investigation(incident_number)
    print(result["final_analysis"])
    if result.get("candidate_findings"):
        print("\nCandidate AI findings (unverified):\n" + result["candidate_findings"])
    if result.get("error"):
        print("\n" + result["error"])
    if not result.get("approval_needed"):
        return
    approval = input("\nApprove moving this ticket to 'On Hold'? (yes/no): ").strip().lower()
    if approval != "yes":
        print("Action cancelled. Ticket status was not changed.")
        return
    try:
        updated = update_ticket(incident_number, "On Hold", approved=True)
        print(f"Ticket updated to {updated['ticket'].get('status')}.")
    except (RuntimeError, ToolInputError, PermissionError) as exc:
        print(str(exc))


if __name__ == "__main__":
    analyze_ticket(input("Enter incident number: ").strip())
