"""Validation of the rebuilt synthetic Unified Inbox incident dataset."""

import json
import hashlib
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agent import llm_agent
from agent.scenario_knowledge import match_scenarios
from agent.unified_inbox import collect_ticket_support_evidence
from mock_api import server

ROOT = Path(__file__).resolve().parents[1]


def data(filename, root_key):
    return json.loads((ROOT / "data" / filename).read_text())[root_key]


TICKETS = data("tickets.json", "tickets")
DOCS = data("product_docs.json", "documents")
ROLES = data("roles.json", "roles")
SYSTEMS = data("systems.json", "systems")
SCENARIOS = data("troubleshooting_scenarios.json", "synthetic_scenarios")
OPERATIONS = data("operational_incidents.json", "operational_incidents")


class UnifiedInboxDatasetTests(unittest.TestCase):
    def test_exact_ticket_count_issue_types_systems_and_unique_ids(self):
        self.assertEqual(len(TICKETS), 25)
        self.assertEqual(len({t["incident_number"] for t in TICKETS}), 25)
        self.assertEqual({t["incident_number"] for t in TICKETS}, {f"INC1000{i:02d}" for i in range(1, 26)})
        self.assertEqual({t["issue_type"] for t in TICKETS}, {
            "Task Not Visible", "Loading Time Too Slow",
            "Cannot Set Up Substitution", "Cannot See Specific Language",
        })
        self.assertEqual({t["source_system"] for t in TICKETS}, {
            "PM7", "SAP MM", "SAP FI/CO", "bTravel", "HR ESS", "SAP SD",
        })
        self.assertEqual(len(SYSTEMS), 6)

    def test_incident_distribution_and_ticket_evidence_fields(self):
        counts = {}
        for ticket in TICKETS:
            counts[ticket["issue_type"]] = counts.get(ticket["issue_type"], 0) + 1
            self.assertTrue(ticket["additional_comments"].strip())
            self.assertEqual(ticket["additional_comments"], ticket["legacy_fields"]["description"])
            self.assertEqual(ticket["error_message"], ticket["legacy_fields"]["test_detail"])
            self.assertIn("scenario_expected_unverified", ticket["scenario_root_cause"]["state"])
        self.assertEqual(counts, {
            "Task Not Visible": 8, "Loading Time Too Slow": 6,
            "Cannot Set Up Substitution": 5, "Cannot See Specific Language": 6,
        })

    def test_existing_ticket_status_priority_and_service_fields_are_preserved(self):
        # Digests preserve the original field-level regression check without
        # making this test depend on the excluded migration backup snapshot.
        original_service_field_digests = {
            "INC100001": "8c405378bba3c6eed7e922b6169a1527ae370644f2bd63b1fa760df7e403f14c",
            "INC100002": "3153763a280ad6475ccac173283e0b861fb2ea93fb5af43d1b8aa1cce918ec43",
            "INC100003": "e76a743487cc7688689d054092853e9938f15d505c5727caefba519ee3250767",
            "INC100004": "eb3624de0d808f3c15c728d22a8efb47e1eed0ea701f5d794188743c95488965",
            "INC100005": "340cd30aa45606c79a6565771833d9dae2a1a98eeb74a4a5d1954402ee579b34",
            "INC100006": "a1264eb583842c9d88100097388d5a140a200f07cdd20579897eae0fc8817bdf",
            "INC100007": "74a368d1dcba810fec882c1ef15186f4d212c9576ab4068945464bf8d388e8de",
            "INC100008": "4b38b55e1a5556cfd23d02c5f837bd443a4432d8c109342a8d9c85054c109492",
            "INC100009": "ee694e254aea06d65f33da033f6e97f6b261271960592a0403c3dc031b288848",
            "INC100010": "e1fca7e4dbfc17c40484f628366f2dfe334de4c2988a5b03fbf86ea38d50f6f2",
            "INC100011": "2f4504b3297e0c1dfebfd3198e8d34798b6c60da21b41f871cc726ce7a5be209",
            "INC100012": "0ea1dba7332cae9826ee9e7ffb64dee6df3c5b837805845a11af4819e8d5e2f3",
            "INC100013": "be0fc73bc79720fc9ab544688f3c792c8adf48c33f44e4f37fda28cb5f68497b",
            "INC100014": "ba186a421250d355b8f357930db9a36c54b42c142c19d7682863f54e1edce728",
            "INC100015": "80ceb43fee862c2d3cdada0bc1a8828ca6b306d4b976f820454d22492ff02ba0",
            "INC100016": "3fdfc7f2c7b13946413205bfe58c9f314a1c7a1aa8b7de5c3b892527d4c65389",
            "INC100017": "fa8cc6701ee903428921429dc668c4fee19e8682496df4f40bd2e50f93befac3",
            "INC100018": "bbdd1cde72b10d497921d8edb2e6ea84c30aec97cfee17fcd856fc4dac8c34c9",
            "INC100019": "821d4e39680cb4cd0db44cf1d342abdfb9587df58130b4aa411794b05d349784",
            "INC100020": "ca14d7e1f5b8929132768703441818052bd937dc4e6a35f3f12e35ce949c1119",
            "INC100021": "25294cc33302ad973528e645a1794e649f0772806d8cb63d4aad8ee710fa86c4",
            "INC100022": "34d315f9374f65fede8ff87b233c5cba9cf561d6691b361738689e38fe9a329e",
            "INC100023": "12edca2a5c2fd798d84cee3ecfb2c3564a9544013f76f2c9aab3274cdbb04308",
            "INC100024": "2dc936d4f59ab4ae62a0d2de6c796f484c13c20574df04a183338a637922ba51",
            "INC100025": "3a66370fe3c971173f6eb8b21724f4c2093dd89700e248bd165fcf82e73df68b",
        }
        current = {ticket["incident_number"]: ticket for ticket in TICKETS}
        fields = ("priority", "assigned_group", "assigned_to", "sla", "time", "user_information")
        self.assertEqual(set(current), set(original_service_field_digests))
        for incident_number, expected_digest in original_service_field_digests.items():
            new = current[incident_number]
            service_fields = {field: new.get(field) for field in fields}
            serialized = json.dumps(service_fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            actual_digest = hashlib.sha256(serialized.encode()).hexdigest()
            self.assertEqual(actual_digest, expected_digest, incident_number)
            # Status remains intentionally mutable through the approved UAT workflow.
            self.assertIn(new.get("status"), {1, 2, 3, 4, 5, 6}, incident_number)

    def test_catalogue_foreign_keys_and_roles_match_exact_configured_ids(self):
        systems = {item["source_system"]: item for item in SYSTEMS}
        roles = {item["source_system"]: item for item in ROLES}
        docs = {item["document_id"]: item for item in DOCS}
        self.assertEqual(set(systems), set(roles))
        self.assertTrue(all(item["source_status"] == "synthetic_draft" for item in DOCS))
        for ticket in TICKETS:
            system = systems[ticket["source_system"]]
            catalogue = roles[ticket["source_system"]]
            self.assertEqual(ticket["gateway_role_id"], catalogue["gateway_roles"][0]["role_id"])
            self.assertEqual(ticket["backend_role_id"], catalogue["backend_roles"][0]["role_id"])
            self.assertIn(ticket["gateway_role_id"], system["configured_gateway_role_ids"])
            self.assertIn(ticket["backend_role_id"], system["configured_backend_role_ids"])
            linked = docs[ticket["linked_kb_document_ids"][0]]
            self.assertEqual((linked["source_system"], linked["issue_type"]), (ticket["source_system"], ticket["issue_type"]))

    def test_scenario_match_uses_context_signature_and_source_not_incident_id(self):
        for ticket in TICKETS:
            api_ticket = server.legacy_ticket_view(ticket)
            matches = match_scenarios(api_ticket)
            self.assertEqual(len(matches), 1, ticket["incident_number"])
            self.assertEqual(matches[0]["source_system"], ticket["source_system"])
            self.assertEqual(matches[0]["issue_type"], ticket["issue_type"])
            changed = dict(api_ticket, incident_number="INC999999")
            self.assertEqual(match_scenarios(changed)[0]["scenario_id"], matches[0]["scenario_id"])
            changed["error_message"] = "Unrelated reported message"
            changed["test_detail"] = "Unrelated reported message"
            changed["test"] = "Unrelated reported message"
            changed["legacy_fields"] = dict(changed.get("legacy_fields", {}), test_detail="Unrelated reported message", test="Unrelated reported message")
            self.assertEqual(match_scenarios(changed), [])

    def test_gateway_and_backend_role_scenarios_are_distinguished(self):
        by_id = {t["incident_number"]: t for t in TICKETS}
        for inc, expected in (("INC100001", "Gateway"), ("INC100002", "Backend"),
                              ("INC100007", "Gateway"), ("INC100008", "Backend")):
            ticket = by_id[inc]
            self.assertEqual(ticket["expected_missing_role_type"], expected)
            result = collect_ticket_support_evidence(server.legacy_ticket_view(ticket))
            role_catalogue = result["role_catalogue"]
            self.assertTrue(role_catalogue["gateway_roles"])
            self.assertTrue(role_catalogue["backend_roles"])
            self.assertFalse(result["scenario"]["root_cause"]["verified"])

    def test_operational_records_use_berlin_timestamps_and_no_stale_ongoing_claim(self):
        data_root = json.loads((ROOT / "data" / "operational_incidents.json").read_text())
        sim = datetime.fromisoformat(data_root["simulation_timestamp"])
        self.assertEqual(data_root["simulation_timezone"], "Europe/Berlin")
        self.assertEqual(len(OPERATIONS), 6)
        for record in OPERATIONS:
            self.assertEqual(record["timezone"], "Europe/Berlin")
            start = datetime.fromisoformat(record["scheduled_start_at"])
            end = datetime.fromisoformat(record["scheduled_end_at"])
            self.assertEqual(start.tzinfo, end.tzinfo)
            self.assertLess(start, end)
        stale_ticket = server.legacy_ticket_view(next(t for t in TICKETS if t["incident_number"] == "INC100009"))
        evidence = collect_ticket_support_evidence(stale_ticket)
        incident = evidence["operational_incident"]
        self.assertGreater(sim, datetime.fromisoformat(incident["expected_recovery_at"]))
        self.assertEqual(evidence["operational_incident_state"], "status_unconfirmed")
        self.assertNotEqual(evidence["operational_incident_state"], "Ongoing")

    def test_substitution_and_language_use_source_workflow_catalogues(self):
        tickets = {t["incident_number"]: t for t in TICKETS}
        hress = collect_ticket_support_evidence(server.legacy_ticket_view(tickets["INC100015"]))
        self.assertFalse(hress["workflow_capability"]["supports_substitution"])
        btravel = collect_ticket_support_evidence(server.legacy_ticket_view(tickets["INC100020"]))
        self.assertNotIn("English", btravel["supported_task_content_languages"])
        self.assertIn("Chinese", btravel["supported_task_content_languages"])
        self.assertTrue(tickets["INC100020"]["source_task_content_language_only"])

    def test_all_tickets_produce_structured_human_support_without_mutation(self):
        with patch.object(llm_agent, "get_ticket", side_effect=lambda inc: server.legacy_ticket_view(next(t for t in TICKETS if t["incident_number"] == inc))), \
             patch.object(llm_agent.requests, "patch") as update:
            results = [llm_agent.run_investigation(ticket["incident_number"], use_llm=False) for ticket in TICKETS]
        self.assertEqual(len(results), 25)
        for result in results:
            self.assertIsNone(result["error"])
            self.assertEqual(result["resolution_path"], "HUMAN_SUPPORT")
            resolution_records = data("resolution_records.json", "verified_resolutions")
            has_verified_resolution = any(
                row.get("incident_number") == result["ticket"]["incident_number"] and row.get("verified") is True
                for row in resolution_records
            )
            self.assertEqual(
                result["root_cause_assessment"]["state"],
                "consultant_verified" if has_verified_resolution else "scenario_expected_unverified",
            )
            self.assertTrue(result["recommended_troubleshooting_steps"])
            self.assertTrue(result["verification_steps"])
            issue = result["ticket"]["issue_type"]
            domain = result["unified_inbox_evidence"]
            if issue == "Task Not Visible":
                self.assertTrue(result["evidence"]["role_catalogue_validation"]["configured_gateway_roles"])
                self.assertTrue(result["evidence"]["role_catalogue_validation"]["configured_backend_roles"])
            elif issue == "Loading Time Too Slow":
                self.assertTrue(domain["operational_incident"])
                self.assertEqual(domain["operational_incident"]["timezone"], "Europe/Berlin")
            elif issue == "Cannot Set Up Substitution":
                self.assertIs(domain["workflow_capability"]["supports_substitution"], False)
            elif issue == "Cannot See Specific Language":
                self.assertNotIn(result["ticket"]["requested_language"], domain["supported_task_content_languages"])
        update.assert_not_called()

    def test_resolution_and_approved_knowledge_stores_are_not_reclassified(self):
        resolutions = data("resolution_records.json", "verified_resolutions")
        articles = data("knowledge_articles.json", "articles")
        ticket_ids = {ticket["incident_number"] for ticket in TICKETS}
        resolution_ids = {record["resolution_id"] for record in resolutions}
        self.assertEqual(len(resolution_ids), len(resolutions))
        for record in resolutions:
            self.assertTrue(record["verified"])
            self.assertIn(record["incident_number"], ticket_ids)
            self.assertTrue(record["confirmed_root_cause"].strip())
            self.assertTrue(record["actions_performed"])
            self.assertTrue(record["verification_evidence"].strip())
        self.assertEqual(len({article["article_id"] for article in articles}), len(articles))
        for article in articles:
            self.assertIn(article["review_status"], {"Draft", "Approved"})
            self.assertIn(article["source_incident"], ticket_ids)
            self.assertIn(article["source_resolution_id"], resolution_ids)

    def test_mock_api_exposes_ticket_and_operational_incidents(self):
        client = server.app.test_client()
        ticket = client.get("/api/tickets/INC100001")
        self.assertEqual(ticket.status_code, 200)
        self.assertEqual(ticket.json["source_system"], "PM7")
        self.assertEqual(ticket.json["issue"], "Task Not Visible")
        operations = client.get("/api/operational-incidents", query_string={"source_system": "PM7"})
        self.assertEqual(operations.status_code, 200)
        self.assertEqual(operations.json["count"], 1)
        self.assertEqual(operations.json["simulation_timezone"], "Europe/Berlin")

    def test_status_update_endpoint_contract_remains_available(self):
        client = server.app.test_client()
        response = client.patch("/api/tickets/INC100001", json={"status": "In Progress"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("confirmation", response.json["error"].lower())


if __name__ == "__main__":
    unittest.main()
