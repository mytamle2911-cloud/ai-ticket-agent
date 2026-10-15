"""Integrity and retrieval checks for Unified Inbox synthetic knowledge."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import llm_agent
from mock_api.server import legacy_ticket_view

ROOT = Path(__file__).resolve().parents[1]
DOCS = json.loads((ROOT / "data/product_docs.json").read_text())["documents"]
TICKETS = json.loads((ROOT / "data/tickets.json").read_text())["tickets"]
SYSTEMS = json.loads((ROOT / "data/systems.json").read_text())["systems"]
ROLES = json.loads((ROOT / "data/roles.json").read_text())["roles"]
ALLOWED_ISSUES = {
    "Task Not Visible", "Loading Time Too Slow",
    "Cannot Set Up Substitution", "Cannot See Specific Language",
}


class UnifiedInboxKnowledgeTests(unittest.TestCase):
    def test_document_schema_ids_systems_and_types(self):
        self.assertEqual(len({doc["document_id"] for doc in DOCS}), len(DOCS))
        source_names = {item["source_system"] for item in SYSTEMS}
        self.assertEqual(len(source_names), 6)
        self.assertEqual({doc["issue_type"] for doc in DOCS}, ALLOWED_ISSUES)
        for doc in DOCS:
            with self.subTest(document=doc["document_id"]):
                self.assertIn(doc["source_system"], source_names)
                self.assertEqual(doc["source_status"], "synthetic_draft")
                self.assertIsInstance(doc["error_signatures"], list)
                self.assertTrue(all(sig["type"] in {"message", "code"} and sig["value"] for sig in doc["error_signatures"]))
                self.assertIsInstance(doc["symptoms"], list)
                self.assertIsInstance(doc["diagnostic_steps"], list)
                self.assertEqual(doc["resolution_steps"], [])
                self.assertEqual(doc["allowed_actions"], [])
                self.assertTrue(all(isinstance(step.get(key), str) and step[key].strip()
                                    for step in doc["diagnostic_steps"]
                                    for key in ("step_id", "instruction", "expected_observation", "handling_when_unclear")))

    def test_draft_kb_documents_do_not_make_any_ticket_resolvable(self):
        tickets_by_id = {ticket["incident_number"]: legacy_ticket_view(ticket) for ticket in TICKETS}
        with patch.object(llm_agent, "get_ticket", side_effect=lambda inc: tickets_by_id[inc]):
            results = [llm_agent.run_investigation(ticket["incident_number"], use_llm=False) for ticket in TICKETS]
        self.assertEqual(len(results), 25)
        self.assertTrue(all(result["resolution_path"] == "HUMAN_SUPPORT" for result in results))
        self.assertTrue(all(result["error"] is None for result in results))

    def test_exact_candidate_and_no_match_retrieval_are_distinct_from_diagnosis(self):
        ticket = legacy_ticket_view(next(row for row in TICKETS if row["incident_number"] == "INC100012"))
        exact = llm_agent.search_product_docs(ticket["system"], ticket["test_detail"], ticket["issue"], llm_agent._ticket_search_fields(ticket))
        self.assertEqual(exact[0]["_retrieval"]["match_type"], "exact")
        candidate = llm_agent.search_product_docs(ticket["system"], "", "Loading Time Too Slow", {"summary": "requests are loading slowly"})
        self.assertEqual(candidate[0]["_retrieval"]["match_type"], "candidate")
        self.assertEqual(llm_agent.search_product_docs(ticket["system"], "unrelated error"), [])
        self.assertNotIn("confirmed_diagnosis", exact[0])

    def test_role_catalogue_has_exact_demo_ids_and_no_assignments(self):
        self.assertEqual({row["source_system"] for row in ROLES}, {row["source_system"] for row in SYSTEMS})
        role_ids = set()
        for system in ROLES:
            for role_type in ("gateway_roles", "backend_roles"):
                for role in system[role_type]:
                    self.assertTrue(role["role_id"].startswith("XX:"))
                    self.assertNotIn(role["role_id"], role_ids)
                    role_ids.add(role["role_id"])
        for ticket in TICKETS:
            self.assertNotIn("assigned_roles", ticket)
            self.assertNotIn("roles", ticket.get("user_information", {}))


if __name__ == "__main__":
    unittest.main()
