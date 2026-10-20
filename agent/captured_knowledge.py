"""Read-only retrieval of human-approved captured knowledge articles."""

from __future__ import annotations

import re

import requests

from agent import internal_api
from agent.knowledge_governance import is_active


def search_approved_articles(ticket: dict) -> tuple[list[dict], str | None]:
    source_system = ticket.get("source_system") or ticket.get("system") or ""
    # Captured articles store the ticket's ServiceNow category as
    # issue_category. Prefer the same field when present; use the normalized
    # issue type aliases for ticket fixtures that only expose those fields.
    issue_category = ticket.get("category") or ticket.get("issue_type") or ticket.get("issue") or ""
    query = " ".join(str(ticket.get(key, "")) for key in (
        "short_description", "additional_comments", "test_detail", "description", "summary",
    ) if isinstance(ticket.get(key), str))
    try:
        response = internal_api.get(
            "/api/knowledge-articles/search",
            params={"source_system": source_system, "issue_category": issue_category, "q": query},
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout:
        return [], "Approved captured knowledge search timed out."
    except requests.RequestException:
        return [], "Approved captured knowledge is unavailable from the ticket service."
    except ValueError:
        return [], "The ticket service returned invalid captured knowledge data."
    except Exception:
        return [], "Approved captured knowledge could not be retrieved from the ticket service."
    articles = payload.get("articles") if isinstance(payload, dict) else None
    if not isinstance(articles, list):
        return [], "The ticket service returned an unexpected captured knowledge response."

    def normalise(value: object) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).split())

    query_terms = set(normalise(query).split()) - {"the", "a", "an", "in", "to", "my", "is", "of", "and", "cannot", "can", "not"}
    matches = []
    for article in articles:
        if not isinstance(article, dict) or article.get("review_status") != "Approved" or not is_active(article):
            continue
        if normalise(article.get("source_system")) != normalise(source_system):
            continue
        if normalise(article.get("issue_category")) != normalise(issue_category):
            continue
        article_text = " ".join(str(article.get(key, "")) for key in (
            "title", "issue_category", "error_signature", "symptoms", "confirmed_root_cause",
        ))
        matched_terms = sorted(query_terms.intersection(normalise(article_text).split()))
        # The Mock API owns symptom matching (it searches the article's title,
        # signature, symptoms and verified cause). Do not apply a second,
        # narrower overlap test here: it can discard valid API matches when
        # the symptom is represented by a signature or a short phrase.
        matches.append({
            **{key: value for key, value in article.items() if key != "version_history"},
            "retrieval_match": {
                "source_system": "exact",
                "issue_category": "exact",
                "symptom_terms": matched_terms,
            },
        })
    return matches, None
