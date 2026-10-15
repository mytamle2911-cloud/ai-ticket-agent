"""Context-grounded, read-only conversational support for the Streamlit copilot."""

from __future__ import annotations

import json
import re
from typing import Any

from agent import llm_agent
from agent.history import search_historical_cases
from agent.scenario_knowledge import match_scenarios


MAX_MESSAGE_LENGTH = 4000
MAX_HISTORY_MESSAGES = 12


def append_message(
    conversations: dict[str, list[dict[str, str]]],
    incident_number: str,
    role: str,
    content: str,
) -> None:
    """Append a chat turn under its incident so conversations cannot bleed across tickets."""
    if not isinstance(conversations, dict):
        raise ValueError("Conversation storage must be a dictionary.")
    if not isinstance(incident_number, str) or not incident_number.strip():
        raise ValueError("A valid incident number is required for chat history.")
    if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
        raise ValueError("Chat messages require a user or assistant role and non-empty text.")
    messages = conversations.setdefault(incident_number, [])
    if not isinstance(messages, list):
        messages = []
        conversations[incident_number] = messages
    messages.append({"role": role, "content": content.strip()})


def _ticket_context(ticket: dict[str, Any]) -> dict[str, Any]:
    """Keep only incident facts needed for support; omit reporter contact details."""
    fields = (
        "incident_number", "system", "issue", "category", "subcategory", "summary",
        "source_system", "issue_type", "short_description", "additional_comments",
        "error_message", "test", "test_detail", "description", "priority", "status",
        "impact", "urgency",
    )
    context = {key: ticket.get(key) for key in fields if ticket.get(key) not in (None, "")}
    aliases = {
        "source_system": ("source_system", "system"),
        "issue_type": ("issue_type", "issue"),
        "short_description": ("short_description", "summary"),
        "additional_comments": ("additional_comments", "description"),
        "error_message": ("error_message", "test_detail", "test"),
    }
    for canonical, keys in aliases.items():
        if context.get(canonical) in (None, ""):
            value = next((ticket.get(key) for key in keys if ticket.get(key) not in (None, "")), None)
            if value is not None:
                context[canonical] = value
    return context


def _document_context(investigation: dict[str, Any]) -> list[dict[str, Any]]:
    facts = investigation.get("retrieved_facts", {})
    docs = facts.get("matching_documents", []) if isinstance(facts, dict) else []
    if not isinstance(docs, list):
        return []
    scenario_applies = bool(investigation.get("scenario_matches"))
    fields = (
        "source_system", "issue_type", "topic", "title",
        "error_signatures", "symptoms", "possible_causes", "required_information",
        "diagnostic_steps", "escalation_criteria", "known_limitations", "role_references",
        "gateway_roles", "backend_roles",
    )
    if investigation.get("resolution_path") == "KB_RESOLVABLE":
        fields = (*fields, "resolution_steps", "verification_steps", "expected_outcome")
    if scenario_applies:
        fields = tuple(key for key in fields if key not in {
            "possible_causes", "resolution_steps", "verification_steps", "expected_outcome",
        })
    return [
        {
            **{key: doc[key] for key in fields if key in doc},
            **({"knowledge_status": "Draft guidance; not independently validated against product behavior."}
               if doc.get("source_status") == "synthetic_draft" else {}),
        }
        for doc in docs if isinstance(doc, dict)
    ]


_INTERNAL_CONTEXT_KEYS = {
    "scenario_id", "source_status", "synthetic_data_notice", "catalogue_notice",
    "role_catalogue_notice", "catalogue_status", "evidence_state", "mapping_status",
    "review_metadata", "document_id", "step_id",
}


def _professional_context(value: Any, *, parent: dict | None = None) -> Any:
    """Remove developer labels from model context while retaining evidence and caveats."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key in _INTERNAL_CONTEXT_KEYS:
                continue
            if key == "source_id" and value.get("source") == "synthetic_scenario":
                continue
            if key in {"operational_incident_id", "simulation_timestamp"}:
                continue
            if key == "effective_status_at_simulation":
                cleaned["effective_status_at_reference_time"] = _professional_context(item, parent=value)
                continue
            cleaned[key] = _professional_context(item, parent=value)
        return cleaned
    if isinstance(value, list):
        return [_professional_context(item, parent=parent) for item in value]
    if isinstance(value, str):
        for old, new in (
            ("Scenario-defined expected cause:", "Scenario-based expected cause:"),
            ("scenario_expected_unverified", "scenario-based cause expectation; unverified"),
            ("synthetic_scenario", "scenario-based reference"),
            ("synthetic capability catalogue", "source-system capability reference"),
            ("synthetic catalogue", "source-system reference"),
            ("Synthetic catalogue", "Source-system reference"),
            ("synthetic scenario", "scenario"),
            ("Synthetic scenario", "Scenario"),
            ("synthetic examples", "historical case records"),
            ("Synthetic examples", "Historical case records"),
            ("synthetic L2 support workspace", "L2 Support workspace"),
            ("verified_synthetic_historical_case", "verified for the source incident"),
            ("verified_synthetic_demo", "verified for the source incident"),
            ("confirmed_within_demo_only", "confirmed for the source incident only"),
        ):
            value = value.replace(old, new)
        if parent and parent.get("role_name") == value and value.startswith("Synthetic "):
            value = value.removeprefix("Synthetic ")
        if parent and parent.get("description") == value and value == "Synthetic demo role identifier only; not a production permission or evidence of a user assignment.":
            value = "Role catalogue reference only; this does not establish a user's current assignment."
        return value
    return value


def _investigation_context(investigation: dict[str, Any]) -> dict[str, Any]:
    """Pass structured findings and relevant KB text, never raw tool logs or credentials."""
    fields = (
        "ticket_understanding", "kb_matches", "evidence", "hypotheses", "information_gaps",
        "evidence_limitations", "diagnostic_steps", "resolution_path", "decision_reason",
        "root_cause_assessment", "scenario_matches", "scenario_evidence", "recommended_troubleshooting_steps",
        "unified_inbox_evidence",
        "recommended_next_steps", "proposed_resolution_steps", "expected_outcomes",
        "verification_steps", "escalation_criteria", "resolution_outcome_status",
        "captured_knowledge_matches", "captured_knowledge_used", "captured_knowledge_error",
    )
    context = {key: investigation.get(key) for key in fields if key in investigation}
    if investigation.get("resolution_path") != "KB_RESOLVABLE":
        for key in ("proposed_resolution_steps", "expected_outcomes", "verification_steps"):
            context.pop(key, None)
    evidence = context.get("evidence")
    if isinstance(evidence, dict):
        # Do not send tool internals, role-validation payloads, or duplicated whole ticket data.
        context["evidence"] = {
            key: evidence[key]
            for key in ("kb_retrievals", "role_assignment_evidence", "limitations", "retrieval_is_diagnosis")
            if key in evidence
        }
        role_check = evidence.get("role_catalogue_validation")
        if isinstance(role_check, dict):
            context["evidence"]["role_catalogue_validation"] = _safe_role_context(role_check)
            ticket = investigation.get("ticket", {})
            if _is_access_related(ticket):
                context["source_system_roles"] = _safe_configured_roles(role_check)
    context["retrieved_knowledge_documents"] = _document_context(investigation)
    captured = context.get("captured_knowledge_matches", [])
    context["captured_knowledge_matches"] = [
        {key: article[key] for key in (
            "article_id", "title", "source_incident", "source_system", "issue_category",
            "error_signature", "symptoms", "confirmed_root_cause", "verified_troubleshooting_steps",
            "required_roles", "verification_method", "review_status", "knowledge_source", "retrieval_match",
        ) if key in article}
        for article in captured if isinstance(article, dict) and article.get("review_status") == "Approved"
    ] if isinstance(captured, list) else []
    return _professional_context(context)


def _safe_role_context(role_check: dict[str, Any]) -> dict[str, Any]:
    """Expose KB-proposed IDs and catalogue labels, never assignment data."""
    def entries(key: str) -> list[dict[str, str]]:
        values = role_check.get(key, [])
        if not isinstance(values, list):
            return []
        allowed = ("role_id", "role_name", "description", "reason")
        return [
            {field: value[field] for field in allowed if isinstance(value.get(field), str)}
            for value in values if isinstance(value, dict)
        ]

    return {
        "source_system": role_check.get("source_system"),
        "catalogue_found": bool(role_check.get("catalogue_found", role_check.get("source_system"))),
        "catalogue_verified_gateway_roles": entries("gateway_roles"),
        "catalogue_verified_backend_roles": entries("backend_roles"),
        "unverified_gateway_role_references": entries("unverified_gateway_roles"),
        "unverified_backend_role_references": entries("unverified_backend_roles"),
        "user_assignments_verified": False,
    }


def _safe_configured_roles(role_check: dict[str, Any]) -> dict[str, Any]:
    """Expose source-system catalogue entries as roles to verify, not assignments."""
    def entries(key: str) -> list[dict[str, str]]:
        values = role_check.get(key, [])
        if not isinstance(values, list):
            return []
        allowed = ("role_id", "role_name", "description")
        return [
            {field: value[field] for field in allowed if isinstance(value.get(field), str)}
            for value in values if isinstance(value, dict)
        ]

    return {
        "source_system": role_check.get("source_system"),
        "catalogue_found": bool(role_check.get("catalogue_found", role_check.get("source_system"))),
        "gateway_roles": entries("configured_gateway_roles"),
        "backend_roles": entries("configured_backend_roles"),
        "role_configuration_available": bool(
            entries("configured_gateway_roles") or entries("configured_backend_roles")
        ),
        "user_assignments_verified": False,
    }


def _is_access_related(ticket: Any) -> bool:
    """Detect access concerns from ticket-provided issue and symptom text."""
    if not isinstance(ticket, dict):
        return False
    fields = (
        "issue", "issue_type", "category", "subcategory", "summary",
        "short_description", "additional_comments", "description",
        "error_message", "test_detail", "test",
    )
    text = " ".join(
        str(ticket.get(key, "")) for key in fields
        if isinstance(ticket.get(key), (str, int, float))
    ).casefold()
    terms = (
        "access", "authori", "permission", "visibility", "not visible", "denied",
        "unauthorized", "forbidden", "role", "privilege",
    )
    return any(term in text for term in terms)


def _historical_context(ticket: dict[str, Any]) -> dict[str, Any]:
    """Retrieve exact-system and issue case context without treating it as current diagnosis."""
    source_system = ticket.get("source_system") or ticket.get("system")
    issue_type = ticket.get("issue_type") or ticket.get("issue")
    if not isinstance(source_system, str) or not isinstance(issue_type, str) or not source_system.strip() or not issue_type.strip():
        return {"search_status": "not_searchable", "matches": [], "limitations": ["The ticket has no source-system and issue pair for a history lookup."]}
    try:
        result = search_historical_cases(source_system, issue_type)
    except (RuntimeError, ValueError):
        return {"search_status": "unavailable", "matches": [], "limitations": ["Local historical case data could not be retrieved."]}

    scenario = next(iter(match_scenarios(ticket)), None)
    ticket_id = ticket.get("incident_number")
    evidence_fields = llm_agent._ticket_search_fields(ticket)
    error_text = evidence_fields.get("error", "")
    signature_match = re.search(r"\bSYNTH-[A-Z0-9-]+\b", error_text, re.IGNORECASE)
    signature = signature_match.group(0) if signature_match else None
    item_references = [scenario.get("required_ticket_reference")] if scenario else []
    if not item_references:
        ticket_text = " ".join(
            str(ticket.get(key, "")) for key in (
                "short_description", "summary", "additional_comments", "description",
                "error_message", "test_detail", "test",
            ) if isinstance(ticket.get(key), str)
        )
        item_references = list(dict.fromkeys(re.findall(r"\b(?:PR|PO|EXP|TR|LV|SD|APR|ATT|DOC|WF)-?\d+\b", ticket_text, re.IGNORECASE)))
    matches = []
    for match in result.get("matches", []) if isinstance(result, dict) else []:
        if not isinstance(match, dict):
            continue
        record = match.get("record", {})
        if match.get("incident_number") == ticket_id:
            continue
        historical_text = " ".join(
            str(record.get(key, "")) for key in ("error_message", "symptoms")
            if isinstance(record, dict)
        )
        if signature and signature.casefold() not in historical_text.casefold():
            continue
        if item_references and not any(reference.casefold() in historical_text.casefold() for reference in item_references if isinstance(reference, str)):
            continue
        root_cause = record.get("root_cause", {}) if isinstance(record, dict) else {}
        outcome = record.get("resolution_outcome", {}) if isinstance(record, dict) else {}
        steps = record.get("troubleshooting_steps", []) if isinstance(record, dict) else []
        matches.append({
            "incident_number": match.get("incident_number"),
            "match_type": match.get("match_type"),
            "case_state": match.get("case_state"),
            "root_cause": {
                "state": root_cause.get("state"),
                "statement": root_cause.get("statement"),
                "scope": root_cause.get("scope"),
            } if isinstance(root_cause, dict) else {},
            "troubleshooting_steps": [
                {key: step[key] for key in ("step_id", "description", "result") if key in step}
                for step in steps if isinstance(step, dict)
            ],
            "resolution_outcome": {
                key: outcome[key] for key in ("state", "description", "verification_evidence") if key in outcome
            } if isinstance(outcome, dict) else {},
        })
    return {
        "search_status": result.get("search_status", "unavailable"),
        "matches": matches,
        "limitations": result.get("limitations", []),
    }


def _response_instructions(
    user_message: str,
    *,
    request_investigation: bool = False,
    resolution_path: str | None = None,
) -> str:
    """Give the model a concise response contract suited to the user's request."""
    question = user_message.casefold()
    if any(phrase in question for phrase in ("troubleshoot", "troubleshooting")):
        intent = (
            "For troubleshooting, use these plain-text labels: Issue: one sentence describing the reported problem; "
            "Likely Root Cause: use root_cause_assessment as the controlling source and preserve its verification state; "
            "never substitute a generic KB cause when a matching scenario is supplied. Required roles: "
            "for access, authorization, or visibility issues, list the configured source-system Gateway and Backend "
            "roles from source_system_roles as roles to verify; if unavailable, say so; Recommended Actions: no more "
            "than three actions from recommended_troubleshooting_steps; Verify: the documented observation. "
            "Keep the answer concise."
        )
    elif request_investigation or any(
        phrase in question
        for phrase in ("analyze", "investigate", "review this incident", "what is happening")
    ):
        intent = (
        "For an investigation, answer in this plain-text format: Issue: one sentence in plain English. "
            "Next steps: up to three prioritized, executable actions. Agent can help: one relevant supported task. "
            "Aim for 60–120 words."
        )
    elif any(phrase in question for phrase in ("find kb", "find the kb", "knowledge base", "kb article", "kb document")):
        intent = (
            "For a knowledge request, name only the best matching KB title, briefly say why it applies or why "
            "the match is only a candidate, and give the first relevant documented troubleshooting action. "
            "Do not reproduce the document or list its internal IDs unless the user asks. If there is no match, say so."
        )
    elif any(phrase in question for phrase in ("next step", "what should", "recommend", "what do i do", "what do we do")):
        intent = (
            "For a next-steps request, return no more than three prioritized, executable actions. State what to "
            "observe and what to do if the observation is unclear, but only where the ticket or KB supports it."
        )
    elif any(phrase in question for phrase in ("root cause", "what caused", "why did", "cause of")):
        intent = (
            "For a cause question, separate confirmed ticket/KB evidence from possible explanations. Label every "
            "explanation as an unverified hypothesis; say plainly when the cause is unknown."
        )
    elif any(phrase in question for phrase in ("missing evidence", "what evidence is missing", "what information", "what is missing", "need from the user")):
        intent = (
            "For a missing-evidence request, identify the most important missing observation and give a concise "
            "question the consultant can ask the reporter to obtain it. Do not invent a diagnostic procedure."
        )
    elif any(phrase in question for phrase in ("work note", "work-note", "draft a note", "write a note")):
        intent = (
            "For a work-note request, draft a concise professional note using only ticket facts and retrieved "
            "investigation evidence. Label proposed actions and unresolved hypotheses accurately. State that it is "
            "a draft and has not been saved."
        )
    elif any(phrase in question for phrase in ("status", "on hold", "resolve the ticket", "close the ticket")):
        intent = (
            "For a status request, explain that the existing UI will validate the requested status and ask for "
            "explicit confirmation before calling the authorized backend. Do not claim the request was staged, "
            "approved, or completed from chat alone."
        )
    elif any(phrase in question for phrase in ("grant access", "grant the user access", "give the user access", "change role", "submit a role", "create an account", "restart", "delete", "clear cache")):
        intent = (
            "This asks for an operation that is not supported by the current prototype. Say briefly that it cannot "
            "perform that operation, do not claim it was done, and offer the closest supported read-only step, "
            "such as investigating the incident or retrieving KB guidance."
        )
    else:
        intent = (
            "For a standard investigation or follow-up, use plain-text labels: Issue: one plain-English sentence; "
            "Next steps: up to three prioritized actions; Agent can help: one relevant supported task. Keep the "
            "answer concise, normally 60–120 words."
        )

    if resolution_path == "HUMAN_SUPPORT":
        path_guidance = (
            "For HUMAN_SUPPORT, lead with the next concrete diagnostic action supported by diagnostic_steps. "
            "Name the exact missing observation from information_gaps and give the reporter question or collection "
            "method only when the ticket or KB supports it. Do not use generic wording such as 'have a human "
            "consultant review the ticket.' Mention escalation only when documented escalation criteria apply; "
            "otherwise state what evidence is needed before deciding."
        )
    elif resolution_path == "KB_RESOLVABLE":
        path_guidance = (
            "For KB_RESOLVABLE, explain the issue in one sentence and give up to three short steps copied or "
            "faithfully summarized from the documented procedure. Present escalation conditions separately. "
            "Call the procedure proposed guidance and say success is unconfirmed until the verification evidence "
            "is reported. Do not repeat demonstration caveats unnecessarily; if asked about validation, state that "
            "scenario guidance has not been confirmed against a live product."
        )
    else:
        path_guidance = (
            "Follow the deterministic resolution_path supplied with the evidence. When it is HUMAN_SUPPORT, "
            "prioritize a concrete diagnostic or exact missing observation over generic review advice."
        )

    return (
        "You are the conversational copilot in an L2 Support workspace. Answer the latest question using "
        "only current incident facts and structured investigation/KB evidence supplied in the conversation. "
        "Treat prior assistant messages and historical records as context, never as authoritative evidence. Re-check "
        "claims against current structured evidence and correct earlier mistakes. Separate reported facts from "
        "unverified hypotheses. Do not invent root causes, procedures, role assignments, completed actions, or "
        "resolution outcomes. A KB retrieval is not proof of diagnosis. Preserve the deterministic resolution_path; "
        "wording cannot change it. Use historical cases only as scoped comparison examples, never as proof of this "
        "ticket's cause or resolution; exclude the current incident from related history, and use historical cases "
        "only when their source, issue, signature, and any available item reference are relevant. A verified historical "
        "outcome applies only to its source incident. "
        "The role catalogue lists configured roles, not a user's assignments. For access, authorization, or "
        "visibility issues, use source_system_roles from the ticket's source-system catalogue lookup as roles "
        "to verify, even when matched KB documents contain no role references. If the catalogue entry or role "
        "configuration is unavailable, say that plainly and do not invent roles. Never say a user is missing a "
        "role without verified assignment evidence. Catalogue entries are not proof that the user lacks a role. "
        "Approved captured articles are separate reusable knowledge from prior consultant-verified outcomes. "
        "When present, cite the article ID and source incident, and summarize its verified cause/resolution as "
        "prior-ticket evidence only. Use them as scoped supporting context and never let them "
        "override current ticket evidence or the deterministic resolution_path. Draft articles are not supplied. "
        "Never update tickets, grant access, submit requests, change workflows, "
        "or claim an action executed. Do not expose internal JSON keys, scenario IDs, operational record IDs, or "
        "developer metadata unless asked. Keep scenario expectations clearly unverified and do not imply live product validation. "
        "Write natural, short sentences. Do not "
        "use Markdown formatting such as headings, bold markers, or bullet characters; plain-text labels and short "
        "numbered steps are allowed.\n\n"
        + path_guidance + "\n\n" + intent
    )


def _deterministic_troubleshooting_reply(ticket: dict[str, Any], investigation: dict[str, Any]) -> str:
    """Render the validated findings when OpenAI is unavailable."""
    understanding = investigation.get("ticket_understanding", {})
    assessment = investigation.get("root_cause_assessment", {})
    issue = understanding.get("issue_type") or ticket.get("issue") or ticket.get("category") or "Reported issue"
    error = understanding.get("error_message_or_code") or ticket.get("test_detail") or ""
    symptom = ticket.get("short_description") or ticket.get("summary") or ticket.get("description") or issue
    lines = [f"Issue: {symptom}" + (f" ({error})" if error and error not in symptom else "")]
    if assessment.get("state") == "scenario_expected_unverified":
        statement = assessment.get("statement") or "No scenario cause is available."
        statement = re.sub(r"^Scenario-based expected cause:\s*", "", statement, flags=re.IGNORECASE)
        lines.append("Expected cause (scenario-based; not independently verified): " + statement)
    else:
        lines.append("Likely Root Cause: " + (assessment.get("statement") or "The available evidence is insufficient to identify a likely cause."))
    role_check = (investigation.get("evidence") or {}).get("role_catalogue_validation", {})
    if _is_access_related(ticket):
        gateway = role_check.get("configured_gateway_roles", [])
        backend = role_check.get("configured_backend_roles", [])
        roles = [f"Gateway: {item['role_id']}" for item in gateway if isinstance(item, dict) and isinstance(item.get("role_id"), str)]
        roles.extend(f"Backend: {item['role_id']}" for item in backend if isinstance(item, dict) and isinstance(item.get("role_id"), str))
        lines.append("Roles to Verify: " + (", ".join(roles) if roles else "Role configuration is unavailable for this source system."))
        lines.append("Role assignments: Not verified; catalogue entries do not establish the user's current access.")
    domain = investigation.get("unified_inbox_evidence", {})
    if isinstance(domain, dict) and issue == "Loading Time Too Slow":
        incident = domain.get("operational_incident")
        if isinstance(incident, dict):
            recorded_state = incident.get("effective_status_at_simulation", "unconfirmed")
            current_state = "unconfirmed" if recorded_state == "status_unconfirmed" else str(recorded_state).replace("_", " ")
            incident_reference = incident.get("operational_incident_id")
            reference_text = f" {incident_reference}" if isinstance(incident_reference, str) and incident_reference else ""
            lines.append(
                f"Related system incident{reference_text}: "
                f"{str(incident.get('incident_type', '')).replace('_', ' ').title()} · "
                f"recorded status: {incident.get('incident_status_as_recorded')}; "
                f"status at the recorded reference time: {current_state} "
                f"({incident.get('simulation_timestamp')}). "
                f"Start: {incident.get('scheduled_start_at')}; expected recovery: {incident.get('expected_recovery_at')} "
                f"(not confirmed); actual resolution: {incident.get('actual_resolution_at') or 'not recorded'}. "
                f"{incident.get('status_interpretation')}"
            )
    if isinstance(domain, dict) and issue == "Cannot Set Up Substitution":
        capability = domain.get("workflow_capability")
        if isinstance(capability, dict):
            lines.append(f"Workflow capability reference: {capability.get('workflow_name')} — substitution supported: {capability.get('supports_substitution')} (not independently verified against the live system).")
    if isinstance(domain, dict) and issue == "Cannot See Specific Language":
        languages = domain.get("supported_task_content_languages", [])
        lines.append("Supported task-content languages: " + (", ".join(languages) if languages else "Unavailable for this source system."))
    steps = investigation.get("recommended_troubleshooting_steps") or investigation.get("diagnostic_steps") or []
    rendered_steps = [step.get("instruction") for step in steps[:3] if isinstance(step, dict) and step.get("instruction")]
    if rendered_steps:
        lines.append("Recommended Actions:\n" + "\n".join(f"{index}. {step}" for index, step in enumerate(rendered_steps, 1)))
    elif investigation.get("information_gaps"):
        lines.append("Next evidence to collect: " + "; ".join(str(item) for item in investigation["information_gaps"][:2]))
    verification = investigation.get("verification_steps") or []
    if verification and isinstance(verification[0], dict) and verification[0].get("instruction"):
        lines.append("Verify: " + verification[0]["instruction"])
    for article in investigation.get("captured_knowledge_used", []):
        if not isinstance(article, dict):
            continue
        cause = article.get("source_verified_cause") or "not recorded"
        steps = article.get("source_verified_resolution") or []
        summary = "; ".join(step for step in steps if isinstance(step, str)) or "no verified action recorded"
        lines.append(
            f"Based on approved knowledge: {article.get('article_id')} · {article.get('source_incident')}. "
            f"Source-ticket verified cause: {cause}. Source-ticket resolution: {summary}. "
            "This does not verify the current user's role assignments or establish the current ticket's cause/outcome."
        )
    return "\n\n".join(lines)


def chat_with_incident(
    incident_number: str,
    user_message: str,
    conversation_history: list[dict[str, str]] | None = None,
    investigation: dict[str, Any] | None = None,
    *,
    request_investigation: bool = False,
) -> dict[str, Any]:
    """Answer a follow-up using real Responses API output grounded in current incident evidence.

    No tools are exposed to the conversational model. Ticket updates and access/workflow
    actions therefore remain outside this endpoint and continue through existing controls.
    """
    if not isinstance(incident_number, str) or not incident_number.strip():
        return {"reply": "", "error": "Select a valid incident before asking the copilot."}
    if not isinstance(user_message, str) or not user_message.strip():
        return {"reply": "", "error": "Enter a message for the copilot."}
    if len(user_message) > MAX_MESSAGE_LENGTH:
        return {"reply": "", "error": f"Messages must be {MAX_MESSAGE_LENGTH} characters or fewer."}
    if conversation_history is None:
        conversation_history = []
    if not isinstance(conversation_history, list):
        return {"reply": "", "error": "Chat history is invalid. Start a new message and retry."}

    history: list[dict[str, str]] = []
    for item in conversation_history[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"} or not isinstance(item.get("content"), str):
            return {"reply": "", "error": "Chat history is invalid. Start a new message and retry."}
        history.append({"role": item["role"], "content": item["content"][-MAX_MESSAGE_LENGTH:]})

    try:
        ticket = llm_agent.get_ticket(incident_number.strip().upper())
    except (RuntimeError, llm_agent.ToolInputError) as exc:
        return {"reply": "", "error": str(exc)}

    result = investigation
    result_ticket = result.get("ticket") if isinstance(result, dict) else None
    if (request_investigation or not isinstance(result, dict)
            or not isinstance(result_ticket, dict)
            or result_ticket.get("incident_number") != ticket.get("incident_number")):
        result = llm_agent.run_investigation(ticket["incident_number"], use_llm=False)
    if not isinstance(result, dict) or result.get("error") and not result.get("ticket"):
        return {
            "reply": "",
            "error": (result.get("error") if isinstance(result, dict) else None)
            or "The incident investigation could not provide context. Retry after checking the ticket service.",
            "investigation": result,
        }

    context = {
        "ticket_facts": _ticket_context(ticket),
        "investigation_findings": _investigation_context(result),
        "historical_case_context": _professional_context(_historical_context(ticket)),
    }
    instructions = _response_instructions(
        user_message,
        request_investigation=request_investigation,
        resolution_path=result.get("resolution_path"),
    )
    prompt = (
        "Current incident evidence (JSON):\n" + json.dumps(context, ensure_ascii=False)
        + "\n\nLatest user question:\n" + user_message.strip()
    )
    if len(prompt.encode("utf-8")) > 64 * 1024:
        return {"reply": "", "error": "The ticket context is too large for a safe AI request. Narrow the investigation and retry.",
                "investigation": result, "ai_provenance": "unavailable"}
    messages = [*history, {"role": "user", "content": prompt}]
    if len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) > 96 * 1024:
        return {"reply": "", "error": "The conversation is too large for a safe AI request. Start a new message.",
                "investigation": result, "ai_provenance": "unavailable"}
    try:
        api = llm_agent._get_client()
    except RuntimeError as exc:
        message = str(exc)
        is_troubleshooting = "troubleshoot" in user_message.casefold() or "troubleshooting" in user_message.casefold()
        if is_troubleshooting:
            return {"reply": _deterministic_troubleshooting_reply(ticket, result), "error": message,
                    "investigation": result, "ai_provenance": "deterministic_fallback"}
        return {"reply": "", "error": message, "investigation": result, "ai_provenance": "unavailable"}
    try:
        response = api.responses.create(
            model=llm_agent.MODEL,
            instructions=instructions,
            input=messages,
            max_output_tokens=llm_agent.MAX_OUTPUT_TOKENS,
        )
        reply = llm_agent._item_value(response, "output_text", "")
        if not isinstance(reply, str) or not reply.strip():
            fallback = _deterministic_troubleshooting_reply(ticket, result) if "troubleshoot" in user_message.casefold() or "troubleshooting" in user_message.casefold() else ""
            return {"reply": fallback, "error": "The AI copilot returned an empty response. Retry your question.",
                    "investigation": result, "ai_provenance": "deterministic_fallback" if fallback else "unavailable"}
        return {"reply": reply.strip(), "error": None, "investigation": result,
                "ai_provenance": "live_gpt", "model": llm_agent.MODEL}
    except Exception as exc:
        fallback = _deterministic_troubleshooting_reply(ticket, result) if "troubleshoot" in user_message.casefold() or "troubleshooting" in user_message.casefold() else ""
        return {
            "reply": fallback,
            "error": llm_agent._safe_openai_error(exc),
            "investigation": result,
            "ai_provenance": "deterministic_fallback" if fallback else "unavailable",
        }
