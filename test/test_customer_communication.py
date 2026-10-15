"""Tests for synthetic customer contact suggestions and email drafting."""

import json
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from agent import customer_communication as communication
from agent import llm_agent
from mock_api import server


ROOT = Path(__file__).resolve().parents[1]
CONSULTANT = {
    "location": "Ho Chi Minh City, Vietnam",
    "timezone": "Asia/Ho_Chi_Minh",
    "working_hours_start": "09:00",
    "working_hours_end": "18:00",
}
GERMAN_CUSTOMER = {
    "customer_id": "CUST100006",
    "full_name": "Anna Müller",
    "email": "anna.mueller@example.com",
    "location": "Stuttgart, Germany",
    "timezone": "Europe/Berlin",
    "working_hours_start": "09:00",
    "working_hours_end": "17:00",
    "preferred_language": "English",
}


class CustomerCommunicationTests(unittest.TestCase):
    def setUp(self):
        self.tickets = json.loads((ROOT / "data/tickets.json").read_text())["tickets"]
        self.customers = json.loads((ROOT / "data/customers.json").read_text())["customers"]

    def test_every_existing_ticket_maps_to_a_complete_synthetic_customer(self):
        self.assertEqual(len(self.tickets), 25)
        self.assertEqual(len(self.customers), 25)
        by_id = {customer["customer_id"]: customer for customer in self.customers}
        for ticket in self.tickets:
            self.assertIn(ticket["customer_id"], by_id)
            customer = by_id[ticket["customer_id"]]
            for field in ("full_name", "email", "location", "timezone", "working_hours_start", "working_hours_end", "preferred_language"):
                self.assertTrue(customer[field])
            self.assertTrue(customer["email"].endswith("@example.com"))
        selected = next(ticket for ticket in self.tickets if ticket["incident_number"] == "INC100006")
        self.assertEqual(by_id[selected["customer_id"]]["full_name"], "Anna Müller")
        self.assertEqual(by_id[selected["customer_id"]]["email"], "anna.mueller@example.com")

    def test_missing_customer_reference_is_actionable(self):
        with self.assertRaisesRegex(communication.CustomerCommunicationError, "does not have a customer reference"):
            communication.get_customer_for_ticket({"incident_number": "INC100001"})

    def test_contact_slot_converts_for_winter_and_summer_dst_dates(self):
        winter_now = datetime(2026, 1, 15, 8, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        winter = communication.suggest_contact_time(
            GERMAN_CUSTOMER, CONSULTANT, now=winter_now, selected_date=date(2026, 1, 15),
        )
        summer_now = datetime(2026, 7, 15, 8, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        summer = communication.suggest_contact_time(
            GERMAN_CUSTOMER, CONSULTANT, now=summer_now, selected_date=date(2026, 7, 15),
        )
        self.assertTrue(winter["available"] and summer["available"])
        self.assertEqual(winter["suggested_slot"]["consultant"]["time"], "15:00")
        self.assertEqual(winter["suggested_slot"]["customer"]["time"], "09:00")
        self.assertEqual(summer["suggested_slot"]["consultant"]["time"], "14:00")
        self.assertEqual(summer["suggested_slot"]["customer"]["time"], "09:00")
        self.assertEqual(winter["suggested_slot"]["duration_minutes"], 30)

    def test_no_overlap_is_reported_without_inventing_a_slot(self):
        customer = dict(GERMAN_CUSTOMER, timezone="America/Los_Angeles", location="Los Angeles, United States")
        result = communication.suggest_contact_time(
            customer,
            CONSULTANT,
            now=datetime(2026, 7, 15, 8, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
            selected_date="2026-07-15",
        )
        self.assertFalse(result["overlap_found"])
        self.assertFalse(result["available"])
        self.assertIsNone(result["suggested_slot"])
        self.assertIn("No common working-hours overlap", result["reason"])

    def test_selected_date_and_future_slot_are_respected(self):
        now = datetime(2026, 7, 15, 17, 31, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        result = communication.suggest_contact_time(
            GERMAN_CUSTOMER, CONSULTANT, now=now, selected_date="2026-07-15",
        )
        self.assertEqual(result["selected_date"], "2026-07-15")
        self.assertTrue(result["overlap_found"])
        self.assertFalse(result["available"])
        self.assertIn("No future 30-minute slot", result["reason"])

    def test_mocked_gpt_email_uses_language_and_sanitized_evidence(self):
        fake = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=SimpleNamespace(
            output_text=json.dumps({
                "subject": "Follow-up on INC100006 — SAP SD Approval Task",
                "body": "Dear Anna, could you confirm the affected document ID? Best regards, L2 Support Team",
            }),
        ))))
        ticket = {
            "incident_number": "INC100006",
            "source_system": "SAP SD",
            "issue_type": "Task Not Visible",
            "description": "The approval task is not visible. Required role XX:BACKEND_SD:XX.",
            "error_message": "SYNTH-OPS-9021",
        }
        slot = communication.suggest_contact_time(
            GERMAN_CUSTOMER, CONSULTANT,
            now=datetime(2026, 7, 15, 8, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
            selected_date="2026-07-15",
        )
        with patch.object(communication, "_verified_resolution_for_ticket", return_value=None):
            draft = communication.generate_customer_email_draft(
                ticket, GERMAN_CUSTOMER, slot,
                investigation={
                    "resolution_path": "HUMAN_SUPPORT",
                    "information_gaps": ["Confirm affected document ID; compare XX:BACKEND_SD:XX"],
                    "hypotheses": [{"text": "Possible role issue"}],
                },
                client=fake,
            )
        self.assertEqual(draft["draft_status"], "editable_unverified")
        self.assertEqual(draft["ai_provenance"], "live_gpt")
        self.assertEqual(fake.responses.create.call_args.kwargs["max_output_tokens"], llm_agent.MAX_OUTPUT_TOKENS)
        self.assertEqual(fake.responses.create.call_args.kwargs["model"], llm_agent.MODEL)
        prompt = fake.responses.create.call_args.kwargs["input"]
        self.assertNotIn("XX:BACKEND_SD:XX", prompt)
        self.assertNotIn("SYNTH-OPS-9021", prompt)
        self.assertIn("English", prompt)
        self.assertIn("subject to the customer's availability", fake.responses.create.call_args.kwargs["instructions"])

    def test_access_scenario_and_unverified_resolution_are_not_sent_as_confirmed(self):
        ticket = {"incident_number":"INC100006", "source_system":"SAP SD", "issue_type":"Task Not Visible", "description":"Approval task is not visible."}
        investigation = {
            "ticket": {"incident_number":"INC100006"},
            "root_cause_assessment": {"state":"consultant_verified", "verified":True, "source":"verified_resolution", "statement":"Missing SAP SD Backend Role"},
            "hypotheses": [],
        }
        with patch.object(communication, "_verified_resolution_for_ticket", return_value={
            "verified":True,
            "confirmed_root_cause":"Missing SAP SD Backend Role — XX:BACKEND_SD:XX",
            "actions_performed":["Request XX:BACKEND_SD:XX"],
            "verification_evidence":"Missing SAP SD backend role",
        }):
            context = communication._email_prompt_context(ticket, GERMAN_CUSTOMER, {"available":False}, investigation, communication._verified_resolution_for_ticket("INC100006"))
        self.assertFalse(context["consultant_confirmed_resolution_available"])
        self.assertIsNone(context["consultant_confirmed_root_cause"])
        self.assertFalse(context["authorization_assignment_verified"])
        self.assertIn("possible cause", context["investigation_hypotheses_unverified"][0]["text"])

    def test_generated_access_email_rephrases_unverified_missing_authorization_claim(self):
        output = SimpleNamespace(output_text=json.dumps({
            "subject":"Follow-up INC100006",
            "body":"Our review verified that required authorization was missing. Please confirm the affected document ID.",
        }))
        fake = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=output)))
        with patch.object(communication, "_verified_resolution_for_ticket", return_value={"verified":True, "verification_evidence":"Missing role"}):
            draft = communication.generate_customer_email_draft(
                {"incident_number":"INC100006", "issue_type":"Task Not Visible"}, GERMAN_CUSTOMER,
                {"available":False, "suggested_slot":None}, investigation={"ticket":{"incident_number":"INC100006"}}, client=fake,
            )
        self.assertNotIn("verified that required authorization was missing", draft["body"].lower())
        self.assertIn("One possible cause is an authorization issue", draft["body"])

    def test_gpt_failure_is_an_explicit_error_not_a_successful_fallback(self):
        fake = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=TimeoutError("private request details"))))
        with patch.object(communication, "_verified_resolution_for_ticket", return_value=None), self.assertRaises(
            communication.CustomerCommunicationError,
        ) as raised:
            communication.generate_customer_email_draft(
                {"incident_number": "INC100006", "description": "Task issue"},
                GERMAN_CUSTOMER,
                {"available": False, "suggested_slot": None},
                client=fake,
            )
        self.assertIn("generation failed", str(raised.exception))
        self.assertNotIn("private request details", str(raised.exception))


class CustomerCommunicationApiTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_ticket_customer_endpoint_returns_profile_and_contact_slot(self):
        response = self.client.get("/api/tickets/INC100006/customer-communication?date=2026-07-15")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["customer"]["customer_id"], "CUST100006")
        self.assertEqual(payload["customer"]["full_name"], "Anna Müller")
        self.assertEqual(payload["customer"]["timezone"], "Europe/Berlin")
        self.assertEqual(payload["consultant"]["timezone"], "Asia/Ho_Chi_Minh")
        self.assertEqual(payload["contact_time"]["selected_date"], "2026-07-15")

    def test_email_draft_route_calls_mocked_gpt_and_returns_editable_fields(self):
        fake = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=SimpleNamespace(
            output_text='{"subject":"Follow-up on INC100006","body":"Dear Anna, please confirm the affected document ID."}',
        ))))
        with patch.object(communication, "_verified_resolution_for_ticket", return_value=None), patch.object(
            llm_agent, "_get_client", return_value=fake,
        ):
            response = self.client.post(
                "/api/tickets/INC100006/customer-email-draft",
                json={"investigation": {"ticket":{"incident_number":"INC100006"}, "resolution_path": "HUMAN_SUPPORT"}, "date": "2026-07-15"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["subject"], "Follow-up on INC100006")
        self.assertEqual(response.get_json()["draft_status"], "editable_unverified")
        fake.responses.create.assert_called_once()

    def test_email_draft_route_rejects_investigation_from_another_ticket(self):
        response = self.client.post(
            "/api/tickets/INC100006/customer-email-draft",
            json={"investigation":{"ticket":{"incident_number":"INC100001"}}},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("belong to the selected ticket", response.get_json()["error"])

    def test_email_draft_route_requires_a_completed_investigation(self):
        response = self.client.post(
            "/api/tickets/INC100006/customer-email-draft",
            json={"investigation":{"ticket":{"incident_number":"INC100006"}}},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("completed investigation", response.get_json()["error"])

    def test_invalid_customer_date_and_missing_ticket_are_rejected(self):
        invalid_date = self.client.get("/api/tickets/INC100006/customer-communication?date=not-a-date")
        missing_ticket = self.client.get("/api/tickets/INC999999/customer-communication")
        self.assertEqual(invalid_date.status_code, 503)
        self.assertEqual(missing_ticket.status_code, 404)


if __name__ == "__main__":
    unittest.main()
