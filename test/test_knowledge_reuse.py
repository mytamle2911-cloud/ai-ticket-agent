import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from agent import captured_knowledge, llm_agent
from mock_api import server


ROOT = Path(__file__).resolve().parents[1]


class LocalApiResponse:
    def __init__(self, response):
        self.response = response
        self.status_code = response.status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self.response.get_json()


class KnowledgeReuseTests(unittest.TestCase):
    def setUp(self):
        self.articles = json.loads((ROOT / "data/knowledge_articles.json").read_text())["articles"]
        self.article = next(
            article for article in self.articles
            if article.get("source_incident") == "INC100006"
            and article.get("source_system") == "SAP SD"
            and article.get("issue_category") == "Task Not Visible"
            and article.get("review_status") == "Approved"
        )
        self.ticket = {
            "incident_number": "INC_TEST_001",
            "source_system": "SAP SD",
            "issue_type": "Task Not Visible",
            "short_description": "My manager cannot see a sales document approval task in Unified Inbox.",
            "description": "My manager cannot see a sales document approval task in Unified Inbox.",
            "additional_comments": "My manager cannot see a sales document approval task in Unified Inbox.",
            "error_message": "N/A — No error displayed.",
            "status": "In Progress",
            "priority": "P3",
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self.temp_dir.name, "knowledge_articles.json")
        draft = copy.deepcopy(self.article)
        draft.update(article_id="KBA-DRAFT-ONLY", review_status="Draft")
        pm7 = copy.deepcopy(self.article)
        pm7.update(article_id="KBA-PM7-UNRELATED", source_system="PM7")
        hr = copy.deepcopy(self.article)
        hr.update(article_id="KBA-HR-UNRELATED", source_system="HR ESS")
        Path(self.store_path).write_text(json.dumps({"articles": [self.article, draft, pm7, hr]}))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _local_get(self, client, url, *, params, timeout):
        del timeout
        from urllib.parse import urlparse
        response = client.get(urlparse(url).path, query_string=params)
        return LocalApiResponse(response)

    def test_approved_sap_sd_article_is_retrieved_and_used_for_isolated_ticket(self):
        self.assertEqual(self.article["article_id"], "KBA-B8AD47ABD2A6")
        self.assertEqual(self.article["confirmed_root_cause"], "Missing SAP SD Backend Role — XX:BACKEND_SD:XX")

        with patch.object(server, "KNOWLEDGE_ARTICLES_FILE", self.store_path):
            client = server.app.test_client()
            query = self.ticket["description"] + " " + self.ticket["error_message"]
            direct = client.get("/api/knowledge-articles/search", query_string={
                "source_system": "SAP SD", "issue_category": "Task Not Visible", "q": query,
            }).get_json()["articles"]
            self.assertEqual([item["article_id"] for item in direct], ["KBA-B8AD47ABD2A6"])
            self.assertNotIn("KBA-PM7-UNRELATED", [item["article_id"] for item in direct])
            self.assertNotIn("KBA-HR-UNRELATED", [item["article_id"] for item in direct])
            self.assertNotIn("KBA-DRAFT-ONLY", [item["article_id"] for item in direct])

            fake_client = SimpleNamespace(responses=SimpleNamespace(
                create=Mock(return_value=SimpleNamespace(
                    output=[], output_text="Prior approved SAP SD knowledge is relevant supporting evidence.",
                )),
            ))
            with patch.object(llm_agent, "_validate_incident_number", side_effect=lambda value: value), \
                 patch.object(llm_agent, "get_ticket", return_value=self.ticket), \
                 patch.object(llm_agent, "_get_client", return_value=fake_client), \
                 patch.object(captured_knowledge.requests, "get", side_effect=lambda url, **kwargs: self._local_get(client, url, **kwargs)):
                result = llm_agent.run_investigation("INC_TEST_001", use_llm=True)

        self.assertIsNone(result["error"])
        self.assertEqual(result["resolution_path"], "HUMAN_SUPPORT")
        self.assertEqual(result["scenario_matches"], [])
        self.assertEqual([item["article_id"] for item in result["captured_knowledge_matches"]], ["KBA-B8AD47ABD2A6"])
        self.assertEqual(result["captured_knowledge_used"][0]["source_incident"], "INC100006")
        self.assertEqual(result["captured_knowledge_used"][0]["source_verified_cause"], self.article["confirmed_root_cause"])
        self.assertEqual(result["captured_knowledge_used"][0]["source_verified_resolution"], self.article["verified_troubleshooting_steps"])
        self.assertEqual(result["captured_knowledge_used"][0]["matching_criteria"]["source_system"], "exact")
        self.assertEqual(result["captured_knowledge_used"][0]["matching_criteria"]["issue_category"], "exact")
        self.assertTrue(result["captured_knowledge_used"][0]["matching_criteria"]["symptom_terms"])
        self.assertIn("Based on approved knowledge: KBA-B8AD47ABD2A6 · INC100006", result["final_analysis"])
        self.assertIn(self.article["confirmed_root_cause"], result["final_analysis"])
        self.assertIn(self.article["verified_troubleshooting_steps"][0], result["final_analysis"])
        self.assertIn("user's role assignments", result["final_analysis"])
        self.assertIn("does not establish a user's access", result["evidence"]["role_assignment_evidence"])

        model_context = fake_client.responses.create.call_args.kwargs["input"][0]["content"]
        self.assertIn("KBA-B8AD47ABD2A6", model_context)
        self.assertIn(self.article["confirmed_root_cause"], model_context)
        self.assertIn(self.article["verified_troubleshooting_steps"][0], model_context)

    def test_approved_article_search_requires_source_issue_and_symptom_overlap(self):
        with patch.object(server, "KNOWLEDGE_ARTICLES_FILE", self.store_path):
            client = server.app.test_client()
            with patch.object(captured_knowledge.requests, "get", side_effect=lambda url, **kwargs: self._local_get(client, url, **kwargs)):
                matches, error = captured_knowledge.search_approved_articles(self.ticket)
                unrelated_ticket = dict(self.ticket, source_system="Workday")
                unrelated, unrelated_error = captured_knowledge.search_approved_articles(unrelated_ticket)
                symptom_miss = dict(
                    self.ticket,
                    short_description="Keyboard has stopped working",
                    description="Keyboard has stopped working",
                    additional_comments="Keyboard has stopped working",
                )
                no_symptom_match, symptom_error = captured_knowledge.search_approved_articles(symptom_miss)

        self.assertIsNone(error)
        self.assertEqual([item["article_id"] for item in matches], ["KBA-B8AD47ABD2A6"])
        self.assertIsNone(unrelated_error)
        self.assertEqual(unrelated, [])
        self.assertIsNone(symptom_error)
        self.assertEqual(no_symptom_match, [])


if __name__ == "__main__":
    unittest.main()
