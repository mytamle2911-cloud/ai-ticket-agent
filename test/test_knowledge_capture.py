"""Knowledge capture is grounded in persisted consultant verification and human approval."""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import captured_knowledge, knowledge_capture
from agent import llm_agent
from mock_api import server


class FlaskResponse:
    def __init__(self, response):
        self.response = response
        self.status_code = response.status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self.response)

    def json(self):
        return self.response.get_json()


class FakeResponses:
    def __init__(self, text="{\"title\": \"Reviewed title\", \"symptoms\": \"Concise symptoms.\"}", error=None):
        self.create = Mock(side_effect=error or [SimpleNamespace(output_text=text)])


class KnowledgeCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.resolution_path = os.path.join(self.temp.name, "resolutions.json")
        self.articles_path = os.path.join(self.temp.name, "articles.json")
        with open(os.path.join(os.path.dirname(__file__), "..", "data", "tickets.json"), encoding="utf-8") as file:
            tickets = json.load(file)["tickets"]
        future_ticket = next(ticket for ticket in tickets if ticket["incident_number"] == "INC100002")
        self.mock_tickets = {"tickets": [{
            "incident_number": "INC100001", "source_system": "SAP MM", "system": "SAP MM",
            "category": "Approval Action", "subcategory": "Workflow",
            "short_description": "Approval action unavailable", "additional_comments": "SYNTH-APR-001",
            "linked_kb_document_ids": [],
        }, future_ticket]}
        self.patches = [
            patch.object(server, "RESOLUTIONS_FILE", self.resolution_path),
            patch.object(server, "KNOWLEDGE_ARTICLES_FILE", self.articles_path),
            patch.object(server, "load_tickets", return_value=self.mock_tickets),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.temp.cleanup)
        self.flask_client = server.app.test_client()
        self.resolution = self.flask_client.post("/api/tickets/INC100001/resolutions", json={
            "confirmed_root_cause": "Consultant-confirmed synthetic cause.",
            "actions_performed": ["Compared the reported state.", "Retested the approval action."],
            "verification_evidence": "The reporter completed the approval action during retest.",
            "consultant_id": "SYNTH-CONS-01", "consultant_confirmed": True,
        }).get_json()["resolution"]

    def mock_get(self, url, *args, **kwargs):
        path = url.replace(knowledge_capture.BASE_URL, "")
        return FlaskResponse(self.flask_client.get(path, query_string=kwargs.get("params")))

    def mock_post(self, url, *args, **kwargs):
        path = url.replace(knowledge_capture.BASE_URL, "")
        return FlaskResponse(self.flask_client.post(path, json=kwargs.get("json")))

    def test_generated_draft_uses_verified_resolution_and_persists(self):
        fake = FakeResponses()
        with patch.object(knowledge_capture.requests, "get", side_effect=self.mock_get), patch.object(
            knowledge_capture.requests, "post", side_effect=self.mock_post,
        ):
            article = knowledge_capture.generate_knowledge_draft(
                "INC100001", self.resolution["resolution_id"], client=SimpleNamespace(responses=fake),
            )
        self.assertEqual(article["review_status"], "Draft")
        self.assertEqual(article["confirmed_root_cause"], self.resolution["confirmed_root_cause"])
        self.assertEqual(article["verified_troubleshooting_steps"], self.resolution["actions_performed"])
        self.assertEqual(article["verification_method"], self.resolution["verification_evidence"])
        self.assertEqual(article["created_by"], "SYNTH-CONS-01")
        self.assertEqual(article["source_incident"], "INC100001")
        self.assertTrue(article["article_id"])
        self.assertTrue(article["created_at"])
        self.assertEqual(fake.create.call_count, 1)
        prompt = json.loads(fake.create.call_args.kwargs["input"])
        self.assertEqual(prompt["verified_resolution"]["confirmed_root_cause"], self.resolution["confirmed_root_cause"])
        with open(self.articles_path, encoding="utf-8") as file:
            self.assertEqual(json.load(file)["articles"][0]["article_id"], article["article_id"])

    def test_missing_or_unverified_resolution_is_rejected_before_openai(self):
        fake = FakeResponses()
        with patch.object(knowledge_capture.requests, "get", side_effect=self.mock_get), patch.object(
            knowledge_capture.requests, "post", side_effect=self.mock_post,
        ), self.assertRaisesRegex(knowledge_capture.KnowledgeCaptureError, "No persisted verified resolution"):
            knowledge_capture.generate_knowledge_draft("INC100001", "RES-OTHER", client=SimpleNamespace(responses=fake))
        fake.create.assert_not_called()
        self.assertFalse(os.path.exists(self.articles_path))

    def test_openai_failure_is_reported_and_draft_is_not_saved(self):
        fake = FakeResponses(error=RuntimeError("private request detail"))
        with patch.object(knowledge_capture.requests, "get", side_effect=self.mock_get), patch.object(
            knowledge_capture.requests, "post", side_effect=self.mock_post,
        ), self.assertRaises(knowledge_capture.KnowledgeCaptureError) as raised:
            knowledge_capture.generate_knowledge_draft("INC100001", self.resolution["resolution_id"], client=SimpleNamespace(responses=fake))
        self.assertIn("OpenAI knowledge draft generation request failed", str(raised.exception))
        self.assertNotIn("private request detail", str(raised.exception))
        self.assertFalse(os.path.exists(self.articles_path))

    def test_missing_openai_configuration_is_displayable_and_saves_no_draft(self):
        with patch.object(knowledge_capture.requests, "get", side_effect=self.mock_get), patch.object(
            knowledge_capture.llm_agent, "_get_client", side_effect=RuntimeError(
                "OpenAI API key is missing. Set OPENAI_API_KEY in your .env file."
            ),
        ), self.assertRaisesRegex(knowledge_capture.KnowledgeCaptureError, "OPENAI_API_KEY"):
            knowledge_capture.generate_knowledge_draft("INC100001", self.resolution["resolution_id"])
        self.assertFalse(os.path.exists(self.articles_path))

    def test_duplicate_generation_returns_existing_article_without_second_ai_call(self):
        fake = FakeResponses()
        with patch.object(knowledge_capture.requests, "get", side_effect=self.mock_get), patch.object(
            knowledge_capture.requests, "post", side_effect=self.mock_post,
        ) as post:
            first = knowledge_capture.generate_knowledge_draft("INC100001", self.resolution["resolution_id"], client=SimpleNamespace(responses=fake))
            second = knowledge_capture.generate_knowledge_draft("INC100001", self.resolution["resolution_id"], client=SimpleNamespace(responses=fake))
        self.assertEqual(first["article_id"], second["article_id"])
        fake.create.assert_called_once()
        post.assert_called_once()

    def test_investigation_retrieval_excludes_draft_then_finds_approved_article(self):
        fake = FakeResponses('{"title":"Approval Task Visibility Guidance","symptoms":"A purchase order approval task cannot be found in SAP MM."}')
        with patch.object(knowledge_capture.requests, "get", side_effect=self.mock_get), patch.object(
            knowledge_capture.requests, "post", side_effect=self.mock_post,
        ):
            article = knowledge_capture.generate_knowledge_draft(
                "INC100001", self.resolution["resolution_id"], client=SimpleNamespace(responses=fake),
            )
        with patch.object(llm_agent.requests, "get", side_effect=self.mock_get):
            before = llm_agent.run_investigation("INC100002", use_llm=False)
        self.assertEqual(before["ticket"]["incident_number"], "INC100002")
        self.assertTrue(before["ticket"].get("description"))
        self.assertTrue(before["ticket"].get("test_detail"))
        self.assertEqual(before["ticket"].get("source_system"), "SAP MM")
        self.assertTrue(before["evidence"]["role_catalogue_validation"]["configured_gateway_roles"])
        self.assertEqual(before["captured_knowledge_matches"], [])
        self.assertEqual(before["resolution_path"], "HUMAN_SUPPORT")
        publish = self.flask_client.patch(f"/api/knowledge-articles/{article['article_id']}", json={
            "approve": True, "approved_by": "SYNTH-REVIEWER-1",
        })
        self.assertEqual(publish.status_code, 200)
        with patch.object(llm_agent.requests, "get", side_effect=self.mock_get):
            after = llm_agent.run_investigation("INC100002", use_llm=False)
        self.assertEqual([item["article_id"] for item in after["captured_knowledge_matches"]], [article["article_id"]])
        self.assertEqual(after["resolution_path"], before["resolution_path"])
        self.assertEqual(after["ticket"]["status"], before["ticket"]["status"])


if __name__ == "__main__":
    unittest.main()
