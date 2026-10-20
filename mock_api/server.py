from flask import Flask, jsonify, make_response, request
from datetime import datetime, timezone
from functools import wraps
import json
import os
import re
import threading
import uuid
import copy
import tempfile
from agent import knowledge_governance as governance
from urllib.parse import parse_qsl, urlsplit

from agent.history import search_historical_cases
from agent import chat_agent, knowledge_capture, llm_agent
from agent import customer_communication
from agent.status_intent import parse_status_intent
from agent import demo_state, demo_auth

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024

BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

DATA_DIR = os.path.join(BASE_DIR, "data")
TICKETS_FILE = os.path.join(DATA_DIR, "tickets.json")
RESOLUTIONS_FILE = os.path.join(DATA_DIR, "resolution_records.json")
KNOWLEDGE_ARTICLES_FILE = os.path.join(DATA_DIR, "knowledge_articles.json")
PERMITTED_UPDATE_FIELDS = {"status", "expected_current_status", "confirmed"}
STATUS_CODES = {
    1: "New",
    2: "In Progress",
    3: "On Hold",
    4: "Resolved",
    5: "Closed",
    6: "Cancelled",
}
STATUS_VALUES = {label: code for code, label in STATUS_CODES.items()}
ALLOWED_STATUSES = set(STATUS_VALUES)
IMPACT_URGENCY_LABELS = {1: "Critical", 2: "High", 3: "Medium", 4: "Low"}
_ticket_status_lock = threading.Lock()
_knowledge_write_lock = threading.RLock()


def _serialize_knowledge_write(function):
    """Keep read/validate/version/write atomic within this JSON-backed process."""
    @wraps(function)
    def guarded(*args, **kwargs):
        with _knowledge_write_lock:
            return function(*args, **kwargs)
    return guarded


@app.before_request
def require_hosted_demo_authentication():
    """Authenticate every deployed API operation, including state dispatch."""
    if os.getenv("VERCEL") != "1" or not request.path.startswith("/api/"):
        return None
    if request.path in {"/api/auth/session", "/api/auth/login"}:
        return None
    if not demo_auth.request_is_authenticated():
        return jsonify({
            "error": "Sign in with the Demo Access Code to use this hosted demo.",
            "authentication_required": True,
        }), 401
    if (request.path not in {"/api/demo/dispatch", "/api/auth/logout"}
            and not demo_state.has_active_state()):
        return jsonify({
            "error": "Use the application request flow so browser demo state is validated.",
        }), 400


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "Request is too large. Reduce the submitted text and retry."}), 413


@app.route("/api/auth/session", methods=["GET"])
def auth_session():
    if os.getenv("VERCEL") != "1":
        return jsonify({"authenticated": True, "hosted_gate": False})
    try:
        demo_auth.validate_configuration()
    except demo_auth.DemoAuthError as exc:
        return jsonify({"authenticated": False, "error": str(exc)}), 503
    return jsonify({"authenticated": demo_auth.request_is_authenticated(),
                    "session_seconds": demo_auth.SESSION_SECONDS})


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    if os.getenv("VERCEL") != "1":
        return jsonify({"authenticated": True, "hosted_gate": False})
    if request.content_length is not None and request.content_length > 2048:
        return jsonify({"error": "The access-code request is too large."}), 413
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"access_code"}:
        return jsonify({"error": "Enter the Demo Access Code."}), 400
    try:
        if not demo_auth.verify_access_code(payload.get("access_code")):
            return jsonify({"error": "That Demo Access Code is not valid."}), 401
        session = demo_auth.issue_session()
    except demo_auth.DemoAuthError as exc:
        return jsonify({"error": str(exc)}), 503
    response = make_response(jsonify({"authenticated": True,
                                      "session_seconds": demo_auth.SESSION_SECONDS}))
    response.set_cookie(
        demo_auth.COOKIE_NAME, session, max_age=demo_auth.SESSION_SECONDS,
        httponly=True, secure=True, samesite="Strict", path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    response = make_response(jsonify({"authenticated": False}))
    response.delete_cookie(demo_auth.COOKIE_NAME, path="/", secure=os.getenv("VERCEL") == "1",
                           httponly=True, samesite="Strict")
    response.headers["Cache-Control"] = "no-store"
    return response


# ============================================================
# LOAD / SAVE
# ============================================================

def load_tickets():
    if os.getenv("VERCEL") == "1" and not demo_state.has_active_state():
        raise RuntimeError("A browser demo-state context is required for mutable ticket status data.")
    with open(
        TICKETS_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        data = json.load(file)
    state = demo_state.active_state()
    if state is not None:
        for ticket in data.get("tickets", []):
            code = state["ticket_statuses"].get(ticket.get("incident_number"))
            if code is not None:
                ticket["status"] = code
                if isinstance(ticket.get("legacy_fields"), dict):
                    label = STATUS_CODES[code]
                    ticket["legacy_status"] = label
                    ticket["legacy_fields"]["status"] = label
    return data


def save_tickets(data):
    if demo_state.has_active_state():
        demo_state.save_statuses(data)
        return
    if os.getenv("VERCEL") == "1":
        raise OSError("Refusing to write mutable ticket data to the Vercel filesystem.")
    with open(
        TICKETS_FILE,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False
        )


def load_resolution_records():
    """Read consultant-confirmed outcomes from their separate local JSON store."""
    state = demo_state.active_state()
    if state is not None:
        return {"verified_resolutions": state["verified_resolutions"]}
    if os.getenv("VERCEL") == "1":
        raise RuntimeError("A browser demo-state context is required for resolution records.")
    if not os.path.exists(RESOLUTIONS_FILE):
        return {"verified_resolutions": []}
    try:
        with open(RESOLUTIONS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("Resolution records are unavailable or invalid.") from None
    if not isinstance(data, dict) or not isinstance(data.get("verified_resolutions"), list):
        raise RuntimeError("Resolution records have an invalid data structure.")
    return data


def save_resolution_records(data):
    """Persist resolution records without changing tickets or KB documents."""
    if demo_state.has_active_state():
        demo_state.save_resolutions(data)
        return
    if os.getenv("VERCEL") == "1":
        raise OSError("Refusing to write resolution records to the Vercel filesystem.")
    with open(RESOLUTIONS_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def load_knowledge_articles():
    state = demo_state.active_state()
    if state is not None:
        return {"articles": copy.deepcopy(state["articles"])}
    if os.getenv("VERCEL") == "1":
        raise RuntimeError("A browser demo-state context is required for knowledge articles.")
    if not os.path.exists(KNOWLEDGE_ARTICLES_FILE):
        return {"articles": []}
    try:
        with open(KNOWLEDGE_ARTICLES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("Knowledge articles are unavailable or invalid.") from None
    if not isinstance(data, dict) or not isinstance(data.get("articles"), list):
        raise RuntimeError("Knowledge articles have an invalid data structure.")
    return data


def save_knowledge_articles(data):
    if demo_state.has_active_state():
        demo_state.save_articles(data)
        return
    if os.getenv("VERCEL") == "1":
        raise OSError("Refusing to write knowledge articles to the Vercel filesystem.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(KNOWLEDGE_ARTICLES_FILE), delete=False) as file:
            temporary = file.name
            json.dump(data, file, indent=2, ensure_ascii=False)
        os.replace(temporary, KNOWLEDGE_ARTICLES_FILE)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _knowledge_review(candidate, records, payload):
    warnings = governance.article_warnings(candidate, records)
    if warnings and payload.get("review_acknowledged") is not True:
        return jsonify({"error": "Review the knowledge warnings before proceeding.",
                        "governance_review_required": True, "warnings": warnings,
                        "proposed_article": {key: candidate[key] for key in ("source_resolution_id", "title", "symptoms")}}), 409
    return None


def _resolution_by_id(incident_number, resolution_id):
    data = load_resolution_records()
    return next((item for item in data["verified_resolutions"]
                 if isinstance(item, dict) and item.get("incident_number") == incident_number
                 and item.get("resolution_id") == resolution_id and item.get("verified") is True), None)


def _ticket_by_id(incident_number):
    return next((item for item in load_tickets().get("tickets", [])
                 if isinstance(item, dict) and item.get("incident_number") == incident_number), None)


def _incident_exists(incident_number):
    return any(
        isinstance(ticket, dict) and ticket.get("incident_number") == incident_number
        for ticket in load_tickets().get("tickets", [])
    )


def legacy_ticket_view(ticket):
    """Expose the previous string-based ticket fields while retaining canonical data."""
    if not isinstance(ticket, dict) or not isinstance(ticket.get("legacy_fields"), dict):
        return ticket

    view = dict(ticket)
    view.update(ticket["legacy_fields"])
    view["incident_number"] = ticket.get("incident_number")
    view["priority"] = ticket.get("priority")
    view["status_code"] = ticket.get("status")
    view["status_label"] = STATUS_CODES.get(ticket.get("status"), ticket.get("legacy_status"))
    view["status"] = view["status_label"]
    view["legacy_status"] = view["status_label"]
    for field in ("impact", "urgency"):
        value = ticket.get(field)
        if isinstance(value, int):
            view[f"{field}_code"] = value
            view[field] = IMPACT_URGENCY_LABELS.get(value, value)
    if "assigned_group" in ticket:
        view["assignment_group"] = ticket["assigned_group"]
    if isinstance(ticket.get("user_information"), dict):
        user = ticket["user_information"]
        view["reported_by"] = {key: user[key] for key in ("user_id", "email") if key in user}
    return view


def set_ticket_status(ticket, status):
    """Persist canonical numeric status and synchronize compatibility aliases."""
    if isinstance(ticket.get("status"), int) and isinstance(ticket.get("legacy_fields"), dict):
        code = STATUS_VALUES[status]
        ticket["status"] = code
        ticket["legacy_status"] = status
        ticket["legacy_fields"]["status"] = status
    else:
        # Keep compatibility with callers/tests that supply the pre-migration shape.
        ticket["status"] = status


def ticket_status_label(ticket):
    value = legacy_ticket_view(ticket).get("status")
    return STATUS_CODES.get(value, value) if isinstance(value, int) else value


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/api/demo/dispatch", methods=["POST"])
def dispatch_demo_state():
    """Run an existing API route against a validated, browser-carried snapshot."""
    envelope = request.get_json(silent=True)
    required_keys = {"path", "method"}
    allowed_keys = required_keys | {"payload", "state", "reset"}
    if (not isinstance(envelope, dict) or not required_keys.issubset(envelope)
            or set(envelope) - allowed_keys):
        return jsonify({"error": "Provide a valid demo-state request."}), 400
    method = envelope.get("method")
    raw_path = envelope.get("path")
    if (not isinstance(method, str) or method.upper() not in {"GET", "POST", "PATCH"}
            or not isinstance(raw_path, str) or len(raw_path) > 2048):
        return jsonify({"error": "The demo-state request method or path is invalid."}), 400
    parsed = urlsplit(raw_path)
    route_path = parsed.path
    if (not route_path.startswith("/api/") or route_path == "/api/demo/dispatch"
            or parsed.scheme or parsed.netloc or ".." in route_path.split("/")):
        return jsonify({"error": "Only application API routes can use browser demo state."}), 400
    if "payload" in envelope and method.upper() == "GET":
        return jsonify({"error": "GET requests cannot include a payload."}), 400
    if "reset" in envelope and not isinstance(envelope["reset"], bool):
        return jsonify({"error": "The reset flag must be a boolean."}), 400
    if envelope.get("reset") and (method.upper() != "GET" or "payload" in envelope):
        return jsonify({"error": "Reset must be a payload-free GET operation."}), 400
    if (not envelope.get("reset") and envelope.get("state") is None
            and method.upper() in {"POST", "PATCH"}):
        return jsonify({"error": "Load the demo state before changing it."}), 428
    model_route = any(route_path.endswith(suffix) for suffix in (
        "/investigation", "/chat", "/customer-email-draft", "/knowledge-articles/generate",
    ))
    if model_route:
        try:
            payload_bytes = len(json.dumps(envelope.get("payload"), ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            return jsonify({"error": "AI request payload must be valid JSON data."}), 400
        if payload_bytes > 96 * 1024:
            return jsonify({"error": "AI request content is too large. Reduce the submitted text and retry."}), 413
    try:
        state = demo_state.initial_state() if envelope.get("reset") else (
            demo_state.decode_state(envelope.get("state"))
            if envelope.get("state") is not None else demo_state.initial_state()
        )
        # Check the deployment signing secret and state bounds before any route
        # can perform side effects or spend a model request.
        demo_state.encode_state(state)
        with demo_state.use_state(state), demo_auth.trusted_dispatch():
            with app.test_client() as client:
                inner = client.open(
                    route_path,
                    method=method.upper(),
                    query_string=parse_qsl(parsed.query, keep_blank_values=True),
                    json=envelope.get("payload"),
                )
            result = inner.get_json(silent=True)
            if result is None:
                result = {"error": "The application API returned a non-JSON response."}
            token = demo_state.encode_state(state)
        return jsonify({
            "result": result,
            "status_code": inner.status_code,
            "demo_state": token,
        })
    except demo_state.DemoStateError as exc:
        status = 503 if "DEMO_STATE_SECRET" in str(exc) else 400
        return jsonify({"error": str(exc)}), status
    except (OSError, RuntimeError, ValueError) as exc:
        return jsonify({"error": f"Could not process browser demo state: {exc}"}), 400

@app.route("/health", methods=["GET"])
def health_check():

    return jsonify({
        "status": "ok",
        "service": "Mock ServiceNow API"
    })


# ============================================================
# GET SINGLE TICKET
# ============================================================

@app.route(
    "/api/tickets/<incident_number>",
    methods=["GET"]
)
def get_ticket(incident_number):

    data = load_tickets()

    for ticket in data["tickets"]:

        if ticket["incident_number"] == incident_number:

            return jsonify(legacy_ticket_view(ticket))

    return jsonify({
        "error": "Ticket not found",
        "incident_number": incident_number
    }), 404


# ============================================================
# SEARCH TICKETS
# ============================================================

@app.route("/api/tickets", methods=["GET"])
def search_tickets():

    data = load_tickets()

    system = request.args.get("system")
    status = request.args.get("status")

    results = [legacy_ticket_view(ticket) for ticket in data["tickets"]]

    if system:

        results = [
            ticket
            for ticket in results
            if ticket.get("system", "").lower()
            == system.lower()
        ]

    if status:

        results = [
            ticket
            for ticket in results
            if ticket.get("status", "").lower()
            == status.lower()
        ]

    return jsonify({
        "count": len(results),
        "tickets": results
    })


@app.route("/api/historical-cases", methods=["GET"])
def get_historical_cases():
    source_system = request.args.get("source_system", "")
    issue_type = request.args.get("issue_type", "")
    if not source_system.strip() or not issue_type.strip():
        return jsonify({
            "error": "source_system and issue_type query parameters are required"
        }), 400
    try:
        return jsonify(search_historical_cases(source_system, issue_type))
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/operational-incidents", methods=["GET"])
def get_operational_incidents():
    """Expose the local synthetic operational records and fixed simulation time."""
    path = os.path.join(DATA_DIR, "operational_incidents.json")
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return jsonify({"error": "Operational incident data is unavailable or invalid."}), 503
    records = data.get("operational_incidents") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return jsonify({"error": "Operational incident data has an invalid structure."}), 503
    source_system = request.args.get("source_system")
    operational_id = request.args.get("operational_incident_id")
    if source_system:
        records = [row for row in records if isinstance(row, dict) and row.get("source_system", "").casefold() == source_system.casefold()]
    if operational_id:
        records = [row for row in records if isinstance(row, dict) and row.get("operational_incident_id") == operational_id]
    return jsonify({
        "count": len(records),
        "simulation_timestamp": data.get("simulation_timestamp"),
        "simulation_timezone": data.get("simulation_timezone"),
        "operational_incidents": records,
    })


@app.route("/api/tickets/<incident_number>/investigation", methods=["POST"])
def investigate_ticket(incident_number):
    """Run deterministic evidence retrieval, then return the copilot's answer."""
    incident_number = incident_number.strip().upper()
    if not re.fullmatch(r"INC\d{6}", incident_number):
        return jsonify({"error": "Enter a valid incident number such as INC100001."}), 400
    try:
        investigation = llm_agent.run_investigation(incident_number, use_llm=False)
    except Exception:
        return jsonify({"error": "The investigation could not be completed. Check the ticket service and local data."}), 500
    if investigation.get("error") and not investigation.get("ticket"):
        status = 404 if "not found" in investigation["error"].lower() else 503
        return jsonify({"error": investigation["error"], "investigation": investigation}), status
    chat = chat_agent.chat_with_incident(
        incident_number,
        "Troubleshoot this ticket",
        [],
        investigation,
    )
    return jsonify({"investigation": investigation, "reply": chat.get("reply", ""),
                    "error": chat.get("error"),
                    "ai_provenance": chat.get("ai_provenance", "unavailable"),
                    "model": chat.get("model")})


@app.route("/api/tickets/<incident_number>/customer-communication", methods=["GET"])
def get_customer_communication(incident_number):
    incident_number = incident_number.strip().upper()
    if not re.fullmatch(r"INC\d{6}", incident_number):
        return jsonify({"error": "Enter a valid incident number such as INC100001."}), 400
    ticket = _ticket_by_id(incident_number)
    if not ticket:
        return jsonify({"error": "Ticket not found.", "incident_number": incident_number}), 404
    try:
        customer = customer_communication.get_customer_for_ticket(ticket)
        consultant = customer_communication.get_consultant_config()
        selected_date = request.args.get("date")
        contact_time = customer_communication.suggest_contact_time(
            customer, consultant, selected_date=selected_date,
        )
    except customer_communication.CustomerCommunicationError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({
        "incident_number": incident_number,
        "customer": customer,
        "consultant": {
            "location": consultant["location"],
            "timezone": consultant["timezone"],
        },
        "contact_time": contact_time,
    })


@app.route("/api/tickets/<incident_number>/customer-email-draft", methods=["POST"])
def generate_customer_email_draft(incident_number):
    incident_number = incident_number.strip().upper()
    if not re.fullmatch(r"INC\d{6}", incident_number):
        return jsonify({"error": "Enter a valid incident number such as INC100001."}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) - {"investigation", "date"}:
        return jsonify({"error": "Provide optional investigation evidence and an ISO contact date."}), 400
    if "investigation" in payload and not isinstance(payload["investigation"], dict):
        return jsonify({"error": "Investigation evidence must be a JSON object."}), 400
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 96 * 1024:
        return jsonify({"error": "Email context is too large. Run the investigation again and retry."}), 413
    investigation = payload.get("investigation")
    if not isinstance(investigation, dict) or not isinstance(investigation.get("ticket"), dict):
        return jsonify({"error": "Run and provide the completed investigation for this ticket before generating a customer email."}), 400
    investigation_incident = investigation["ticket"].get("incident_number")
    if not isinstance(investigation_incident, str) or investigation_incident.strip().upper() != incident_number:
        return jsonify({"error": "Investigation results must belong to the selected ticket."}), 400
    if not isinstance(investigation.get("resolution_path"), str):
        return jsonify({"error": "Provide the completed investigation result for the selected ticket."}), 400
    if "date" in payload and not isinstance(payload["date"], str):
        return jsonify({"error": "Contact date must use YYYY-MM-DD format."}), 400
    ticket = _ticket_by_id(incident_number)
    if not ticket:
        return jsonify({"error": "Ticket not found.", "incident_number": incident_number}), 404
    try:
        customer = customer_communication.get_customer_for_ticket(ticket)
        consultant = customer_communication.get_consultant_config()
        contact_time = customer_communication.suggest_contact_time(
            customer, consultant, selected_date=payload.get("date"),
        )
        draft = customer_communication.generate_customer_email_draft(
            ticket,
            customer,
            contact_time,
            investigation=investigation,
        )
    except customer_communication.CustomerCommunicationError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"incident_number": incident_number, **draft})


@app.route("/api/tickets/<incident_number>/chat", methods=["POST"])
def chat_about_ticket(incident_number):
    incident_number = incident_number.strip().upper()
    if not re.fullmatch(r"INC\d{6}", incident_number):
        return jsonify({"error": "Enter a valid incident number such as INC100001."}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) - {"message", "history", "request_investigation"}:
        return jsonify({"error": "Provide a message, optional conversation history, and optional investigation flag."}), 400
    message = payload.get("message")
    history = payload.get("history", [])
    request_investigation = payload.get("request_investigation", False)
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "Enter a message for the Ticket Agent."}), 400
    if not isinstance(history, list) or not isinstance(request_investigation, bool):
        return jsonify({"error": "Conversation history or investigation flag has an invalid format."}), 400
    if len(message) > 4000:
        return jsonify({"error": "Messages must be 4000 characters or fewer."}), 413
    if len(history) > 12 or any(
        not isinstance(item, dict) or set(item) != {"role", "content"}
        or item.get("role") not in {"user", "assistant"}
        or not isinstance(item.get("content"), str) or len(item["content"]) > 4000
        for item in history
    ):
        return jsonify({"error": "Chat history is invalid or too long. Start a new message and retry."}), 400
    if sum(len(item["content"]) for item in history) > 20_000:
        return jsonify({"error": "Chat history exceeds the supported size. Start a new message and retry."}), 413
    status_intent = parse_status_intent(message)
    if status_intent:
        target_incident = status_intent.get("incident_number") or incident_number
        target_status = status_intent.get("target_status")
        if status_intent.get("clarification"):
            return jsonify({
                "reply": status_intent["clarification"],
                "error": None,
                "status_update": {"state": "clarification", "incident_number": target_incident,
                                  "requested_status": None},
            })
        ticket = _ticket_by_id(target_incident)
        if not ticket:
            return jsonify({
                "reply": f"I could not find ticket {target_incident}. Check the incident number and try again.",
                "error": None,
                "status_update": {"state": "not_found", "incident_number": target_incident,
                                  "requested_status": target_status},
            })
        current_status = ticket_status_label(ticket)
        if current_status not in ALLOWED_STATUSES:
            return jsonify({
                "reply": f"Ticket {target_incident} has no valid current status. Refresh the incident before requesting an update.",
                "error": None,
                "status_update": {"state": "invalid_current_status", "incident_number": target_incident,
                                  "current_status": current_status, "requested_status": target_status},
            })
        if current_status == target_status:
            return jsonify({
                "reply": f"{target_incident} is already {target_status}.",
                "error": None,
                "status_update": {"state": "already_current", "incident_number": target_incident,
                                  "current_status": current_status, "requested_status": target_status},
            })
        return jsonify({
            "reply": "Review the proposed status change and confirm it before I update the ticket.",
            "error": None,
            "status_update": {"state": "pending_confirmation", "incident_number": target_incident,
                              "explicit_target": bool(status_intent.get("incident_number")),
                              "current_status": current_status, "requested_status": target_status},
        })
    result = chat_agent.chat_with_incident(
        incident_number, message, history, None,
        request_investigation=request_investigation,
    )
    return jsonify(result), 200 if not result.get("error") or result.get("ai_provenance") == "deterministic_fallback" else 503


@app.route("/api/tickets/<incident_number>/knowledge-articles/generate", methods=["POST"])
@_serialize_knowledge_write
def generate_ticket_knowledge_draft(incident_number):
    incident_number = incident_number.strip().upper()
    if not re.fullmatch(r"INC\d{6}", incident_number):
        return jsonify({"error": "Enter a valid incident number such as INC100001."}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"resolution_id"}:
        return jsonify({"error": "Provide the selected verified resolution_id."}), 400
    try:
        ticket = _ticket_by_id(incident_number)
        if not ticket:
            return jsonify({"error": "Ticket not found."}), 404
        resolution = _resolution_by_id(incident_number, payload["resolution_id"])
        if not resolution:
            return jsonify({"error": "A persisted verified resolution for this incident is required."}), 409
        data = load_knowledge_articles()
        existing = next((item for item in data["articles"] if isinstance(item, dict)
                         and item.get("source_resolution_id") == resolution["resolution_id"]), None)
        if existing:
            return jsonify({"article": existing, "created": False,
                            "ai_provenance": "previously_generated"}), 200
        draft_content = knowledge_capture.generate_article_content(ticket, resolution)
        source_system = ticket.get("source_system") or ticket.get("system") or ""
        category = ticket.get("category") or ticket.get("issue_type") or ""
        article = {
            "article_id": "KBA-" + uuid.uuid4().hex[:12].upper(),
            "title": draft_content["title"],
            "source_incident": incident_number,
            "source_resolution_id": resolution["resolution_id"],
            "source_system": source_system,
            "issue_category": category,
            "error_signature": ticket.get("additional_comments") or ticket.get("test_detail") or "",
            "symptoms": draft_content["symptoms"],
            "confirmed_root_cause": resolution["confirmed_root_cause"],
            "verified_troubleshooting_steps": list(resolution["actions_performed"]),
            "required_roles": _documented_roles(ticket),
            "verification_method": resolution["verification_evidence"],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "created_by": resolution["consultant_id"],
            "review_status": "Draft",
            "approved_by": None,
            "approved_at": None,
            "published_at": None,
            "knowledge_source": "consultant_verified_resolution",
        }
        article.update(governance.new_metadata())
        review = _knowledge_review(article, data["articles"], payload)
        if review:
            return review
        data["articles"].append(article)
        save_knowledge_articles(data)
    except knowledge_capture.KnowledgeCaptureError as exc:
        return jsonify({"error": str(exc)}), 503
    except (RuntimeError, OSError, json.JSONDecodeError, AttributeError):
        return jsonify({"error": "Resolution or knowledge article storage is unavailable; no draft was saved."}), 500
    return jsonify({"article": article, "created": True,
                    "ai_provenance": "live_gpt", "model": llm_agent.MODEL}), 201


@app.route("/api/tickets/<incident_number>/resolutions", methods=["GET"])
def get_ticket_resolutions(incident_number):
    incident_number = incident_number.strip().upper()
    if not re.fullmatch(r"INC\d{6}", incident_number):
        return jsonify({"error": "Enter a valid incident number such as INC100001."}), 400
    try:
        if not _incident_exists(incident_number):
            return jsonify({"error": "Ticket not found", "incident_number": incident_number}), 404
        data = load_resolution_records()
    except (RuntimeError, OSError, json.JSONDecodeError, AttributeError) as exc:
        return jsonify({"error": str(exc)}), 500
    matches = [
        record for record in data["verified_resolutions"]
        if isinstance(record, dict) and record.get("incident_number") == incident_number
        and record.get("verified") is True
    ]
    return jsonify({
        "incident_number": incident_number,
        "count": len(matches),
        "resolutions": matches,
    })


@app.route("/api/tickets/<incident_number>/resolutions", methods=["POST"])
def create_ticket_resolution(incident_number):
    incident_number = incident_number.strip().upper()
    if not re.fullmatch(r"INC\d{6}", incident_number):
        return jsonify({"error": "Enter a valid incident number such as INC100001."}), 400
    try:
        if not _incident_exists(incident_number):
            return jsonify({"error": "Ticket not found", "incident_number": incident_number}), 404
    except (RuntimeError, OSError, json.JSONDecodeError, AttributeError):
        return jsonify({"error": "Ticket data is unavailable; no resolution was saved."}), 500

    payload = request.get_json(silent=True)
    required = {
        "confirmed_root_cause", "actions_performed", "verification_evidence",
        "consultant_id", "consultant_confirmed",
    }
    if not isinstance(payload, dict):
        return jsonify({"error": "Provide a JSON object with the confirmed resolution details."}), 400
    if set(payload) != required:
        return jsonify({
            "error": "Provide exactly the required resolution fields.",
            "required_fields": sorted(required),
        }), 400
    if payload.get("consultant_confirmed") is not True:
        return jsonify({"error": "Explicit consultant confirmation is required; no resolution was saved."}), 400

    text_fields = ("confirmed_root_cause", "verification_evidence", "consultant_id")
    normalized = {}
    for field in text_fields:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            return jsonify({"error": f"'{field}' must be a non-empty string; no resolution was saved."}), 400
        normalized[field] = value.strip()

    actions = payload.get("actions_performed")
    if (not isinstance(actions, list) or not actions
            or not all(isinstance(action, str) and action.strip() for action in actions)):
        return jsonify({"error": "'actions_performed' must be a non-empty list of completed actions; no resolution was saved."}), 400

    try:
        data = load_resolution_records()
    except (RuntimeError, OSError) as exc:
        return jsonify({"error": str(exc) + " No resolution was saved."}), 500

    record = {
        "resolution_id": "RES-" + uuid.uuid4().hex,
        "incident_number": incident_number,
        "confirmed_root_cause": normalized["confirmed_root_cause"],
        "actions_performed": [action.strip() for action in actions],
        "verification_evidence": normalized["verification_evidence"],
        "consultant_id": normalized["consultant_id"],
        "confirmation_timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "verified": True,
        "verification_source": "explicit_consultant_confirmation",
    }
    data["verified_resolutions"].append(record)
    try:
        save_resolution_records(data)
    except OSError:
        return jsonify({"error": "Could not persist the resolution record; no success was recorded."}), 500

    return jsonify({"message": "Consultant-confirmed resolution saved.", "resolution": record}), 201


@app.route("/api/tickets/<incident_number>/knowledge-articles", methods=["GET"])
def get_ticket_knowledge_articles(incident_number):
    incident_number = incident_number.strip().upper()
    if not re.fullmatch(r"INC\d{6}", incident_number):
        return jsonify({"error": "Enter a valid incident number such as INC100001."}), 400
    try:
        if not _incident_exists(incident_number):
            return jsonify({"error": "Ticket not found", "incident_number": incident_number}), 404
        records = load_knowledge_articles()["articles"]
    except (RuntimeError, OSError, json.JSONDecodeError, AttributeError):
        return jsonify({"error": "Knowledge article storage is unavailable."}), 500
    matches = [item for item in records if isinstance(item, dict) and item.get("source_incident") == incident_number]
    return jsonify({"incident_number": incident_number, "articles": matches})


def _documented_roles(ticket):
    """Return only role references explicitly attached to the ticket's linked KB docs."""
    if not any(word in " ".join(str(ticket.get(key, "")) for key in ("category", "subcategory", "short_description", "additional_comments")).lower()
               for word in ("access", "authoriz", "role", "visibility", "permission")):
        return []
    try:
        with open(os.path.join(DATA_DIR, "product_docs.json"), "r", encoding="utf-8") as file:
            docs = json.load(file).get("documents", [])
        with open(os.path.join(DATA_DIR, "roles.json"), "r", encoding="utf-8") as file:
            catalogues = json.load(file).get("roles", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return []
    linked = set(ticket.get("linked_kb_document_ids", []))
    source = ticket.get("source_system") or ticket.get("system")
    catalogue = next((row for row in catalogues if isinstance(row, dict) and row.get("source_system") == source), {})
    by_id = {row.get("role_id"): row for group in ("gateway_roles", "backend_roles")
             for row in catalogue.get(group, []) if isinstance(row, dict)}
    role_ids = set()
    for doc in docs:
        if not isinstance(doc, dict) or doc.get("document_id") not in linked:
            continue
        refs = doc.get("role_references", {})
        if isinstance(refs, dict):
            for key in ("gateway_role_ids", "backend_role_ids"):
                role_ids.update(item for item in refs.get(key, []) if isinstance(item, str))
    return [by_id[item] for item in sorted(role_ids) if item in by_id]


@app.route("/api/tickets/<incident_number>/knowledge-articles", methods=["POST"])
@_serialize_knowledge_write
def create_knowledge_article_draft(incident_number):
    incident_number = incident_number.strip().upper()
    if not re.fullmatch(r"INC\d{6}", incident_number):
        return jsonify({"error": "Enter a valid incident number such as INC100001."}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not {"source_resolution_id", "title", "symptoms"}.issubset(payload) or set(payload) - {"source_resolution_id", "title", "symptoms", "review_acknowledged"}:
        return jsonify({"error": "Provide a source resolution ID, generated title, and symptoms."}), 400
    if not all(isinstance(payload.get(key), str) and payload[key].strip()
               for key in ("source_resolution_id", "title", "symptoms")):
        return jsonify({"error": "Article title, symptoms, and source resolution are required."}), 400
    try:
        ticket = _ticket_by_id(incident_number)
        if not ticket:
            return jsonify({"error": "Ticket not found."}), 404
        resolution = _resolution_by_id(incident_number, payload["source_resolution_id"])
        if not resolution:
            return jsonify({"error": "A persisted verified resolution for this incident is required."}), 409
        data = load_knowledge_articles()
    except (RuntimeError, OSError, json.JSONDecodeError, AttributeError):
        return jsonify({"error": "Resolution or knowledge article storage is unavailable; no draft was saved."}), 500

    existing = next((item for item in data["articles"]
                     if isinstance(item, dict) and item.get("source_resolution_id") == resolution["resolution_id"]), None)
    if existing:
        return jsonify({"message": "An article already exists for this verified resolution.", "article": existing, "created": False}), 200

    source_system = ticket.get("source_system") or ticket.get("system") or ""
    category = ticket.get("category") or ticket.get("issue_type") or ""
    signature = ticket.get("additional_comments") or ticket.get("test_detail") or ""
    article = {
        "article_id": "KBA-" + uuid.uuid4().hex[:12].upper(),
        "title": payload["title"].strip(),
        "source_incident": incident_number,
        "source_resolution_id": resolution["resolution_id"],
        "source_system": source_system,
        "issue_category": category,
        "error_signature": signature,
        "symptoms": payload["symptoms"].strip(),
        "confirmed_root_cause": resolution["confirmed_root_cause"],
        "verified_troubleshooting_steps": list(resolution["actions_performed"]),
        "required_roles": _documented_roles(ticket),
        "verification_method": resolution["verification_evidence"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "created_by": resolution["consultant_id"],
        "review_status": "Draft",
        "approved_by": None,
        "approved_at": None,
        "published_at": None,
        "knowledge_source": "consultant_verified_resolution",
    }
    article.update(governance.new_metadata())
    review = _knowledge_review(article, data["articles"], payload)
    if review:
        return review
    data["articles"].append(article)
    try:
        save_knowledge_articles(data)
    except OSError:
        return jsonify({"error": "Could not persist the knowledge draft; no draft was recorded."}), 500
    return jsonify({"message": "Knowledge draft saved for human review.", "article": article, "created": True}), 201


@app.route("/api/knowledge-articles/<article_id>", methods=["GET"])
def get_knowledge_article(article_id):
    try:
        article = next((row for row in load_knowledge_articles()["articles"] if row.get("article_id") == article_id), None)
    except RuntimeError:
        return jsonify({"error": "Knowledge article storage is unavailable."}), 500
    if not article:
        return jsonify({"error": "Knowledge article not found."}), 404
    return jsonify({"article": {**governance.new_metadata(), **article}})


@app.route("/api/knowledge-articles/<article_id>", methods=["PATCH"])
@_serialize_knowledge_write
def update_knowledge_article(article_id):
    payload = request.get_json(silent=True)
    content_fields = {"title", "symptoms", "confirmed_root_cause", "verified_troubleshooting_steps", "verification_method"}
    allowed = content_fields | {"approve", "approved_by", "lifecycle_status", "expected_version", "review_acknowledged"}
    if not isinstance(payload, dict) or not payload or set(payload) - allowed:
        return jsonify({"error": "Provide supported article edits or an explicit approval action."}), 400
    try:
        data = load_knowledge_articles()
    except RuntimeError:
        return jsonify({"error": "Knowledge article storage is unavailable."}), 500
    previous = next((row for row in data["articles"] if row.get("article_id") == article_id), None)
    if not previous:
        return jsonify({"error": "Knowledge article not found."}), 404
    if "expected_version" in payload and (type(payload["expected_version"]) is not int or payload["expected_version"] != previous.get("version", 1)):
        return jsonify({"error": "The article changed after review. Reload it before saving; your edits have not been saved."}), 409
    if previous.get("review_status") != "Draft" and (content_fields.intersection(payload) or "approve" in payload):
        return jsonify({"error": "Only Draft articles can be edited or approved. Published articles support lifecycle changes only."}), 409
    article = copy.deepcopy(previous)
    for key in content_fields.intersection(payload):
        value = payload[key]
        if key == "verified_troubleshooting_steps":
            valid = isinstance(value, list) and bool(value) and all(isinstance(v, str) and v.strip() for v in value)
        else:
            valid = isinstance(value, str) and bool(value.strip())
        if not valid:
            return jsonify({"error": f"'{key}' must contain non-empty text."}), 400
        article[key] = value
    if "lifecycle_status" in payload:
        if not isinstance(payload["lifecycle_status"], str) or payload["lifecycle_status"] not in governance.LIFECYCLE_STATUSES:
            return jsonify({"error": "Lifecycle must be active, expired, or superseded."}), 400
        article["lifecycle_status"] = payload["lifecycle_status"]
    approval = payload.get("approve") is True
    if "approve" in payload and not approval:
        return jsonify({"error": "Approval must be an explicit true value."}), 400
    if approval:
        if not isinstance(payload.get("approved_by"), str) or not payload["approved_by"].strip():
            return jsonify({"error": "A reviewer identifier is required for approval."}), 400
        article.update(review_status="Approved", approved_by=payload["approved_by"].strip())
        article["approved_at"] = article["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if content_fields.intersection(payload) or approval or payload.get("lifecycle_status") == "active":
        review = _knowledge_review(article, data["articles"], payload)
        if review:
            return review
    if article != previous:
        if len(previous.get("version_history", [])) >= 100:
            return jsonify({"error": "Version history limit reached. Export and review history before further updates; nothing was saved."}), 409
        article = governance.version_update(previous, article, datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
        data["articles"] = [article if row.get("article_id") == article_id else row for row in data["articles"]]
        try:
            save_knowledge_articles(data)
        except (OSError, RuntimeError):
            return jsonify({"error": "Could not persist the article update. Your edits have not been saved; retry when storage is available."}), 500
    return jsonify({"article": article, "message": "Article approved and published." if approval else "Article updated."})


@app.route("/api/knowledge-articles/search", methods=["GET"])
def search_knowledge_articles():
    source_system = request.args.get("source_system", "").strip()
    issue_category = request.args.get("issue_category", "").strip()
    query = request.args.get("q", "").strip().lower()
    try:
        records = load_knowledge_articles()["articles"]
    except RuntimeError:
        return jsonify({"error": "Knowledge article storage is unavailable."}), 500
    def norm(value):
        return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value).lower()).split())
    tokens = set(norm(query).split())
    matches = []
    for article in records:
        if not isinstance(article, dict) or article.get("review_status") != "Approved" or not governance.is_active(article):
            continue
        if source_system and norm(article.get("source_system")) != norm(source_system):
            continue
        if issue_category and norm(article.get("issue_category")) != norm(issue_category):
            continue
        haystack = norm(" ".join(str(article.get(key, "")) for key in
                           ("title", "issue_category", "error_signature", "symptoms", "confirmed_root_cause")))
        if query and not (norm(query) in haystack or tokens.intersection(haystack.split())):
            continue
        matches.append({**{key: value for key, value in article.items() if key != "version_history"}, "knowledge_source": "approved_captured_article"})
    return jsonify({"count": len(matches), "articles": matches})


# ============================================================
# UPDATE TICKET
# ============================================================

@app.route(
    "/api/tickets/<incident_number>",
    methods=["PATCH"]
)
def update_ticket(incident_number):
    update_data = request.get_json(silent=True)

    if not isinstance(update_data, dict) or not update_data:

        return jsonify({
            "error": "Request body must be a non-empty JSON object"
        }), 400

    invalid_fields = set(update_data) - PERMITTED_UPDATE_FIELDS
    if invalid_fields:
        return jsonify({
            "error": "Update contains fields that cannot be changed",
            "invalid_fields": sorted(invalid_fields),
            "permitted_fields": sorted(PERMITTED_UPDATE_FIELDS),
        }), 400

    if not isinstance(update_data.get("status"), str) or update_data["status"] not in ALLOWED_STATUSES:
        return jsonify({
            "error": "Invalid status",
            "allowed_statuses": sorted(ALLOWED_STATUSES),
        }), 400
    if update_data.get("confirmed") is not True:
        return jsonify({"error": "Explicit consultant confirmation is required before updating status."}), 400
    expected = update_data.get("expected_current_status")
    if not isinstance(expected, str) or expected not in ALLOWED_STATUSES:
        return jsonify({"error": "A valid expected_current_status is required to prevent stale updates."}), 400

    with _ticket_status_lock:
        latest_data = load_tickets()
        ticket = next((item for item in latest_data.get("tickets", [])
                       if item.get("incident_number") == incident_number), None)
        if ticket is None:
            return jsonify({"error": "Ticket not found", "incident_number": incident_number}), 404
        latest_status = ticket_status_label(ticket)
        if latest_status != expected:
            return jsonify({
                "error": "The ticket status changed after confirmation was prepared. Review the latest status and try again.",
                "current_status": latest_status,
            }), 409
        set_ticket_status(ticket, update_data["status"])
        try:
            save_tickets(latest_data)
        except OSError:
            return jsonify({"error": "Could not persist the ticket status update."}), 500
    return jsonify({"message": "Ticket updated successfully", "ticket": legacy_ticket_view(ticket)})


# ============================================================
# RUN SERVER
# ============================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
