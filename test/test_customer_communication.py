"""Tests for synthetic customer contact suggestions and email drafting."""

import json
import unittest
from datetime import date, datetime, timedelta, timezone
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
        self.assertTrue(result["available"])
        self.assertIsNone(result["reason"])
        self.assertEqual(result["suggested_slot"]["consultant"]["date"], "2026-07-16")
        self.assertEqual(result["suggested_slot"]["consultant"]["time"], "14:00")

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


class ContactSchedulingRegressionTests(unittest.TestCase):
    def suggest(self, now, **kwargs):
        return communication.suggest_contact_time(
            dict(GERMAN_CUSTOMER, location="Munich, Germany"), CONSULTANT, now=now, **kwargs,
        )

    def test_remaining_overlap_today_is_full_thirty_minutes_in_both_zones(self):
        result = self.suggest(datetime(2026, 7, 15, 15, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))
        slot = result["suggested_slot"]
        self.assertEqual(slot["consultant"]["date"], "2026-07-15")
        self.assertEqual((slot["consultant"]["time"], slot["consultant"]["end_time"]), ("15:30", "16:00"))
        self.assertEqual((slot["customer"]["time"], slot["customer"]["end_time"]), ("10:30", "11:00"))
        self.assertEqual(slot["customer"]["utc_offset"], "UTC+02:00")
        self.assertEqual(slot["consultant"]["utc_offset"], "UTC+07:00")
        self.assertEqual(datetime.fromisoformat(slot["end_utc"].replace("Z", "+00:00")) - datetime.fromisoformat(slot["start_utc"].replace("Z", "+00:00")), timedelta(minutes=30))
        self.assertFalse(slot["is_confirmed_booking"])

    def test_last_full_slot_is_allowed_but_not_a_partial_slot(self):
        before = self.suggest(datetime(2026, 7, 15, 17, 30, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))
        after = self.suggest(datetime(2026, 7, 15, 17, 30, 1, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))
        self.assertEqual(before["suggested_slot"]["consultant"]["end_time"], "18:00")
        self.assertEqual(after["suggested_slot"]["consultant"]["date"], "2026-07-16")
        self.assertEqual(after["selected_date"], "2026-07-15")

    def test_after_friday_overlap_searches_monday_skipping_both_weekends(self):
        for day in (17, 18, 19):
            result = self.suggest(datetime(2026, 7, day, 18, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))
            self.assertEqual(result["suggested_slot"]["consultant"]["date"], "2026-07-20")
            self.assertEqual(result["suggested_slot"]["customer"]["date"], "2026-07-20")

    def test_berlin_dst_transition_weekends_search_forward_with_actual_new_offset(self):
        for selected, expected_day, vn_time, offset in [
            ("2026-03-27", "2026-03-30", "14:00", "UTC+02:00"),
            ("2026-10-23", "2026-10-26", "15:00", "UTC+01:00"),
        ]:
            now = datetime.fromisoformat(selected + "T18:00:00").replace(tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
            slot = self.suggest(now)["suggested_slot"]
            self.assertEqual(slot["consultant"]["date"], expected_day)
            self.assertEqual(slot["consultant"]["time"], vn_time)
            self.assertEqual(slot["customer"]["time"], "09:00")
            self.assertEqual(slot["customer"]["utc_offset"], offset)

    def test_no_overlap_any_day_within_horizon_never_invents_a_slot(self):
        customer = dict(GERMAN_CUSTOMER, timezone="America/Los_Angeles")
        result = communication.suggest_contact_time(customer, CONSULTANT,
            now=datetime(2026, 7, 15, 8, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")), search_horizon_days=7)
        self.assertFalse(result["available"])
        self.assertIsNone(result["suggested_slot"])
        self.assertFalse(result["overlap_found"])
        self.assertEqual(result["search_end_date"], "2026-07-21")
        self.assertIn("7-calendar-day search horizon", result["reason"])

    def test_calendar_day_horizon_is_bounded_and_configurable_without_changing_hours(self):
        now = datetime(2026, 7, 17, 17, 31, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        bounded = self.suggest(now, search_horizon_days=3)
        self.assertFalse(bounded["available"])
        self.assertEqual(bounded["searched_days"], 3)
        self.assertIn("No future 30-minute slot", bounded["reason"])
        longer = communication.suggest_contact_time(GERMAN_CUSTOMER, dict(CONSULTANT, contact_search_horizon_days=4), now=now)
        self.assertEqual(longer["suggested_slot"]["customer"]["date"], "2026-07-20")
        for invalid in (0, 367, True, "14"):
            with self.assertRaises(communication.CustomerCommunicationError):
                self.suggest(now, search_horizon_days=invalid)

    def test_default_clock_and_selected_date_ignore_server_timezone(self):
        # UTC server date is July 14; Vietnam's working day is already July 15.
        with patch.object(communication, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 7, 14, 23, 30, tzinfo=timezone.utc)
            result = communication.suggest_contact_time(GERMAN_CUSTOMER, CONSULTANT)
            clock.now.assert_called_once_with(timezone.utc)
        self.assertEqual(result["selected_date"], "2026-07-15")
        self.assertEqual(result["suggested_slot"]["consultant"]["date"], "2026-07-15")
        now = datetime(2026, 7, 14, 16, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        self.assertEqual(self.suggest(now)["suggested_slot"], result["suggested_slot"])

    def test_past_selected_date_starts_search_from_today_and_future_date_is_respected(self):
        now = datetime(2026, 7, 15, 8, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        past = self.suggest(now, selected_date="2026-01-01")
        self.assertEqual(past["search_start_date"], "2026-07-15")
        self.assertEqual(past["suggested_slot"]["customer"]["date"], "2026-07-15")
        future = self.suggest(now, selected_date="2026-07-21")
        self.assertEqual(future["suggested_slot"]["consultant"]["date"], "2026-07-21")

    def test_local_dates_are_preserved_when_overlap_spans_different_calendar_dates(self):
        customer = dict(GERMAN_CUSTOMER, timezone="Pacific/Honolulu")
        result = communication.suggest_contact_time(customer, CONSULTANT,
            now=datetime(2026, 7, 14, 7, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))
        slot = result["suggested_slot"]
        self.assertEqual((slot["consultant"]["date"], slot["consultant"]["time"]), ("2026-07-14", "09:00"))
        self.assertEqual((slot["customer"]["date"], slot["customer"]["time"]), ("2026-07-13", "16:00"))
        self.assertEqual(slot["customer"]["end_date"], "2026-07-13")

    def test_mocked_email_receives_the_deterministic_future_slot_with_both_dates(self):
        contact = self.suggest(datetime(2026, 7, 17, 18, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))
        fake = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=SimpleNamespace(
            output_text='{"subject":"Follow-up","body":"Please confirm your availability."}',
        ))))
        ticket = {"incident_number":"INC100006", "issue_type":"Performance"}
        with patch.object(communication, "_verified_resolution_for_ticket", return_value=None):
            communication.generate_customer_email_draft(ticket, GERMAN_CUSTOMER, contact, investigation={}, client=fake)
        prompt = json.loads(fake.responses.create.call_args.kwargs["input"])
        self.assertEqual(prompt["suggested_contact_slot"]["consultant"]["date"], "2026-07-20")
        self.assertEqual(prompt["suggested_contact_slot"]["customer"]["time"], "09:00")


    def test_customer_route_and_email_route_share_forward_searched_backend_slot(self):
        fake = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=SimpleNamespace(
            output_text='{"subject":"Follow-up","body":"Please confirm availability."}',
        ))))
        client = server.app.test_client()
        with patch.object(communication, "datetime", wraps=datetime) as clock, patch.object(
            llm_agent, "_get_client", return_value=fake,
        ), patch.object(communication, "_verified_resolution_for_ticket", return_value=None):
            clock.now.return_value = datetime(2026, 7, 17, 17, 31, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
            contact = client.get("/api/tickets/INC100006/customer-communication?date=2026-07-17")
            email = client.post("/api/tickets/INC100006/customer-email-draft", json={
                "date": "2026-07-17", "investigation": {
                    "ticket": {"incident_number": "INC100006"}, "resolution_path": "HUMAN_SUPPORT",
                },
            })
        self.assertEqual(contact.status_code, 200)
        self.assertEqual(email.status_code, 200)
        slot = contact.get_json()["contact_time"]["suggested_slot"]
        self.assertEqual(slot["customer"]["date"], "2026-07-20")
        self.assertEqual(slot["consultant"]["time"], "14:00")
        self.assertEqual(slot["customer"]["end_time"], "09:30")
        self.assertEqual(email.get_json()["contact_time"]["suggested_slot"], slot)
        fake.responses.create.assert_called_once()

    def test_slot_does_not_extend_beyond_a_configured_subminute_closing_time(self):
        consultant = dict(CONSULTANT, working_hours_end="17:59:30")
        result = communication.suggest_contact_time(GERMAN_CUSTOMER, consultant,
            now=datetime(2026, 7, 15, 17, 30, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))
        self.assertEqual(result["suggested_slot"]["consultant"]["date"], "2026-07-16")
