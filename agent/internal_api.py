"""In-process calls to the Flask API for agent-to-service lookups.

The agent and API share one deployment process. Dispatching through Flask's WSGI
test client keeps the public route contract and avoids localhost HTTP calls, which
cannot reach the current function instance on serverless hosts.
"""

from __future__ import annotations

from typing import Any

import requests


class InternalResponse:
    """Small requests-compatible response wrapper for internal WSGI dispatch."""

    def __init__(self, response: Any):
        self._response = response
        self.status_code = response.status_code

    def json(self) -> Any:
        return self._response.get_json(silent=True)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"Internal API request returned HTTP {self.status_code}.",
                response=self,
            )


def request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
) -> InternalResponse:
    """Dispatch an API request through the same Flask app without network I/O."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Internal API paths must start with '/'.")

    # Import lazily to avoid the server -> agent -> internal_api import cycle.
    from mock_api.server import app

    with app.test_client() as client:
        response = client.open(
            path,
            method=method.upper(),
            query_string=params,
            json=json,
        )
    return InternalResponse(response)


def get(path: str, *, params: dict | None = None, timeout: float | None = None) -> InternalResponse:
    del timeout  # Retained for compatibility with the previous requests call sites.
    return request("GET", path, params=params)


def post(path: str, *, json: dict | None = None, timeout: float | None = None) -> InternalResponse:
    del timeout
    return request("POST", path, json=json)


def patch(path: str, *, json: dict | None = None, timeout: float | None = None) -> InternalResponse:
    del timeout
    return request("PATCH", path, json=json)
