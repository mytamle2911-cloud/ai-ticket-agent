import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import internal_api, llm_agent, demo_auth
from api.index import app as vercel_app


ROOT = Path(__file__).resolve().parents[1]


class VercelDeploymentTests(unittest.TestCase):
    def test_vercel_build_routes_to_frontend_bundle_and_python_function(self):
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        self.assertEqual(config["buildCommand"], "npm --prefix frontend run build")
        self.assertEqual(config["outputDirectory"], "frontend/dist")
        self.assertIn("api/index.py", config["functions"])
        self.assertEqual(config["functions"]["api/index.py"]["maxDuration"], 60)
        self.assertEqual(config["functions"]["api/index.py"]["includeFiles"], "data/*.json")
        self.assertEqual(config["rewrites"][0], {
            "source": "/api/:path*", "destination": "/api/index.py",
        })

        entrypoint = (ROOT / "api/index.py").read_text(encoding="utf-8")
        self.assertIn("from mock_api.server import app", entrypoint)

    def test_internal_flask_call_retrieves_ticket_without_localhost_network(self):
        response = internal_api.get("/api/tickets/INC100001")
        response.raise_for_status()
        ticket = response.json()
        self.assertEqual(ticket["incident_number"], "INC100001")

    def test_function_entrypoint_exposes_existing_api_routes(self):
        client = vercel_app.test_client()
        self.assertEqual(client.get("/health").status_code, 200)
        response = client.get("/api/tickets")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["tickets"])

    def test_agent_modules_have_no_localhost_http_self_calls(self):
        paths = (
            ROOT / "agent/llm_agent.py",
            ROOT / "agent/captured_knowledge.py",
            ROOT / "agent/knowledge_capture.py",
        )
        for path in paths:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("127.0.0.1:5000", text)

    def test_live_llm_requires_valid_session_and_explicit_enablement(self):
        env = {
            "VERCEL": "1", "OPENAI_API_KEY": "test-only",
            "DEMO_ACCESS_CODE": "academic-demo-code-123456",
            "DEMO_AUTH_SECRET": "test-auth-secret-with-at-least-32-chars",
            "ENABLE_LIVE_AI": "true",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(llm_agent, "client", None):
            with vercel_app.test_request_context("/api/test"):
                with self.assertRaisesRegex(RuntimeError, "Sign in"):
                    llm_agent._get_client()
            with vercel_app.test_request_context("/api/test"):
                with demo_auth.trusted_dispatch(), patch.object(llm_agent, "OpenAI", return_value="client") as factory:
                    self.assertEqual(llm_agent._get_client(), "client")
                    factory.assert_called_once_with(api_key="test-only", timeout=20, max_retries=0)

    def test_vercel_openai_timeouts_and_investigation_rounds_fit_function_budget(self):
        with patch.dict(os.environ, {"VERCEL": "1", "OPENAI_TIMEOUT_SECONDS": "45"}):
            self.assertEqual(llm_agent._model_request_limits(), (800, 20))
            self.assertEqual(llm_agent._model_iteration_limit(), 2)
        with patch.dict(os.environ, {"VERCEL": "", "OPENAI_TIMEOUT_SECONDS": "45"}):
            self.assertEqual(llm_agent._model_request_limits(), (800, 45))
            self.assertEqual(llm_agent._model_iteration_limit(), llm_agent.MAX_ITERATIONS)

    def test_live_llm_disabled_flag_fails_closed(self):
        env = {
            "VERCEL": "1", "OPENAI_API_KEY": "test-only",
            "DEMO_ACCESS_CODE": "academic-demo-code-123456",
            "DEMO_AUTH_SECRET": "test-auth-secret-with-at-least-32-chars",
            "ENABLE_LIVE_AI": "false",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(llm_agent, "client", None), \
             vercel_app.test_request_context("/api/test"), demo_auth.trusted_dispatch():
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                llm_agent._get_client()


if __name__ == "__main__":
    unittest.main()
