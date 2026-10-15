"""AI assisted drafting from consultant-verified resolution records."""

from __future__ import annotations

import json
import re
from typing import Any

import requests

from agent import llm_agent

BASE_URL = "http://127.0.0.1:5000"


class KnowledgeCaptureError(RuntimeError):
    """A safe, user-displayable knowledge capture failure."""


def _get_json(path: str) -> dict[str, Any]:
    try:
        response = requests.get(f"{BASE_URL}{path}", timeout=10)
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout:
        raise KnowledgeCaptureError("The ticket service timed out while loading verified resolution data.") from None
    except requests.RequestException:
        raise KnowledgeCaptureError("Could not reach the ticket service. Start it and retry knowledge capture.") from None
    except ValueError:
        raise KnowledgeCaptureError("The ticket service returned invalid data while loading knowledge capture information.") from None
    if not isinstance(payload, dict):
        raise KnowledgeCaptureError("The ticket service returned an unexpected knowledge capture response.")
    return payload


def _article_seed(ticket: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
    """Build model input solely from ticket metadata and the verified resolution."""
    issue_text = " ".join(str(ticket.get(key, "")) for key in ("category", "subcategory", "short_description", "additional_comments")).lower()
    include_roles = any(term in issue_text for term in ("access", "authorization", "authoriz", "permission", "role", "visibility"))
    kb_context = []
    linked_ids = set(ticket.get("linked_kb_document_ids", []))
    try:
        with open(llm_agent.get_data_path("product_docs.json"), "r", encoding="utf-8") as file:
            docs = json.load(file).get("documents", [])
        for document in docs:
            if isinstance(document, dict) and document.get("document_id") in linked_ids:
                kb_context.append({key: document[key] for key in ("document_id", "title", "issue_type", "symptoms") if key in document})
    except (OSError, json.JSONDecodeError, AttributeError):
        # The verified outcome remains sufficient to draft; absent context is not fabricated.
        kb_context = []
    return {
        "ticket": {key: ticket.get(key) for key in (
            "incident_number", "source_system", "system", "category", "subcategory",
            "short_description", "additional_comments", "test_detail", "description",
        ) if ticket.get(key) is not None},
        "verified_resolution": {
            "resolution_id": resolution.get("resolution_id"),
            "confirmed_root_cause": resolution.get("confirmed_root_cause"),
            "actions_performed": resolution.get("actions_performed"),
            "verification_evidence": resolution.get("verification_evidence"),
            "consultant_id": resolution.get("consultant_id"),
            "verified": resolution.get("verified"),
        },
        "relevant_original_kb_context": kb_context,
        "role_context_relevant": include_roles,
    }


def _model_draft(seed: dict[str, Any], client: Any = None) -> dict[str, str]:
    try:
        api = client if client is not None else llm_agent._get_client()
    except RuntimeError as exc:
        message = str(exc)
        if "OPENAI_API_KEY" in message:
            raise KnowledgeCaptureError(message) from None
        raise KnowledgeCaptureError("OpenAI client configuration is unavailable. Check the app configuration and retry.") from None
    instructions = (
        "Write only a reusable article title and a concise symptoms description. Return one JSON object "
        'with exactly the string fields "title" and "symptoms". Use only the supplied ticket and verified '
        "resolution facts. Do not add diagnosis, actions, roles, procedures, causes, or outcomes. The verified "
        "resolution is authoritative. Original KB context is only descriptive context and never evidence that its "
        "steps were performed. If an aspect is not stated, omit it."
    )
    try:
        response = api.responses.create(
            model=llm_agent.MODEL,
            instructions=instructions,
            input=json.dumps(seed, ensure_ascii=False),
        )
    except Exception as exc:
        raise KnowledgeCaptureError(llm_agent._safe_openai_error(exc).replace("investigation", "knowledge draft generation")) from None
    output_text = llm_agent._item_value(response, "output_text", "")
    if not isinstance(output_text, str) or not output_text.strip():
        raise KnowledgeCaptureError("OpenAI returned an empty knowledge draft. No article was saved.")
    try:
        content = json.loads(output_text)
    except json.JSONDecodeError:
        # Accept a fenced JSON object only, not prose with embedded claims.
        match = re.fullmatch(r"\s*```(?:json)?\s*(\{.*\})\s*```\s*", output_text, re.DOTALL | re.IGNORECASE)
        if not match:
            raise KnowledgeCaptureError("OpenAI returned a malformed knowledge draft. No article was saved.") from None
        try:
            content = json.loads(match.group(1))
        except json.JSONDecodeError:
            raise KnowledgeCaptureError("OpenAI returned a malformed knowledge draft. No article was saved.") from None
    if (not isinstance(content, dict) or set(content) != {"title", "symptoms"}
            or not all(isinstance(content.get(key), str) and content[key].strip() for key in ("title", "symptoms"))):
        raise KnowledgeCaptureError("OpenAI returned incomplete article content. No article was saved.")
    return {key: content[key].strip() for key in ("title", "symptoms")}


def generate_article_content(ticket: dict[str, Any], resolution: dict[str, Any], *, client: Any = None) -> dict[str, str]:
    """Generate article wording from already-loaded ticket and verified resolution facts."""
    if not isinstance(resolution, dict) or resolution.get("verified") is not True:
        raise KnowledgeCaptureError("A persisted verified resolution is required before generating an article.")
    if (not isinstance(resolution.get("confirmed_root_cause"), str) or not resolution["confirmed_root_cause"].strip()
            or not isinstance(resolution.get("actions_performed"), list) or not resolution["actions_performed"]
            or not all(isinstance(action, str) and action.strip() for action in resolution["actions_performed"])
            or not isinstance(resolution.get("verification_evidence"), str) or not resolution["verification_evidence"].strip()):
        raise KnowledgeCaptureError("The verified resolution record is incomplete. No article was generated.")
    if not isinstance(ticket, dict):
        raise KnowledgeCaptureError("The selected incident could not be loaded. No article was generated.")
    return _model_draft(_article_seed(ticket, resolution), client=client)


def generate_knowledge_draft(incident_number: str, resolution_id: str, *, client: Any = None) -> dict[str, Any]:
    """Generate then persist one Draft based on a verified resolution for this incident."""
    if not isinstance(incident_number, str) or not re.fullmatch(r"INC\d{6}", incident_number.strip(), re.IGNORECASE):
        raise KnowledgeCaptureError("Enter a valid incident number before generating an article.")
    if not isinstance(resolution_id, str) or not resolution_id.strip():
        raise KnowledgeCaptureError("Select a persisted verified resolution before generating an article.")
    incident_number = incident_number.strip().upper()
    existing_payload = _get_json(f"/api/tickets/{incident_number}/knowledge-articles")
    existing = existing_payload.get("articles", [])
    match = next((article for article in existing if isinstance(article, dict)
                  and article.get("source_resolution_id") == resolution_id), None)
    if match:
        return match

    resolution_payload = _get_json(f"/api/tickets/{incident_number}/resolutions")
    resolutions = resolution_payload.get("resolutions", [])
    resolution = next((record for record in resolutions if isinstance(record, dict)
                       and record.get("resolution_id") == resolution_id
                       and record.get("incident_number") == incident_number
                       and record.get("verified") is True), None)
    if not resolution:
        raise KnowledgeCaptureError("No persisted verified resolution exists for this incident and selection.")
    if (not isinstance(resolution.get("confirmed_root_cause"), str) or not resolution["confirmed_root_cause"].strip()
            or not isinstance(resolution.get("actions_performed"), list) or not resolution["actions_performed"]
            or not all(isinstance(action, str) and action.strip() for action in resolution["actions_performed"])
            or not isinstance(resolution.get("verification_evidence"), str) or not resolution["verification_evidence"].strip()):
        raise KnowledgeCaptureError("The verified resolution record is incomplete. No article was generated.")
    ticket_payload = _get_json(f"/api/tickets/{incident_number}")
    ticket = ticket_payload.get("ticket", ticket_payload)
    if not isinstance(ticket, dict) or ticket.get("incident_number") != incident_number:
        raise KnowledgeCaptureError("The ticket service did not return the selected incident. No article was generated.")
    draft_content = generate_article_content(ticket, resolution, client=client)
    try:
        response = requests.post(
            f"{BASE_URL}/api/tickets/{incident_number}/knowledge-articles",
            json={"source_resolution_id": resolution_id, **draft_content}, timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout:
        raise KnowledgeCaptureError("The ticket service timed out while saving the draft. Check the article list before retrying.") from None
    except requests.RequestException:
        raise KnowledgeCaptureError("The ticket service could not save the generated draft. Check the service and retry.") from None
    except ValueError:
        raise KnowledgeCaptureError("The ticket service returned invalid data while saving the draft.") from None
    article = payload.get("article") if isinstance(payload, dict) else None
    if not isinstance(article, dict) or article.get("source_resolution_id") != resolution_id or article.get("review_status") != "Draft":
        raise KnowledgeCaptureError("The ticket service did not confirm that the article draft was saved.")
    return article
