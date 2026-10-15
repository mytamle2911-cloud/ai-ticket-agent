import json
import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

from mock_api import server
from agent.status_intent import parse_status_intent


class MockApiUpdateTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.tickets = {"tickets": [{
            "incident_number": "INC100001", "status": "In Progress", "summary": "Keep unchanged",
            "source_system": "SAP MM", "category": "Approval Action",
            "short_description": "Approval action unavailable", "additional_comments": "Synthetic fixture",
            "linked_kb_document_ids": [],
        }]}
        self.load_patcher = patch.object(server, "load_tickets", return_value=self.tickets)
        self.save_patcher = patch.object(server, "save_tickets")
        self.load_patcher.start()
        self.save = self.save_patcher.start()
        self.addCleanup(self.load_patcher.stop)
        self.addCleanup(self.save_patcher.stop)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.resolution_path = os.path.join(self.temp_dir.name, "resolution_records.json")
        self.resolution_path_patcher = patch.object(server, "RESOLUTIONS_FILE", self.resolution_path)
        self.resolution_path_patcher.start()
        self.addCleanup(self.resolution_path_patcher.stop)
        self.articles_path = os.path.join(self.temp_dir.name, "knowledge_articles.json")
        self.articles_path_patcher = patch.object(server, "KNOWLEDGE_ARTICLES_FILE", self.articles_path)
        self.articles_path_patcher.start()
        self.addCleanup(self.articles_path_patcher.stop)
        self.addCleanup(self.temp_dir.cleanup)

    def test_invalid_update_field_is_rejected_without_saving(self):
        response = self.client.patch("/api/tickets/INC100001", json={"summary": "changed"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.tickets["tickets"][0]["summary"], "Keep unchanged")
        self.save.assert_not_called()

    def test_invalid_status_is_rejected_without_saving(self):
        response = self.client.patch("/api/tickets/INC100001", json={"status": "Approved"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.tickets["tickets"][0]["status"], "In Progress")
        self.save.assert_not_called()

    def test_malformed_or_empty_update_is_rejected(self):
        self.assertEqual(self.client.patch("/api/tickets/INC100001", json={}).status_code, 400)
        self.assertEqual(self.client.patch("/api/tickets/INC100001", data="not json", content_type="text/plain").status_code, 400)
        self.save.assert_not_called()

    def test_permitted_status_update_is_saved(self):
        response = self.client.patch("/api/tickets/INC100001", json={
            "status": "On Hold", "expected_current_status": "In Progress", "confirmed": True,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.tickets["tickets"][0]["status"], "On Hold")
        self.save.assert_called_once_with(self.tickets)

    def test_deterministic_parser_recognizes_supported_commands_and_ignores_normal_chat(self):
        cases = {
            "Change this ticket to In Progress.": "In Progress",
            "Set status to on hold.": "On Hold",
            "Mark INC100001 as Resolved.": "Resolved",
            "Change INC100001 status to Closed.": "Closed",
            "Update the current ticket status to New.": "New",
            "Move this incident to In Progress.": "In Progress",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(parse_status_intent(message)["target_status"], expected)
        self.assertIsNone(parse_status_intent("What does the current status mean?"))
        self.assertIn("not supported", parse_status_intent("Set this ticket to Approved") ["clarification"])
        self.assertIn("Which status", parse_status_intent("Change this ticket status") ["clarification"])

    def test_closed_status_uses_existing_consultant_confirmation_gate(self):
        self.tickets["tickets"][0]["status"] = "In Progress"
        proposed = self.client.post("/api/tickets/INC100001/chat", json={
            "message": "Change INC100001 status to Closed.",
        })
        self.assertEqual(proposed.status_code, 200)
        proposal = proposed.get_json()["status_update"]
        self.assertEqual(proposal["state"], "pending_confirmation")
        self.assertEqual(proposal["requested_status"], "Closed")
        self.assertEqual(self.tickets["tickets"][0]["status"], "In Progress")
        self.save.assert_not_called()

        confirmed = self.client.patch("/api/tickets/INC100001", json={
            "status": proposal["requested_status"],
            "expected_current_status": proposal["current_status"],
            "confirmed": True,
        })
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.get_json()["ticket"]["status"], "Closed")
        self.assertEqual(self.tickets["tickets"][0]["status"], "Closed")
        self.save.assert_called_once_with(self.tickets)

    def test_chat_prepares_status_update_without_writing_and_resolves_selected_ticket(self):
        self.tickets["tickets"][0]["status"] = "On Hold"
        with patch.object(server.chat_agent, "chat_with_incident") as model_chat:
            response = self.client.post("/api/tickets/INC100001/chat", json={
                "message": "Change this ticket to In Progress.",
            })
        self.assertEqual(response.status_code, 200)
        plan = response.get_json()["status_update"]
        self.assertEqual(plan["state"], "pending_confirmation")
        self.assertEqual(plan["incident_number"], "INC100001")
        self.assertEqual(plan["current_status"], "On Hold")
        self.assertEqual(plan["requested_status"], "In Progress")
        self.assertFalse(plan["explicit_target"])
        self.assertEqual(self.tickets["tickets"][0]["status"], "On Hold")
        self.save.assert_not_called()
        model_chat.assert_not_called()

    def test_explicit_incident_is_never_substituted_with_selected_incident(self):
        self.tickets["tickets"].append({"incident_number": "INC100002", "status": "New"})
        response = self.client.post("/api/tickets/INC100001/chat", json={
            "message": "Mark INC100002 as Resolved.",
        })
        plan = response.get_json()["status_update"]
        self.assertEqual(plan["incident_number"], "INC100002")
        self.assertTrue(plan["explicit_target"])
        self.assertEqual(plan["current_status"], "New")
        self.assertEqual(self.tickets["tickets"][0]["status"], "In Progress")
        self.save.assert_not_called()

    def test_status_prepare_handles_already_current_and_unknown_incident(self):
        same = self.client.post("/api/tickets/INC100001/chat", json={
            "message": "Set status to In Progress.",
        })
        self.assertEqual(same.get_json()["status_update"]["state"], "already_current")
        missing = self.client.post("/api/tickets/INC100001/chat", json={
            "message": "Mark INC100099 as Resolved.",
        })
        self.assertEqual(missing.get_json()["status_update"]["state"], "not_found")
        self.save.assert_not_called()

    def test_status_prepare_rejects_invalid_existing_status(self):
        self.tickets["tickets"][0]["status"] = None
        response = self.client.post("/api/tickets/INC100001/chat", json={
            "message": "Set this ticket to Resolved.",
        })
        self.assertEqual(response.get_json()["status_update"]["state"], "invalid_current_status")
        self.save.assert_not_called()

    def test_status_update_requires_confirmation_and_rejects_stale_or_duplicate_submission(self):
        path = "/api/tickets/INC100001"
        body = {"status": "On Hold", "expected_current_status": "In Progress", "confirmed": True}
        no_consent = self.client.patch(path, json={**body, "confirmed": False})
        self.assertEqual(no_consent.status_code, 400)
        unsupported = self.client.patch(path, json={**body, "status": "Approved"})
        self.assertEqual(unsupported.status_code, 400)
        self.save.assert_not_called()

        self.tickets["tickets"][0]["status"] = "New"  # Changed since the proposal was prepared.
        stale = self.client.patch(path, json=body)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()["current_status"], "New")
        self.save.assert_not_called()

        fresh = self.client.patch(path, json={**body, "expected_current_status": "New"})
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(fresh.get_json()["ticket"]["status"], "On Hold")
        duplicate = self.client.patch(path, json={**body, "expected_current_status": "New"})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.tickets["tickets"][0]["status"], "On Hold")
        self.save.assert_called_once()
        refreshed = self.client.get("/api/tickets")
        self.assertEqual(refreshed.get_json()["tickets"][0]["status"], "On Hold")

    def test_status_persistence_failure_does_not_return_success(self):
        self.load_patcher.stop()
        self.load_patcher = patch.object(server, "load_tickets", return_value=deepcopy(self.tickets))
        self.load_patcher.start()
        self.addCleanup(self.load_patcher.stop)
        with patch.object(server, "save_tickets", side_effect=OSError("disk unavailable")):
            response = self.client.patch("/api/tickets/INC100001", json={
                "status": "Resolved", "expected_current_status": "In Progress", "confirmed": True,
            })
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("successfully", response.get_json().get("message", "").lower())

    def test_confirming_unknown_incident_returns_not_found(self):
        response = self.client.patch("/api/tickets/INC100099", json={
            "status": "Resolved", "expected_current_status": "New", "confirmed": True,
        })
        self.assertEqual(response.status_code, 404)
        self.save.assert_not_called()

    def test_react_workflow_api_contracts_cover_ticket_to_approved_knowledge_reuse(self):
        """Exercise the React-facing API sequence without live OpenAI or file mutation."""
        listed = self.client.get("/api/tickets")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["tickets"][0]["incident_number"], "INC100001")
        detail = self.client.get("/api/tickets/INC100001")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["incident_number"], "INC100001")

        investigation = {
            "ticket": detail.get_json(), "resolution_path": "HUMAN_SUPPORT",
            "ticket_understanding": {"issue_type": "Approval Action"},
            "evidence": {"role_catalogue_validation": {}}, "diagnostic_steps": [],
        }
        with patch.object(server.llm_agent, "run_investigation", return_value=investigation), patch.object(
            server.chat_agent, "chat_with_incident",
            return_value={"reply": "Ask the reporter for the exact error and retest result.", "error": None},
        ):
            response = self.client.post("/api/tickets/INC100001/investigation", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["investigation"]["resolution_path"], "HUMAN_SUPPORT")
        self.assertIn("exact error", response.get_json()["reply"])

        with patch.object(server.chat_agent, "chat_with_incident", return_value={
            "reply": "Check whether the same request loads again.", "error": None,
            "investigation": investigation,
        }) as chat:
            response = self.client.post("/api/tickets/INC100001/chat", json={
                "message": "What next?", "history": [{"role": "user", "content": "Troubleshoot"}],
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reply"], "Check whether the same request loads again.")
        self.assertEqual(chat.call_args.args[0:3], ("INC100001", "What next?", [{"role": "user", "content": "Troubleshoot"}]))

        resolution_response = self.client.post(
            "/api/tickets/INC100001/resolutions", json=self._resolution_payload(),
        )
        self.assertEqual(resolution_response.status_code, 201)
        resolution = resolution_response.get_json()["resolution"]
        with patch.object(server.knowledge_capture, "generate_article_content", return_value={
            "title": "Approval action unavailable", "symptoms": "The reported action does not appear.",
        }) as generate:
            draft_response = self.client.post("/api/tickets/INC100001/knowledge-articles/generate", json={
                "resolution_id": resolution["resolution_id"],
            })
        self.assertEqual(draft_response.status_code, 201)
        article = draft_response.get_json()["article"]
        generate.assert_called_once()
        self.assertEqual(article["review_status"], "Draft")
        self.assertEqual(article["confirmed_root_cause"], resolution["confirmed_root_cause"])

        unapproved_search = self.client.get("/api/knowledge-articles/search?source_system=SAP%20MM&q=approval")
        self.assertEqual(unapproved_search.get_json()["articles"], [])
        approval = self.client.patch(f"/api/knowledge-articles/{article['article_id']}", json={
            "approve": True, "approved_by": "SYNTH-REVIEWER-1",
        })
        self.assertEqual(approval.status_code, 200)
        approved_search = self.client.get("/api/knowledge-articles/search?source_system=SAP%20MM&q=approval")
        self.assertEqual(approved_search.status_code, 200)
        self.assertEqual([row["article_id"] for row in approved_search.get_json()["articles"]], [article["article_id"]])
        self.assertEqual(self.tickets["tickets"][0]["status"], "In Progress")

    def test_new_ai_routes_reject_invalid_payloads_and_incident_ids(self):
        self.assertEqual(self.client.post("/api/tickets/nope/investigation", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/tickets/INC100001/chat", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/tickets/INC100001/knowledge-articles/generate", json={}).status_code, 400)

    def _resolution_payload(self, **overrides):
        payload = {
            "confirmed_root_cause": "Consultant-confirmed synthetic cause.",
            "actions_performed": ["Compared the reported item and retested."],
            "verification_evidence": "Reporter reopened the item successfully during retest.",
            "consultant_id": "SYNTH-CONS-01",
            "consultant_confirmed": True,
        }
        payload.update(overrides)
        return payload

    def test_consultant_confirmed_resolution_is_persisted_separately_and_retrievable(self):
        response = self.client.post(
            "/api/tickets/INC100001/resolutions", json=self._resolution_payload(),
        )
        self.assertEqual(response.status_code, 201)
        record = response.get_json()["resolution"]
        self.assertEqual(record["incident_number"], "INC100001")
        self.assertEqual(record["confirmed_root_cause"], "Consultant-confirmed synthetic cause.")
        self.assertEqual(record["actions_performed"], ["Compared the reported item and retested."])
        self.assertEqual(record["verification_evidence"], "Reporter reopened the item successfully during retest.")
        self.assertEqual(record["consultant_id"], "SYNTH-CONS-01")
        self.assertTrue(record["verified"])
        self.assertEqual(record["verification_source"], "explicit_consultant_confirmation")
        self.assertTrue(record["confirmation_timestamp"].endswith("Z"))

        with open(self.resolution_path, encoding="utf-8") as file:
            persisted = json.load(file)
        self.assertEqual(persisted["verified_resolutions"], [record])
        retrieved = self.client.get("/api/tickets/INC100001/resolutions")
        self.assertEqual(retrieved.status_code, 200)
        self.assertEqual(retrieved.get_json()["resolutions"], [record])
        self.assertEqual(self.tickets["tickets"][0]["status"], "In Progress")
        self.save.assert_not_called()

    def test_incomplete_or_unconfirmed_resolution_is_not_saved(self):
        incomplete = self._resolution_payload(verification_evidence=" ")
        response = self.client.post("/api/tickets/INC100001/resolutions", json=incomplete)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(os.path.exists(self.resolution_path))

        unconfirmed = self.client.post(
            "/api/tickets/INC100001/resolutions",
            json=self._resolution_payload(consultant_confirmed=False),
        )
        self.assertEqual(unconfirmed.status_code, 400)
        self.assertFalse(os.path.exists(self.resolution_path))

    def test_resolution_records_are_scoped_to_their_incident(self):
        self.tickets["tickets"].append({
            "incident_number": "INC100002", "status": "On Hold",
        })
        saved = self.client.post(
            "/api/tickets/INC100001/resolutions", json=self._resolution_payload(),
        )
        self.assertEqual(saved.status_code, 201)
        other_ticket = self.client.get("/api/tickets/INC100002/resolutions")
        self.assertEqual(other_ticket.status_code, 200)
        self.assertEqual(other_ticket.get_json()["resolutions"], [])
        missing_ticket = self.client.post(
            "/api/tickets/INC100003/resolutions", json=self._resolution_payload(),
        )
        self.assertEqual(missing_ticket.status_code, 404)
        self.assertEqual(len(server.load_resolution_records()["verified_resolutions"]), 1)

    def _save_verified_resolution(self):
        response = self.client.post(
            "/api/tickets/INC100001/resolutions", json=self._resolution_payload(),
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["resolution"]

    def _draft_payload(self, resolution_id):
        return {"source_resolution_id": resolution_id, "title": "Synthetic approval item troubleshooting", "symptoms": "A reported approval action was unavailable."}

    def test_knowledge_draft_requires_verified_resolution_and_is_persisted(self):
        self.assertEqual(self.client.post(
            "/api/tickets/INC100001/knowledge-articles",
            json=self._draft_payload("RES-NOT-VERIFIED"),
        ).status_code, 409)
        self.assertFalse(os.path.exists(self.articles_path))
        resolution = self._save_verified_resolution()
        response = self.client.post(
            "/api/tickets/INC100001/knowledge-articles",
            json=self._draft_payload(resolution["resolution_id"]),
        )
        self.assertEqual(response.status_code, 201)
        article = response.get_json()["article"]
        self.assertEqual(article["review_status"], "Draft")
        self.assertEqual(article["confirmed_root_cause"], resolution["confirmed_root_cause"])
        self.assertEqual(article["verified_troubleshooting_steps"], resolution["actions_performed"])
        self.assertEqual(article["verification_method"], resolution["verification_evidence"])
        self.assertEqual(article["created_by"], resolution["consultant_id"])
        self.assertEqual(article["source_resolution_id"], resolution["resolution_id"])
        with open(self.articles_path, encoding="utf-8") as file:
            persisted = json.load(file)
        self.assertEqual(persisted["articles"], [article])
        self.assertEqual(self.client.get("/api/tickets/INC100001/knowledge-articles").get_json()["articles"], [article])

    def test_duplicate_draft_creation_is_idempotent(self):
        resolution = self._save_verified_resolution()
        payload = self._draft_payload(resolution["resolution_id"])
        first = self.client.post("/api/tickets/INC100001/knowledge-articles", json=payload)
        second = self.client.post("/api/tickets/INC100001/knowledge-articles", json=payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.get_json()["created"])
        self.assertEqual(len(server.load_knowledge_articles()["articles"]), 1)

    def test_only_human_approval_publishes_and_search_excludes_drafts(self):
        resolution = self._save_verified_resolution()
        draft = self.client.post(
            "/api/tickets/INC100001/knowledge-articles",
            json=self._draft_payload(resolution["resolution_id"]),
        ).get_json()["article"]
        search = "/api/knowledge-articles/search?source_system=SAP%20MM&issue_category=Approval%20Action&q=approval"
        self.assertEqual(self.client.get(search).get_json()["count"], 0)
        missing_reviewer = self.client.patch(f"/api/knowledge-articles/{draft['article_id']}", json={"approve": True})
        self.assertEqual(missing_reviewer.status_code, 400)
        self.assertEqual(self.client.get(search).get_json()["count"], 0)
        edit_approve = self.client.patch(f"/api/knowledge-articles/{draft['article_id']}", json={
            "title": "Reviewed synthetic article", "approve": True, "approved_by": "SYNTH-REVIEWER-1",
        })
        self.assertEqual(edit_approve.status_code, 200)
        published = edit_approve.get_json()["article"]
        self.assertEqual(published["review_status"], "Approved")
        self.assertTrue(published["published_at"])
        found = self.client.get(search).get_json()["articles"]
        self.assertEqual([item["article_id"] for item in found], [draft["article_id"]])
        self.assertEqual(found[0]["knowledge_source"], "approved_captured_article")
        self.assertEqual(self.client.patch(f"/api/knowledge-articles/{draft['article_id']}", json={"approve": True, "approved_by": "OTHER"}).status_code, 409)
        self.assertEqual(self.tickets["tickets"][0]["status"], "In Progress")
        self.save.assert_not_called()

    def test_article_source_resolution_cannot_be_used_by_another_ticket(self):
        self.tickets["tickets"].append({"incident_number": "INC100002", "status": "On Hold"})
        resolution = self._save_verified_resolution()
        response = self.client.post(
            "/api/tickets/INC100002/knowledge-articles",
            json=self._draft_payload(resolution["resolution_id"]),
        )
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
