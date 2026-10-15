# Ticket Agent — AI-Powered L2 Troubleshooting & Knowledge Management

A local synthetic prototype for L2 support consultants. The React workspace
uses the existing Flask Mock ServiceNow API and Python Agent for evidence-led
troubleshooting, consultant-verified resolution capture, and reviewed knowledge
reuse.

## Architecture

- `frontend/` is a React/Vite interface. It lists incidents, starts ticket-aware
  troubleshooting and chat, captures consultant-confirmed resolutions, and
  supports article review and approval.
- `mock_api/server.py` is a local Flask API. It serves ticket list, lookup,
  health-check, and update endpoints. Ticket updates are persisted to
  `data/tickets.json`.
- `agent/llm_agent.py` retrieves tickets through the API, searches
  `data/product_docs.json`, checks proposed roles against `data/roles.json`,
  reads scenario and source-system evidence from `data/troubleshooting_scenarios.json`
  and `data/systems.json`, and checks time-bounded operational records from
  `data/operational_incidents.json`. It calls the OpenAI Responses API for
  candidate analysis. Status changes are
  available only after explicit approval in the UI or CLI flow. Evidence status
  is calculated from the retrieved ticket, matching local documents, and role
  catalogue rather than phrases in generated analysis.
- `agent/agent.py` is a command-line entry point for the agent's interactive
  investigation flow.
- `tools/knowledge_tool.py` provides a standalone local product-documentation
  search helper.

The React development server proxies `/api` requests to the Flask API at
`http://127.0.0.1:5000`. The Agent and JSON stores remain Python-side.

## Prerequisites

- Python 3.10 or newer
- `pip`
- Node.js 20.19+ (or 22.12+) and npm for the React frontend.
- An OpenAI API key for AI troubleshooting, chat, and knowledge draft writing.
  Ticket browsing, deterministic retrieval and resolution/article APIs remain
  local; AI-generated content needs a configured key and network access.

## Set up the environment

From the repository root, create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell, activate it with:

```powershell
.venv\Scripts\Activate.ps1
```

Install the runtime dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Python dependencies cover Flask, OpenAI, python-dotenv, and Requests.

## Configure the OpenAI credential

`agent/llm_agent.py` calls `load_dotenv()` and creates an OpenAI client with
`OpenAI()`. The SDK reads the credential from the `OPENAI_API_KEY` environment
variable. Configure it in your shell or in a local `.env` file in the
repository root. The `.env` file is for local use only; never put credential
values in this README or commit them to version control.

For the current shell session, set the variable using your operating system's
usual secure method. For example, in macOS/Linux shells:

```bash
export OPENAI_API_KEY="<your-key>"
```

Replace the placeholder locally; do not paste a real key into shared terminal
logs, screenshots, documentation, or source control. To use `.env`, add a line
named `OPENAI_API_KEY` with your key as its value. Keep that file private.

## Run the services

Use two terminals, both opened at the repository root. Activate the virtual
environment in each terminal.

**Terminal 1 — Flask mock API:**

```bash
python -m mock_api.server
```

The development server listens at `http://127.0.0.1:5000`.

**Terminal 2 — React workspace:**

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

Open the Vite URL (usually `http://localhost:5173`). Select a ticket and choose
**Troubleshoot**. AI requests require a valid `OPENAI_API_KEY` in the Flask
process environment and internet access.

## Verify the services

With the Flask API running, visit `http://127.0.0.1:5000/health`. A healthy
response is JSON with `"status": "ok"`. You can also check from a terminal:

```bash
curl http://127.0.0.1:5000/health
curl http://127.0.0.1:5000/api/tickets
```

The first command checks the API health route; the second should return a JSON
ticket list. With Vite running, open the URL it printed. The workspace should
show the incident queue; an API error means Flask is not available at the
configured address. The UI uses these existing and added API contracts:

- `GET /api/tickets` and `GET /api/tickets/<incident_number>` for ticket data.
- `GET /api/operational-incidents` for the synthetic incident records and fixed
  Europe/Berlin simulation timestamp.
- `POST /api/tickets/<incident_number>/investigation` and `/chat` for
  troubleshooting, ticket-scoped follow-up, and prepare-only status intents.
- `PATCH /api/tickets/<incident_number>` accepts a status only with an explicit
  `confirmed: true` and the `expected_current_status` shown for review. A stale
  status returns HTTP 409 and is not overwritten.
- `GET` / `POST /api/tickets/<incident_number>/resolutions` for verified
  resolution capture.
- `POST /api/tickets/<incident_number>/knowledge-articles/generate`,
  `GET` / `POST /api/tickets/<incident_number>/knowledge-articles`, and
  `PATCH /api/knowledge-articles/<article_id>` for draft and review.
- `GET /api/knowledge-articles/search` for approved reusable articles.

## Troubleshooting

- **`ModuleNotFoundError`** — activate the virtual environment in that
  terminal, then run `python -m pip install -r requirements.txt` again.
- **The UI says it cannot reach the Mock API** — start `python -m
  mock_api.server` in another terminal and confirm that port 5000 is free.
- **Port 5000 or 5173 is already in use** — stop the other process using that
  port before starting the service again. The API address is currently fixed in
  the application at `127.0.0.1:5000`.
- **OpenAI authentication or connection error during investigation** — check
  that `OPENAI_API_KEY` is configured in the environment available to the
  Flask process, that the key is valid, and that the machine can reach the
  OpenAI API. Do not share the key when asking for help.
- **Vite shows a proxy error** — confirm Flask is still running on port 5000.
- **The browser cannot open the UI** — use the exact local URL printed by Vite
  and leave both terminal processes running.

## Tests

The focused unit tests use Python's standard-library `unittest`; no additional
test dependency or API key is required. With the virtual environment active,
run them from the repository root:

```bash
python -m unittest discover -s test -v
```

The tests mock OpenAI and external HTTP calls. Mock API update tests use
Flask's in-process test client and do not write to `data/tickets.json`. The
React-facing workflow API contract is also exercised without a live OpenAI
call. To check the production asset bundle, run `npm --prefix frontend run build`.
