import builtins
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from agent import llm_agent as agent
from mock_api.server import legacy_ticket_view


class FakeResponses:
    def __init__(self, *responses):
        self._responses = iter(responses)
        self.create = Mock(side_effect=lambda **kwargs: next(self._responses))


def fake_response(output=(), output_text="candidate analysis"):
    return SimpleNamespace(output=list(output), output_text=output_text)


class AgentTests(unittest.TestCase):
    def setUp(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "data", "tickets.json"), encoding="utf-8") as file:
            tickets = json.load(file)["tickets"]
        with open(os.path.join(root, "data", "product_docs.json"), encoding="utf-8") as file:
            docs = {doc["document_id"]: doc for doc in json.load(file)["documents"]}
        raw = next(
            ticket for ticket in tickets
            if docs[ticket["linked_kb_document_ids"][0]]["source_status"] == "synthetic_draft"
        )
        self.ticket = legacy_ticket_view(raw)

    @patch.object(agent.requests, "get")
    def test_existing_ticket_lookup(self, get):
        response = Mock()
        expected = dict(self.ticket, incident_number="INC100002")
        response.json.return_value = expected
        get.return_value = response
        self.assertEqual(agent.get_ticket("inc100002"), expected)
        get.assert_called_once_with("http://127.0.0.1:5000/api/tickets/INC100002", timeout=10)

    @patch.object(agent.requests, "get")
    def test_missing_ticket_is_actionable_and_does_not_leak_response(self, get):
        response = Mock(status_code=404)
        response.raise_for_status.side_effect = requests.HTTPError("404 secret response", response=response)
        get.return_value = response
        with self.assertRaisesRegex(RuntimeError, "Ticket not found") as raised:
            agent.get_ticket("INC100002")
        self.assertNotIn("secret", str(raised.exception))

    def test_invalid_incident_number_rejected_before_request(self):
        with patch.object(agent.requests, "get") as get:
            with self.assertRaises(agent.ToolInputError):
                agent.get_ticket("../../etc/passwd")
            get.assert_not_called()

    def test_document_search_requires_source_and_error_match(self):
        matches = agent.search_product_docs(self.ticket["system"], self.ticket["test_detail"])
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["_retrieval"]["match_type"], "exact")
        candidates = agent.search_product_docs(self.ticket["system"], "", self.ticket["issue"], {"summary": "related approval item context"})
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["_retrieval"]["match_type"], "candidate")
        self.assertEqual(agent.search_product_docs(self.ticket["system"], "unrelated database unavailable"), [])

    def test_role_validation_reports_verified_and_unverified_ids(self):
        catalogue = next(item for item in agent._load_json("roles.json", "roles") if item["source_system"] == self.ticket["system"])
        gateway_id = catalogue["gateway_roles"][0]["role_id"]
        backend_id = catalogue["backend_roles"][0]["role_id"]
        result = agent.verify_roles(self.ticket["system"], [{"role_id": gateway_id}, {"role_id": "HALLUCINATED"}], [{"role_id": backend_id}])
        self.assertEqual([r["role_id"] for r in result["gateway_roles"]], [gateway_id])
        self.assertEqual(result["unverified_gateway_roles"][0]["role_id"], "HALLUCINATED")
        self.assertEqual([r["role_id"] for r in result["backend_roles"]], [backend_id])

    def test_source_system_lookup_returns_all_configured_roles_without_kb_references(self):
        catalogue = {
            "source_system": "PM7",
            "gateway_roles": [
                {"role_id": "GW_1", "role_name": "Gateway One"},
                {"role_id": "GW_2", "role_name": "Gateway Two"},
            ],
            "backend_roles": [
                {"role_id": "BE_1", "role_name": "Backend One"},
                {"role_id": "BE_2", "role_name": "Backend Two"},
            ],
        }
        with patch.object(agent, "_load_json", return_value=[catalogue]):
            result = agent.verify_roles("pm7", [], [])
        self.assertTrue(result["catalogue_found"])
        self.assertEqual([r["role_id"] for r in result["configured_gateway_roles"]], ["GW_1", "GW_2"])
        self.assertEqual([r["role_id"] for r in result["configured_backend_roles"]], ["BE_1", "BE_2"])
        self.assertEqual(result["gateway_roles"], [])
        self.assertEqual(result["backend_roles"], [])

    def test_source_system_lookup_reports_missing_catalogue_and_empty_configuration(self):
        with patch.object(agent, "_load_json", return_value=[]):
            missing = agent.verify_roles("PM7", [], [])
        self.assertFalse(missing["catalogue_found"])
        self.assertEqual(missing["configured_gateway_roles"], [])
        self.assertEqual(missing["configured_backend_roles"], [])

        empty = {"source_system": "PM7", "gateway_roles": [], "backend_roles": []}
        with patch.object(agent, "_load_json", return_value=[empty]):
            unconfigured = agent.verify_roles("PM7", [], [])
        self.assertTrue(unconfigured["catalogue_found"])
        self.assertEqual(unconfigured["configured_gateway_roles"], [])
        self.assertEqual(unconfigured["configured_backend_roles"], [])

    def test_preflight_uses_ticket_source_system_for_catalogue_lookup(self):
        catalogue = next(item for item in agent._load_json("roles.json", "roles") if item["source_system"] == "SAP MM")
        ticket = dict(self.ticket, system="Legacy System", source_system="SAP MM")
        with patch.object(agent, "get_ticket", return_value=ticket), \
             patch.object(agent, "_structured_retrieval", return_value=[]), \
             patch.object(agent, "search_product_docs", return_value=[]):
            _, _, role_check, _, _ = agent._preflight(ticket["incident_number"])
        self.assertEqual(role_check["source_system"], "SAP MM")
        self.assertEqual(role_check["configured_gateway_roles"], catalogue["gateway_roles"])
        self.assertEqual(role_check["configured_backend_roles"], catalogue["backend_roles"])

    def _run_with_fixture(self, *, ticket=None, docs=None, response=None):
        ticket = ticket or self.ticket
        docs = docs
        fake_client = SimpleNamespace(responses=FakeResponses(response or fake_response()))
        with patch.object(agent, "get_ticket", return_value=ticket), \
             patch.object(agent, "client", fake_client):
            return agent.run_investigation(ticket["incident_number"])

    def test_matching_draft_kb_is_retrieval_not_resolution(self):
        with patch.object(agent, "_same_ticket_verified_resolution", return_value=None):
            result = self._run_with_fixture()
        self.assertEqual(result["evidence_status"], "confirmed")
        self.assertEqual(result["resolution_path"], "HUMAN_SUPPORT")
        self.assertFalse(result["approval_needed"])
        self.assertEqual(result["approval_required"], False)
        self.assertEqual(result["kb_matches"][0]["match_type"], "exact")
        self.assertIn("not proof of root cause", result["final_analysis"])
        self.assertEqual(result["proposed_actions"], [])

    def test_missing_documentation_has_explicit_no_match_state(self):
        ticket = dict(self.ticket, source_system="Unlisted system", system="Unlisted system",
                      issue_type="unclassified synthetic issue", issue="unclassified synthetic issue",
                      test_detail="unknown synthetic error", error_message="unknown synthetic error")
        result = self._run_with_fixture(ticket=ticket)
        self.assertEqual(result["evidence_status"], "no_matching_evidence")
        self.assertFalse(result["approval_needed"])

    def test_exact_document_match_without_reviewed_resolution_stays_human_support(self):
        result = self._run_with_fixture(ticket=dict(self.ticket, incident_number="INC100001"))
        self.assertEqual(result["evidence_status"], "confirmed")
        self.assertEqual(result["resolution_path"], "HUMAN_SUPPORT")
        self.assertFalse(result["approval_needed"])

    def _ticket_by_id(self, incident_number):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tickets.json")
        with open(path, encoding="utf-8") as file:
            raw = next(row for row in json.load(file)["tickets"] if row["incident_number"] == incident_number)
        return legacy_ticket_view(raw)

    def test_scenario_defined_cause_uses_source_issue_signature_and_reference_not_incident_id(self):
        ticket = self._ticket_by_id("INC100001")
        ticket["incident_number"] = "INC199999"
        result = self._run_with_fixture(ticket=ticket, response=fake_response(output_text="A filter probably hides the task."))
        assessment = result["root_cause_assessment"]
        self.assertEqual(assessment["state"], "scenario_expected_unverified")
        self.assertEqual(assessment["source"], "synthetic_scenario")
        self.assertIn("Gateway role", assessment["statement"])
        self.assertFalse(assessment["verified"])
        self.assertEqual(result["scenario_matches"][0]["scenario_id"], "TS01-PM7-01")
        self.assertNotIn("filter", assessment["statement"].lower())
        self.assertEqual(result["resolution_path"], "HUMAN_SUPPORT")

    def test_apr001_roles_are_exact_catalogue_entries_and_actions_avoid_generic_filter_steps(self):
        ticket = self._ticket_by_id("INC100001")
        original_status = ticket["status"]
        result = self._run_with_fixture(ticket=ticket, response=fake_response(output_text="Filters are likely."))
        roles = result["evidence"]["role_catalogue_validation"]
        self.assertEqual([item["role_id"] for item in roles["configured_gateway_roles"]], ["XX:GATEWAY_PM7:XX"])
        self.assertEqual([item["role_id"] for item in roles["configured_backend_roles"]], ["XX:BACKEND_PM7:XX"])
        steps = result["recommended_troubleshooting_steps"]
        self.assertEqual(len(steps), 3)
        self.assertEqual(result["proposed_resolution_steps"], [])
        self.assertTrue(all("filter" not in item["instruction"].lower() and "view" not in item["instruction"].lower() for item in steps))
        self.assertIn("PR-1001", result["verification_steps"][0]["instruction"])
        self.assertEqual(result["ticket"]["status"], original_status)

    def test_same_source_and_issue_but_different_reference_does_not_match_scenario(self):
        ticket = self._ticket_by_id("INC100001")
        ticket["description"] = "The manager cannot find purchase request PR-9999."
        ticket["additional_comments"] = ticket["description"]
        ticket["summary"] = "The manager cannot find purchase request PR-9999."
        ticket["legacy_fields"]["description"] = ticket["description"]
        ticket["legacy_fields"]["summary"] = ticket["summary"]
        ticket["scenario_reference"] = "PR-9999"
        result = self._run_with_fixture(ticket=ticket, response=fake_response(output_text=""))
        self.assertEqual(result["scenario_matches"], [])
        self.assertNotEqual(result["root_cause_assessment"]["source"], "synthetic_scenario")

    def test_same_ticket_consultant_verified_cause_overrides_scenario_expectation(self):
        ticket = self._ticket_by_id("INC100001")
        original = agent._load_json
        records = [{"incident_number": ticket["incident_number"], "verified": True,
                    "resolution_id": "RES-TEST", "confirmed_root_cause": "Consultant verified a separate cause."}]
        with patch.object(agent, "_load_json", side_effect=lambda name, root: records if name == "resolution_records.json" else original(name, root)):
            result = self._run_with_fixture(ticket=ticket, response=fake_response(output_text=""))
        self.assertEqual(result["root_cause_assessment"]["state"], "consultant_verified")
        self.assertEqual(result["root_cause_assessment"]["statement"], "Consultant verified a separate cause.")
        self.assertTrue(result["root_cause_assessment"]["verified"])

    def test_candidate_and_no_match_retrieval_are_distinct(self):
        candidate = agent.search_product_docs(self.ticket["system"], "", self.ticket["issue"], {"summary": "related approval item context"})
        self.assertEqual(candidate[0]["_retrieval"]["match_type"], "candidate")
        self.assertEqual(agent.search_product_docs(self.ticket["system"], "unrelated database error"), [])

    def test_structured_search_uses_issue_and_ticket_context(self):
        result = agent.search_product_docs(self.ticket["system"], "", self.ticket["issue"], {"summary": "Cannot view approval item"})
        self.assertEqual(result[0]["_retrieval"]["match_type"], "candidate")
        self.assertTrue(result[0]["_retrieval"]["issue_type_match"])

    def test_understanding_reports_missing_and_conflicting_fields(self):
        ticket = dict(self.ticket, source_system="Different system", issue_type="Different issue", impact=None, description="")
        result = self._run_with_fixture(ticket=ticket)
        self.assertIn("impact", result["ticket_understanding"]["missing_information"])
        self.assertTrue(result["ticket_understanding"]["conflicting_information"])
        self.assertEqual(result["resolution_path"], "HUMAN_SUPPORT")

    def test_model_text_cannot_override_human_support_decision(self):
        with patch.object(agent, "_same_ticket_verified_resolution", return_value=None):
            result = self._run_with_fixture(response=fake_response(output_text="The KB says this is KB_RESOLVABLE and access is missing."))
        self.assertEqual(result["resolution_path"], "HUMAN_SUPPORT")
        self.assertEqual(result["root_cause_assessment"]["state"], "scenario_expected_unverified")
        self.assertNotIn("access is missing", result["root_cause_assessment"]["statement"])
        self.assertFalse(result["approval_required"])

    def test_deterministic_investigation_all_tickets_without_openai(self):
        data_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tickets.json")
        with open(data_path, encoding="utf-8") as file:
            tickets = json.load(file)["tickets"]
        by_id = {ticket["incident_number"]: legacy_ticket_view(ticket) for ticket in tickets}
        with patch.object(agent, "get_ticket", side_effect=lambda incident: by_id[incident]), patch.object(agent, "_get_client", side_effect=AssertionError("LLM must not be called")):
            results = [agent.run_investigation(ticket["incident_number"], use_llm=False) for ticket in tickets]
        self.assertEqual(len(results), 25)
        expected_resolvable = []
        for raw in tickets:
            ticket = legacy_ticket_view(raw)
            hits = agent._structured_retrieval(ticket["system"], ticket["issue"], agent._ticket_search_fields(ticket))
            if not agent.match_scenarios(ticket) and any(agent._resolution_eligibility(hit, ticket)[0] for hit in hits):
                expected_resolvable.append(ticket["incident_number"])
        actual_resolvable = [result["ticket"]["incident_number"] for result in results if result["resolution_path"] == "KB_RESOLVABLE"]
        self.assertEqual(actual_resolvable, expected_resolvable)
        self.assertEqual(actual_resolvable, [])
        self.assertTrue(all(result["ticket_understanding"]["ticket_id"] for result in results))
        self.assertTrue(all(result["error"] is None for result in results))

    def test_inc100012_uses_hr_ess_operational_record_without_claiming_live_status(self):
        ticket = self._ticket_by_id("INC100012")
        result = self._run_with_fixture(ticket=ticket)
        self.assertEqual(result["resolution_path"], "HUMAN_SUPPORT")
        self.assertEqual(result["ticket_understanding"]["source_system"], "HR ESS")
        self.assertEqual(result["unified_inbox_evidence"]["operational_incident_state"], "Resolved")
        self.assertIsNone(result["unified_inbox_evidence"]["operational_incident"]["actual_resolution_at"])
        self.assertIn("actual recovery timestamp is not recorded", result["unified_inbox_evidence"]["operational_incident_guidance"])

    def test_approved_captured_knowledge_is_separate_from_deterministic_resolution(self):
        article = {
            "article_id": "KBA-SYNTH-1", "title": "Approved prior example",
            "source_incident": "INC100001", "source_system": "SAP MM",
            "issue_category": "Approval Action", "review_status": "Approved",
            "knowledge_source": "approved_captured_article",
        }
        ticket = self._ticket_by_id("INC100001")
        original_status = ticket["status"]
        with patch("agent.captured_knowledge.search_approved_articles", return_value=([article], None)):
            result = self._run_with_fixture(ticket=ticket)
        self.assertEqual(result["resolution_path"], "HUMAN_SUPPORT")
        self.assertEqual(result["captured_knowledge_matches"], [article])
        self.assertEqual(result["ticket"]["status"], original_status)

    def test_investigation_never_mutates_any_rebuilt_ticket(self):
        with patch.object(agent.requests, "patch") as patch_request:
            result = self._run_with_fixture(ticket=self._ticket_by_id("INC100012"))
        self.assertEqual(result["resolution_path"], "HUMAN_SUPPORT")
        self.assertFalse(result["approval_needed"])
        patch_request.assert_not_called()

    def test_role_catalogue_does_not_prove_missing_user_access(self):
        result = self._run_with_fixture()
        self.assertIn("does not establish a user's access", result["evidence"]["role_assignment_evidence"])

    def test_malformed_tool_arguments_are_rejected(self):
        with self.assertRaises(agent.ToolInputError):
            agent.execute_tool("get_ticket", {"unexpected": "INC100001"})
        with self.assertRaises(agent.ToolInputError):
            agent.execute_tool("verify_roles", {"source_system": "A", "gateway_roles": "not-a-list", "backend_roles": []})

    def test_invalid_json_tool_call_is_recorded_without_crashing(self):
        tool_call = SimpleNamespace(type="function_call", name="get_ticket", arguments="{bad", call_id="call-1")
        fake_client = SimpleNamespace(responses=FakeResponses(fake_response([tool_call], ""), fake_response([], "candidate only")))
        with patch.object(agent, "get_ticket", return_value=self.ticket), patch.object(agent, "client", fake_client):
            result = agent.run_investigation("INC100002")
        self.assertEqual(result["candidate_findings"], "candidate only")
        call_result = result["steps"][1]["tool_calls"][0]["result"]
        self.assertIn("error", call_result)

    def test_unexpected_model_response_fails_closed(self):
        malformed = SimpleNamespace(output="unexpected", output_text="claim access is missing")
        fake_client = SimpleNamespace(responses=FakeResponses(malformed))
        with patch.object(agent, "get_ticket", return_value=self.ticket), patch.object(agent, "client", fake_client):
            result = agent.run_investigation("INC100002")
        self.assertFalse(result["approval_needed"])
        self.assertEqual(result["evidence_status"], "confirmed")
        self.assertIn("unexpected response", result["error"])

    def test_malformed_function_call_response_fails_closed(self):
        malformed_call = SimpleNamespace(type="function_call", name="get_ticket", arguments="{}")
        fake_client = SimpleNamespace(responses=FakeResponses(fake_response([malformed_call], "")))
        with patch.object(agent, "get_ticket", return_value=self.ticket), patch.object(agent, "client", fake_client):
            result = agent.run_investigation("INC100002")
        self.assertFalse(result["approval_needed"])
        self.assertIn("malformed tool response", result["error"])

    def test_openai_timeout_fails_closed_without_exposing_exception(self):
        fake_client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=TimeoutError("secret request details"))))
        with patch.object(agent, "get_ticket", return_value=self.ticket), patch.object(agent, "client", fake_client):
            result = agent.run_investigation("INC100002")
        self.assertFalse(result["approval_needed"])
        self.assertIn("timed out", result["error"])
        self.assertNotIn("secret", result["error"])

    def test_openai_api_error_is_sanitized(self):
        fake_client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=RuntimeError("secret request payload"))))
        with patch.object(agent, "get_ticket", return_value=self.ticket), patch.object(agent, "client", fake_client):
            result = agent.run_investigation("INC100002")
        self.assertFalse(result["approval_needed"])
        self.assertEqual(result["error"], "The OpenAI investigation request failed. Check the OpenAI service and app configuration, then retry.")
        self.assertNotIn("secret", result["error"])

    def test_ai_investigation_never_updates_status(self):
        patcher = patch.object(agent, "requests", wraps=agent.requests)
        with patch.object(agent, "get_ticket", return_value=self.ticket), patch.object(agent, "client", SimpleNamespace(responses=FakeResponses(fake_response()))), patcher as requests_mock:
            result = agent.run_investigation("INC100002")
        self.assertFalse(result["approval_needed"])
        self.assertEqual(result["resolution_path"], "HUMAN_SUPPORT")
        requests_mock.patch.assert_not_called()

    def test_cli_approval_accept_and_reject(self):
        eligible = {"approval_needed": False, "final_analysis": "review", "candidate_findings": ""}
        with patch.object(agent, "run_investigation", return_value=eligible), patch.object(agent, "update_ticket") as update, patch.object(builtins, "input", return_value="no"):
            agent.analyze_ticket("INC100002")
            update.assert_not_called()

    def test_update_helper_requires_approval_and_valid_status(self):
        with patch.object(agent.requests, "patch") as patch_request:
            with self.assertRaises(PermissionError):
                agent.update_ticket("INC100002", "On Hold")
            with self.assertRaises(agent.ToolInputError):
                agent.update_ticket("INC100002", "Invented", approved=True)
            patch_request.assert_not_called()

    def test_approved_update_sends_explicit_confirmation_and_expected_status(self):
        response = Mock()
        response.json.return_value = {"ticket": dict(self.ticket, status="Resolved")}
        with patch.object(agent, "get_ticket", return_value=dict(self.ticket, status="On Hold")), patch.object(
            agent.requests, "patch", return_value=response,
        ) as patch_request:
            result = agent.update_ticket("INC100002", "Resolved", approved=True)
        self.assertEqual(result["ticket"]["status"], "Resolved")
        self.assertEqual(patch_request.call_args.kwargs["json"], {
            "status": "Resolved", "expected_current_status": "On Hold", "confirmed": True,
        })

    def test_resolved_ticket_cannot_be_moved_to_on_hold(self):
        for terminal_status in ("Resolved", "Closed"):
            with self.subTest(status=terminal_status), patch.object(agent, "get_ticket", return_value=dict(self.ticket, status=terminal_status)), patch.object(agent.requests, "patch") as patch_request:
                with self.assertRaisesRegex(PermissionError, "Resolved or Closed"):
                    agent.update_ticket("INC100002", "On Hold", approved=True)
                patch_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
