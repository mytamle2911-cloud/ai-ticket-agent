"""Hosted shared-code authentication and live-AI access checks."""

import os
import unittest
from unittest.mock import patch

from agent import demo_auth, demo_state, llm_agent
from mock_api import server


AUTH_ENV = {
    "VERCEL": "1",
    "DEMO_ACCESS_CODE": "academic-demo-code-123456",
    "DEMO_AUTH_SECRET": "test-auth-secret-with-at-least-32-chars",
    "DEMO_STATE_SECRET": "test-state-secret-with-at-least-32-characters",
    "ENABLE_LIVE_AI": "true",
    "OPENAI_API_KEY": "unit-test-key-not-real",
}


class DemoAuthTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, AUTH_ENV, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = server.app.test_client()

    def login(self):
        return self.client.post("/api/auth/login", json={"access_code": AUTH_ENV["DEMO_ACCESS_CODE"]})

    def dispatch(self, path, state=None, method="GET", payload=None):
        envelope = {"path": path, "method": method}
        if state is not None:
            envelope["state"] = state
        if payload is not None:
            envelope["payload"] = payload
        return self.client.post("/api/demo/dispatch", json=envelope)

    def test_unauthenticated_direct_and_dispatch_requests_are_rejected(self):
        self.assertEqual(self.client.get("/api/tickets").status_code, 401)
        self.assertEqual(self.dispatch("/api/tickets").status_code, 401)
        protected_model_routes = (
            ("/api/tickets/INC100001/investigation", {}),
            ("/api/tickets/INC100001/chat", {"message": "Troubleshoot this ticket", "history": []}),
            ("/api/tickets/INC100001/customer-email-draft", {}),
            ("/api/tickets/INC100001/knowledge-articles/generate", {}),
        )
        for path, payload in protected_model_routes:
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json=payload).status_code, 401)

    def test_invalid_code_is_rejected_and_valid_login_sets_secure_httponly_cookie(self):
        bad = self.client.post("/api/auth/login", json={"access_code": "wrong"})
        self.assertEqual(bad.status_code, 401)
        self.assertFalse(bad.get_json()["authenticated"] if "authenticated" in bad.get_json() else False)

        good = self.login()
        self.assertEqual(good.status_code, 200)
        cookie = next(value for key, value in good.headers if key.lower() == "set-cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertEqual(self.client.get("/api/auth/session").get_json()["authenticated"], True)

    def test_signed_state_does_not_replace_auth_and_direct_api_does_not_bypass_state(self):
        token = demo_state.encode_state(demo_state.initial_state())
        # A valid state token without a session is not authentication.
        anonymous = server.app.test_client()
        response = anonymous.post("/api/demo/dispatch", json={
            "path": "/api/tickets", "method": "GET", "state": token,
        })
        self.assertEqual(response.status_code, 401)

        self.login()
        self.assertEqual(self.client.get("/api/tickets").status_code, 400)
        routed = self.dispatch("/api/tickets")
        self.assertEqual(routed.status_code, 200)
        self.assertEqual(routed.get_json()["status_code"], 200)

    def test_cookie_expiry_and_logout_remove_access(self):
        self.client.set_cookie(demo_auth.COOKIE_NAME, demo_auth.issue_session(now=100))
        expired = self.client.get("/api/tickets")
        self.assertEqual(expired.status_code, 401)
        self.login()
        logout = self.client.post("/api/auth/logout", json={})
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/api/tickets").status_code, 401)

    def test_tampered_session_cookie_is_rejected(self):
        self.login()
        token = demo_auth.issue_session()
        self.client.set_cookie(demo_auth.COOKIE_NAME, token[:-1] + ("0" if token[-1] != "0" else "1"))
        self.assertEqual(self.client.get("/api/tickets").status_code, 401)

    def test_missing_auth_configuration_fails_closed(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            self.assertEqual(self.client.get("/api/auth/session").status_code, 503)
            self.assertEqual(self.client.post("/api/auth/login", json={"access_code": "anything"}).status_code, 503)

    def test_missing_state_secret_is_rejected_before_protected_route_execution(self):
        self.login()
        state = demo_state.encode_state(demo_state.initial_state())
        with patch.dict(os.environ, {"DEMO_STATE_SECRET": ""}), \
             patch.object(server.chat_agent, "chat_with_incident") as model_route:
            response = self.dispatch("/api/tickets/INC100001/investigation", method="POST", payload={}, state=state)
        self.assertEqual(response.status_code, 503)
        model_route.assert_not_called()

    def test_openai_client_requires_auth_and_explicit_live_flag(self):
        with patch.object(llm_agent, "client", None), server.app.test_request_context("/api/test"):
            with self.assertRaisesRegex(RuntimeError, "Sign in"):
                llm_agent._get_client()
        with patch.dict(os.environ, {"ENABLE_LIVE_AI": "false"}), \
             patch.object(llm_agent, "client", None), server.app.test_request_context("/api/test"), \
             demo_auth.trusted_dispatch():
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                llm_agent._get_client()

    def test_missing_model_key_and_invalid_limits_fail_closed(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), \
             patch.object(llm_agent, "client", None), server.app.test_request_context("/api/test"), \
             demo_auth.trusted_dispatch():
            with self.assertRaisesRegex(RuntimeError, "API key is missing"):
                llm_agent._get_client()
        with patch.dict(os.environ, {"OPENAI_TIMEOUT_SECONDS": "120"}):
            with self.assertRaisesRegex(RuntimeError, "outside supported bounds"):
                llm_agent._model_request_limits()

    def test_dispatch_limits_model_payload_size_before_invoking_route(self):
        self.login()
        result = self.dispatch("/api/tickets/INC100001/chat", method="POST", payload={
            "message": "x" * (97 * 1024), "history": [],
        }, state=demo_state.encode_state(demo_state.initial_state()))
        self.assertEqual(result.status_code, 413)
        self.assertIn("too large", result.get_json()["error"].lower())

    def test_dispatch_returns_outer_success_while_chat_rejects_assistant_metadata(self):
        self.login()
        state = demo_state.encode_state(demo_state.initial_state())
        response = self.dispatch(
            "/api/tickets/INC100004/chat", state=state, method="POST", payload={
                "message": "Set this ticket to On Hold.",
                "history": [
                    {"role": "user", "content": "Troubleshoot this ticket."},
                    {"role": "assistant", "content": "The issue is not resolved.",
                     "ai_provenance": "deterministic_fallback", "model": None},
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        wrapped = response.get_json()
        self.assertEqual(wrapped["status_code"], 400)
        self.assertEqual(
            wrapped["result"]["error"],
            "Chat history is invalid or too long. Start a new message and retry.",
        )

    def test_projected_chat_history_reaches_deterministic_status_confirmation(self):
        self.login()
        state = demo_state.encode_state(demo_state.initial_state())
        response = self.dispatch(
            "/api/tickets/INC100004/chat", state=state, method="POST", payload={
                "message": "Set this ticket to On Hold.",
                "history": [
                    {"role": "assistant", "content": "The issue remains under investigation."},
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        wrapped = response.get_json()
        self.assertEqual(wrapped["status_code"], 200)
        self.assertEqual(wrapped["result"]["status_update"]["state"], "pending_confirmation")


if __name__ == "__main__":
    unittest.main()
