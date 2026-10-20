import json
import os
import unittest
from unittest.mock import patch

from agent import demo_state, llm_agent
from mock_api import server


class BrowserDemoStateTests(unittest.TestCase):
    def setUp(self):
        self.secret = patch.dict(os.environ, {
            "DEMO_STATE_SECRET": "unit-test-secret-value-longer-than-32-characters",
        })
        self.secret.start()
        self.addCleanup(self.secret.stop)
        self.client = server.app.test_client()
        with open(server.TICKETS_FILE, encoding="utf-8") as file:
            self.original_tickets = json.load(file)
        self.seed_resolution_count = len(demo_state.initial_state()["verified_resolutions"])
        self.seed_article_count = len(demo_state.initial_state()["articles"])

    def dispatch(self, path, method="GET", payload=None, state=None, reset=False):
        envelope = {"path": path, "method": method}
        if payload is not None:
            envelope["payload"] = payload
        if state is not None:
            envelope["state"] = state
        if reset:
            envelope["reset"] = True
        response = self.client.post("/api/demo/dispatch", json=envelope)
        return response, response.get_json() or {}

    def test_status_update_persists_across_independent_requests_without_seed_write(self):
        _, first = self.dispatch("/api/tickets")
        self.assertEqual(first["status_code"], 200)
        token = first["demo_state"]
        _, changed = self.dispatch(
            "/api/tickets/INC100001", "PATCH",
            {"status": "In Progress", "expected_current_status": "Resolved", "confirmed": True},
            token,
        )
        self.assertEqual(changed["status_code"], 200)
        new_token = changed["demo_state"]

        # A new client instance represents a page refresh. State survives because
        # the browser resends its localStorage token, not because of process memory.
        refreshed_client = server.app.test_client()
        refreshed = refreshed_client.post("/api/demo/dispatch", json={
            "path": "/api/tickets/INC100001", "method": "GET", "state": new_token,
        }).get_json()
        self.assertEqual(refreshed["result"]["status"], "In Progress")
        self.assertEqual(refreshed["result"]["short_description"], "PM7 — Task Not Visible")
        with open(server.TICKETS_FILE, encoding="utf-8") as file:
            current_seed = json.load(file)
        self.assertEqual(current_seed, self.original_tickets)

    def test_two_browser_snapshots_are_isolated(self):
        _, browser_a = self.dispatch("/api/tickets")
        _, browser_b = self.dispatch("/api/tickets")
        _, updated_a = self.dispatch(
            "/api/tickets/INC100001", "PATCH",
            {"status": "In Progress", "expected_current_status": "Resolved", "confirmed": True},
            browser_a["demo_state"],
        )
        _, read_a = self.dispatch("/api/tickets/INC100001", state=updated_a["demo_state"])
        _, read_b = self.dispatch("/api/tickets/INC100001", state=browser_b["demo_state"])
        self.assertEqual(read_a["result"]["status"], "In Progress")
        self.assertEqual(read_b["result"]["status"], "Resolved")

    def test_vercel_storage_helpers_never_write_mutable_json_without_request_state(self):
        with patch.dict(os.environ, {"VERCEL": "1"}), \
             patch("builtins.open", side_effect=AssertionError("filesystem write attempted")):
            with self.assertRaises(OSError):
                server.save_tickets({"tickets": []})
            with self.assertRaises(OSError):
                server.save_resolution_records({"verified_resolutions": []})
            with self.assertRaises(OSError):
                server.save_knowledge_articles({"articles": []})

    def test_resolution_article_approval_and_reuse_share_one_browser_state(self):
        _, initial = self.dispatch("/api/tickets")
        token = initial["demo_state"]
        created, resolution_result = self.dispatch(
            "/api/tickets/INC100006/resolutions", "POST", {
                "confirmed_root_cause": "Consultant verified the relevant role assignment.",
                "actions_performed": ["Compared current assignments with the documented role."],
                "verification_evidence": "Reporter retested and confirmed the task is visible.",
                "consultant_id": "CONSULTANT-DEMO-1",
                "consultant_confirmed": True,
            }, token,
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(resolution_result["status_code"], 201)
        resolution = resolution_result["result"]["resolution"]
        token = resolution_result["demo_state"]

        _, article_result = self.dispatch(
            "/api/tickets/INC100006/knowledge-articles", "POST", {
                "source_resolution_id": resolution["resolution_id"],
                "title": "SAP SD approval task visibility checks",
                "symptoms": "An approver cannot see the reported sales document task.",
            }, token,
        )
        self.assertEqual(article_result["status_code"], 409)
        self.assertEqual(article_result["result"]["error_info"]["code"], "DUPLICATE_KNOWLEDGE")
        _, article_result = self.dispatch(
            "/api/tickets/INC100006/knowledge-articles", "POST", {
                "source_resolution_id": resolution["resolution_id"],
                "title": "SAP SD approval task visibility checks",
                "symptoms": "An approver cannot see the reported sales document task.",
                "review_acknowledged": True,
            }, article_result["demo_state"],
        )
        self.assertEqual(article_result["status_code"], 201)
        article = article_result["result"]["article"]
        self.assertEqual(article["review_status"], "Draft")
        token = article_result["demo_state"]

        _, draft_search = self.dispatch(
            "/api/knowledge-articles/search?source_system=SAP%20SD&issue_category=Task%20Not%20Visible&q=approver",
            state=token,
        )
        self.assertNotIn(article["article_id"], [row["article_id"] for row in draft_search["result"]["articles"]])

        _, approved_result = self.dispatch(
            f"/api/knowledge-articles/{article['article_id']}", "PATCH",
            {"approve": True, "approved_by": "REVIEWER-DEMO-1"}, token,
        )
        self.assertEqual(approved_result["status_code"], 200)
        token = approved_result["demo_state"]

        _, search_result = self.dispatch(
            "/api/knowledge-articles/search?source_system=SAP%20SD&issue_category=Task%20Not%20Visible&q=approver",
            state=token,
        )
        matches = search_result["result"]["articles"]
        self.assertIn(article["article_id"], [row["article_id"] for row in matches])
        self.assertEqual(
            next(row for row in matches if row["article_id"] == article["article_id"])["knowledge_source"],
            "approved_captured_article",
        )
        token = search_result["demo_state"]
        future_ticket = {
            "incident_number": "INC199999", "source_system": "SAP SD", "system": "SAP SD",
            "issue_type": "Task Not Visible", "issue": "Task Not Visible",
            "category": "Task Not Visible", "status": "In Progress", "priority": "P3",
            "short_description": "A manager cannot find a sales document approval task.",
            "description": "A manager cannot find a sales document approval task in Unified Inbox.",
            "additional_comments": "The approver cannot see the sales document task.",
            "test_detail": "N/A — No error displayed",
        }
        with demo_state.use_state(demo_state.decode_state(token)), \
             patch.object(llm_agent, "_preflight", return_value=(future_ticket, [], {}, [], [])):
            investigation = llm_agent.run_investigation("INC199999", use_llm=False)
        self.assertIn(article["article_id"], [
            row["article_id"] for row in investigation["captured_knowledge_used"]
        ])
        self.assertEqual(
            investigation["captured_knowledge_used"][0]["source_incident"],
            "INC100006",
        )
        self.assertNotIn(
            "user's role assignments have been verified",
            investigation["final_analysis"].lower(),
        )
        _, resolution_read = self.dispatch("/api/tickets/INC100006/resolutions", state=token)
        _, article_read = self.dispatch("/api/tickets/INC100006/knowledge-articles", state=resolution_read["demo_state"])
        self.assertIn(resolution["resolution_id"], [
            row["resolution_id"] for row in resolution_read["result"]["resolutions"]
        ])
        self.assertEqual(
            next(row for row in article_read["result"]["articles"] if row["article_id"] == article["article_id"])["review_status"],
            "Approved",
        )

    def test_reset_restores_seed_status_and_seed_knowledge_for_that_browser(self):
        _, initial = self.dispatch("/api/tickets")
        _, changed = self.dispatch(
            "/api/tickets/INC100001", "PATCH",
            {"status": "In Progress", "expected_current_status": "Resolved", "confirmed": True},
            initial["demo_state"],
        )
        _, reset = self.dispatch("/api/tickets", reset=True)
        self.assertEqual(reset["status_code"], 200)
        self.assertEqual(reset["result"]["tickets"][0]["status"], "Resolved")
        _, refreshed = self.dispatch("/api/tickets", state=reset["demo_state"])
        self.assertEqual(refreshed["result"]["count"], 25)
        self.assertEqual(len(demo_state.decode_state(refreshed["demo_state"])["verified_resolutions"]), self.seed_resolution_count)
        self.assertEqual(len(demo_state.decode_state(refreshed["demo_state"])["articles"]), self.seed_article_count)
        self.assertNotEqual(changed["demo_state"], reset["demo_state"])

    def test_malformed_and_tampered_state_are_rejected(self):
        _, initial = self.dispatch("/api/tickets")
        token = initial["demo_state"]
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
        response, _ = self.dispatch("/api/tickets", state=tampered)
        self.assertEqual(response.status_code, 400)
        response, _ = self.dispatch("/api/tickets", method="POST", payload={}, state="not-a-token")
        self.assertEqual(response.status_code, 400)
        response, _ = self.dispatch("/api/tickets/INC100001", method="PATCH", payload={
            "status": "Closed", "expected_current_status": "Resolved", "confirmed": True,
        })
        self.assertEqual(response.status_code, 428)

    def test_state_validation_rejects_unknown_ticket_or_unverified_records(self):
        state = demo_state.initial_state()
        state["ticket_statuses"]["INC999999"] = 2
        with self.assertRaises(demo_state.DemoStateError):
            demo_state.validate_state(state)
        state = demo_state.initial_state()
        state["trusted_role_catalogue"] = []
        with self.assertRaises(demo_state.DemoStateError):
            demo_state.validate_state(state)

    def test_missing_deployment_secret_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            response, payload = self.dispatch("/api/tickets")
        self.assertEqual(response.status_code, 503)
        self.assertIn("DEMO_STATE_SECRET", payload["error"])
        state = demo_state.initial_state()
        state["verified_resolutions"].append({
            "resolution_id": "RES-UNVERIFIED", "incident_number": "INC100006",
            "confirmed_root_cause": "Unverified", "actions_performed": ["Something"],
            "verification_evidence": "Unknown", "consultant_id": "CONS-1",
            "confirmation_timestamp": "2026-10-15T00:00:00Z", "verified": False,
            "verification_source": "explicit_consultant_confirmation",
        })
        with self.assertRaises(demo_state.DemoStateError):
            demo_state.validate_state(state)

    def test_deployed_direct_api_calls_require_authenticated_dispatch(self):
        with patch.dict(os.environ, {"VERCEL": "1"}):
            response = self.client.patch("/api/tickets/INC100001", json={
                "status": "Closed", "expected_current_status": "Resolved", "confirmed": True,
            })
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.get_json()["authentication_required"])


if __name__ == "__main__":
    unittest.main()
