from flask import Flask, jsonify, request
import json
import os

app = Flask(__name__)

BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

DATA_DIR = os.path.join(BASE_DIR, "data")
TICKETS_FILE = os.path.join(DATA_DIR, "tickets.json")


# ============================================================
# LOAD / SAVE
# ============================================================

def load_tickets():
    with open(
        TICKETS_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        return json.load(file)


def save_tickets(data):
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


# ============================================================
# HEALTH CHECK
# ============================================================

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

            return jsonify(ticket)

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

    results = data["tickets"]

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


# ============================================================
# UPDATE TICKET
# ============================================================

@app.route(
    "/api/tickets/<incident_number>",
    methods=["PATCH"]
)
def update_ticket(incident_number):

    data = load_tickets()

    update_data = request.get_json()

    if not update_data:

        return jsonify({
            "error": "No update data provided"
        }), 400

    for ticket in data["tickets"]:

        if ticket["incident_number"] == incident_number:

            # Apply updates
            for key, value in update_data.items():
                ticket[key] = value

            # Persist changes to JSON
            save_tickets(data)

            return jsonify({
                "message": "Ticket updated successfully",
                "ticket": ticket
            })

    return jsonify({
        "error": "Ticket not found",
        "incident_number": incident_number
    }), 404


# ============================================================
# RUN SERVER
# ============================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
