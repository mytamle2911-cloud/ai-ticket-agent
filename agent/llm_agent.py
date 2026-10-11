import json
import os
import requests

from openai import OpenAI
from dotenv import load_dotenv


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

BASE_URL = "http://127.0.0.1:5000"

client = OpenAI()


# ============================================================
# HELPER
# ============================================================

def get_data_path(filename):

    base_dir = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )

    return os.path.join(
        base_dir,
        "data",
        filename
    )


# ============================================================
# TOOL 1 — GET TICKET
# ============================================================

def get_ticket(incident_number):

    response = requests.get(
        f"{BASE_URL}/api/tickets/{incident_number}",
        timeout=10
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# TOOL 2 — SEARCH PRODUCT DOCUMENTATION
# ============================================================

def search_product_docs(
    source_system,
    error_message
):

    file_path = get_data_path(
        "product_docs.json"
    )

    with open(
        file_path,
        "r",
        encoding="utf-8"
    ) as file:

        data = json.load(file)

    source_system = source_system.strip().lower()
    error_message = error_message.strip().lower()

    results = []

    # --------------------------------------------------------
    # Search by source system + error
    # --------------------------------------------------------

    for doc in data["documents"]:

        if doc.get(
            "source_system",
            ""
        ).lower() != source_system:

            continue

        searchable_messages = []

        searchable_messages.extend(
            message.lower()
            for message in doc.get(
                "error_messages",
                []
            )
        )

        searchable_messages.extend(
            code.lower()
            for code in doc.get(
                "error_codes",
                []
            )
        )

        searchable_messages.append(
            doc.get(
                "topic",
                ""
            ).lower()
        )

        matched = any(
            error_message in message
            or message in error_message
            for message in searchable_messages
        )

        # Generic authorization fallback
        authorization_keywords = [
            "access",
            "authorization",
            "authorized",
            "permission",
            "denied"
        ]

        error_has_auth_keyword = any(
            keyword in error_message
            for keyword in authorization_keywords
        )

        doc_has_auth_keyword = any(
            keyword in " ".join(
                searchable_messages
            )
            for keyword in authorization_keywords
        )

        if matched or (
            error_has_auth_keyword
            and doc_has_auth_keyword
            and "visibility" in " ".join(
                searchable_messages
            )
        ):

            results.append(doc)

    return results


# ============================================================
# TOOL 3 — VERIFY ROLES
# ============================================================

def verify_roles(
    source_system,
    gateway_roles,
    backend_roles
):

    file_path = get_data_path(
        "roles.json"
    )

    with open(
        file_path,
        "r",
        encoding="utf-8"
    ) as file:

        data = json.load(file)

    source_system = source_system.strip().lower()

    for system in data["roles"]:

        if system.get(
            "source_system",
            ""
        ).lower() != source_system:

            continue

        valid_gateway_ids = {
            role["role_id"]
            for role in system.get(
                "gateway_roles",
                []
            )
        }

        valid_backend_ids = {
            role["role_id"]
            for role in system.get(
                "backend_roles",
                []
            )
        }

        return {
            "source_system": system[
                "source_system"
            ],

            "gateway_roles": [
                role
                for role in gateway_roles
                if role.get(
                    "role_id"
                ) in valid_gateway_ids
            ],

            "backend_roles": [
                role
                for role in backend_roles
                if role.get(
                    "role_id"
                ) in valid_backend_ids
            ]
        }

    return {
        "source_system": source_system,
        "gateway_roles": [],
        "backend_roles": []
    }


# ============================================================
# TOOL 4 — UPDATE TICKET
#
# This tool is intentionally NOT exposed to the LLM.
# It can only be called after human L2 approval.
# ============================================================

def update_ticket(
    incident_number,
    status
):

    response = requests.patch(
        f"{BASE_URL}/api/tickets/{incident_number}",
        json={
            "status": status
        },
        timeout=10
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# OPENAI TOOL DEFINITIONS
# ============================================================

TOOLS = [

    {
        "type": "function",
        "name": "get_ticket",

        "description": (
            "Retrieve a support ticket from the mock "
            "ServiceNow ticket system using its incident number."
        ),

        "parameters": {

            "type": "object",

            "properties": {

                "incident_number": {
                    "type": "string",
                    "description": (
                        "Incident number, e.g. INC100001"
                    )
                }
            },

            "required": [
                "incident_number"
            ],

            "additionalProperties": False
        }
    },

    {
        "type": "function",
        "name": "search_product_docs",

        "description": (
            "Search approved product documentation using "
            "the source system and error message. The "
            "documentation may contain required Gateway "
            "and Backend Roles."
        ),

        "parameters": {

            "type": "object",

            "properties": {

                "source_system": {
                    "type": "string"
                },

                "error_message": {
                    "type": "string"
                }
            },

            "required": [
                "source_system",
                "error_message"
            ],

            "additionalProperties": False
        }
    },

    {
        "type": "function",
        "name": "verify_roles",

        "description": (
            "Verify that Gateway and Backend Roles "
            "identified from product documentation "
            "belong to the specified source system."
        ),

        "parameters": {

            "type": "object",

            "properties": {

                "source_system": {
                    "type": "string"
                },

                "gateway_roles": {

                    "type": "array",

                    "items": {

                        "type": "object",

                        "properties": {

                            "role_id": {
                                "type": "string"
                            },

                            "description": {
                                "type": "string"
                            }
                        },

                        "required": [
                            "role_id"
                        ],

                        "additionalProperties": False
                    }
                },

                "backend_roles": {

                    "type": "array",

                    "items": {

                        "type": "object",

                        "properties": {

                            "role_id": {
                                "type": "string"
                            },

                            "description": {
                                "type": "string"
                            }
                        },

                        "required": [
                            "role_id"
                        ],

                        "additionalProperties": False
                    }
                }
            },

            "required": [
                "source_system",
                "gateway_roles",
                "backend_roles"
            ],

            "additionalProperties": False
        }
    }
]


# ============================================================
# TOOL EXECUTOR
# ============================================================

def execute_tool(
    name,
    arguments
):

    if name == "get_ticket":

        return get_ticket(
            arguments["incident_number"]
        )

    if name == "search_product_docs":

        return search_product_docs(
            source_system=arguments[
                "source_system"
            ],

            error_message=arguments[
                "error_message"
            ]
        )

    if name == "verify_roles":

        return verify_roles(
            source_system=arguments[
                "source_system"
            ],

            gateway_roles=arguments[
                "gateway_roles"
            ],

            backend_roles=arguments[
                "backend_roles"
            ]
        )

    return {
        "error": f"Unknown tool: {name}"
    }


# ============================================================
# UI-FRIENDLY INVESTIGATION (used by Streamlit app.py)
# Returns structured data instead of printing to stdout.
# ============================================================

def run_investigation(incident_number: str) -> dict:
    """
    Run the full AI agent investigation and return a structured
    result dict for the Streamlit UI.

    Returns:
        {
            "ticket": dict,
            "steps": [{"iteration": int, "tool_calls": [...]}],
            "final_analysis": str,
            "approval_needed": bool,
            "already_on_hold": bool,
            "error": str | None,
        }
    """

    result = {
        "ticket": None,
        "steps": [],
        "final_analysis": "",
        "approval_needed": False,
        "already_on_hold": False,
        "error": None,
    }

    # ----------------------------------------------------------
    # Initial prompt
    # ----------------------------------------------------------

    input_items = [
        {
            "role": "user",
            "content": (
                f"Analyze support ticket "
                f"{incident_number}. "
                f"Retrieve the ticket and investigate "
                f"the issue using the available product "
                f"knowledge and role verification tools."
            )
        }
    ]

    instructions = """
You are an AI Agent supporting a Level 2 workflow
application support team.

Your job is to investigate support tickets using
approved tools and product documentation.

Follow these rules:

1. Retrieve the ticket using get_ticket.
2. Identify the source system.
3. Identify the issue and error message.
4. Search product documentation using
   search_product_docs.
5. If documentation identifies Gateway or Backend
   Roles, verify them using verify_roles.
6. Never invent role IDs.
7. Never infer a role ID only from the source system.
8. Never grant access.
9. Never create an access request on behalf of the user.
10. The user must request access through the designated
    access-management process.
11. If documented required roles are identified,
    recommend that the user check/request them.
12. If access is confirmed as the issue, recommend
    putting the ticket On Hold while waiting for the
    user's access request.
13. Human L2 approval is required before changing
    ticket status.
14. You cannot change ticket status yourself.
15. Clearly distinguish:
    - information retrieved from tools
    - analysis
    - recommendation
16. Do not claim that a role is missing from the user's
    account because the ticket does not contain the
    user's actual assigned roles.
17. Do not claim that access has been granted.
"""

    max_iterations = 10

    for iteration in range(max_iterations):

        step = {
            "iteration": iteration + 1,
            "tool_calls": [],
        }

        try:
            # pyrefly: ignore [no-matching-overload]
            response = client.responses.create(
                model="gpt-5.6-luna",
                instructions=instructions,
                tools=TOOLS,
                input=input_items,
            )
        except Exception as exc:
            result["error"] = str(exc)  # type: ignore[assignment]
            return result

        input_items += response.output

        tool_calls_found = False

        for item in response.output:

            if item.type != "function_call":
                continue

            tool_calls_found = True

            arguments = json.loads(item.arguments)

            tool_result = execute_tool(item.name, arguments)

            step["tool_calls"].append({
                "name": item.name,
                "arguments": arguments,
                "result": tool_result,
            })

            input_items.append({
                "type": "function_call_output",
                "call_id": item.call_id,
                "output": json.dumps(tool_result, ensure_ascii=False),
            })

        result["steps"].append(step)

        if not tool_calls_found:
            result["final_analysis"] = response.output_text or ""
            break

    # ----------------------------------------------------------
    # Evidence gate
    # ----------------------------------------------------------

    analysis = str(result["final_analysis"] or "").lower()

    no_documentation = (
        "no matching documentation" in analysis
        or "no relevant documentation" in analysis
        or "no relevant product knowledge" in analysis
        or "documentation search returned no" in analysis
        or "no matching documentation was returned" in analysis
    )

    insufficient_evidence = (
        "evidence is insufficient" in analysis
        or "cannot determine" in analysis
        or "not possible to confirm" in analysis
        or "cannot be established as the cause" in analysis
        or "no ticket status change is recommended" in analysis
        or "further investigation" in analysis
    )

    access_evidence = (
        "required roles" in analysis
        or "required role" in analysis
        or "access-related issue" in analysis
        or "authorization issue" in analysis
        or "on hold" in analysis
    )

    if no_documentation or insufficient_evidence or not access_evidence:
        result["approval_needed"] = False
        return result

    # ----------------------------------------------------------
    # Check if ticket is already On Hold
    # ----------------------------------------------------------

    try:
        current_ticket = get_ticket(incident_number)
        result["ticket"] = current_ticket
    except Exception:
        pass

    if isinstance(result["ticket"], dict) and result["ticket"].get("status") == "On Hold":
        result["already_on_hold"] = True
        result["approval_needed"] = False
        return result

    result["approval_needed"] = True
    return result


# ============================================================
# AI AGENT
# ============================================================

def analyze_ticket(
    incident_number
):

    print(
        "\n=== AI TICKET AGENT ===\n"
    )

    instructions = """
You are an AI Agent supporting a Level 2 workflow
application support team.

Your job is to investigate support tickets using
approved tools and product documentation.

Follow these rules:

1. Retrieve the ticket using get_ticket.
2. Identify the source system.
3. Identify the issue and error message.
4. Search product documentation using
   search_product_docs.
5. If documentation identifies Gateway or Backend
   Roles, verify them using verify_roles.
6. Never invent role IDs.
7. Never infer a role ID only from the source system.
8. Never grant access.
9. Never create an access request on behalf of the user.
10. The user must request access through the designated
    access-management process.
11. If documented required roles are identified,
    recommend that the user check/request them.
12. If access is confirmed as the issue, recommend
    putting the ticket On Hold while waiting for the
    user's access request.
13. Human L2 approval is required before changing
    ticket status.
14. You cannot change ticket status yourself.
15. Clearly distinguish:
    - information retrieved from tools
    - analysis
    - recommendation
16. Do not claim that a role is missing from the user's
    account because the ticket does not contain the
    user's actual assigned roles.
17. Do not claim that access has been granted.
"""

    # --------------------------------------------------------
    # Initial input
    # --------------------------------------------------------

    input_items = [

        {
            "role": "user",

            "content": (
                f"Analyze support ticket "
                f"{incident_number}. "
                f"Retrieve the ticket and investigate "
                f"the issue using the available product "
                f"knowledge and role verification tools."
            )
        }
    ]

    max_iterations = 10

    # --------------------------------------------------------
    # Agent tool loop
    # --------------------------------------------------------

    for iteration in range(
        max_iterations
    ):

        print(
            f"\n--- Agent iteration "
            f"{iteration + 1} ---"
        )

        # pyrefly: ignore [no-matching-overload]
        response = client.responses.create(

            model="gpt-5.6-luna",

            instructions=instructions,

            tools=TOOLS,

            input=input_items
        )

        # Keep model output
        input_items += response.output

        tool_calls_found = False

        # ----------------------------------------------------
        # Execute requested tools
        # ----------------------------------------------------

        for item in response.output:

            if item.type != "function_call":

                continue

            tool_calls_found = True

            tool_name = item.name

            arguments = json.loads(
                item.arguments
            )

            print(
                f"\nTool call: {tool_name}"
            )

            print(
                "Arguments:"
            )

            print(
                json.dumps(
                    arguments,
                    indent=2,
                    ensure_ascii=False
                )
            )

            result = execute_tool(
                tool_name,
                arguments
            )

            print(
                "Tool result:"
            )

            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False
                )
            )

            input_items.append(

                {
                    "type": "function_call_output",

                    "call_id": item.call_id,

                    "output": json.dumps(
                        result,
                        ensure_ascii=False
                    )
                }
            )

        # ----------------------------------------------------
        # Agent finished
        # ----------------------------------------------------

        if not tool_calls_found:

            print(
                "\n=== FINAL AI ANALYSIS ===\n"
            )

            print(
                response.output_text
            )

            break

    else:

        print(
            "\nAgent stopped: "
            "maximum tool iterations reached."
        )

        return

    # ========================================================
    # EVIDENCE GATE — only proceed to L2 approval if the
    # AI analysis supports an access-related On Hold action
    # ========================================================

    final_text = response.output_text or ""
    analysis = final_text.lower()

    no_documentation = (
        "no matching documentation" in analysis
        or "no relevant documentation" in analysis
        or "no relevant product knowledge" in analysis
        or "documentation search returned no" in analysis
        or "no matching documentation was returned" in analysis
    )

    insufficient_evidence = (
        "evidence is insufficient" in analysis
        or "cannot determine" in analysis
        or "not possible to confirm" in analysis
        or "cannot be established as the cause" in analysis
        or "no ticket status change is recommended" in analysis
        or "further investigation" in analysis
    )

    access_evidence = (
        "required roles" in analysis
        or "required role" in analysis
        or "access-related issue" in analysis
        or "authorization issue" in analysis
        or "on hold" in analysis
    )

    if no_documentation or insufficient_evidence or not access_evidence:
        print(
            "\n" + "=" * 50
        )
        print(
            "L2 ACTION: CONTINUE INVESTIGATION"
        )
        print(
            "=" * 50
        )
        print(
            "\nReason: The available evidence does not "
            "sufficiently support placing the ticket "
            "On Hold for an access request."
        )
        print(
            "Ticket status was NOT changed."
        )
        return

    # ========================================================
    # CHECK CURRENT TICKET STATUS
    # ========================================================

    current_ticket = get_ticket(incident_number)

    if current_ticket.get("status") == "On Hold":
        print(
            "\nTicket is already On Hold."
        )
        print(
            "No status update is necessary."
        )
        return

    # ========================================================
    # HUMAN L2 APPROVAL
    # ========================================================

    print(
        "\n=================================================="
    )

    print(
        "L2 HUMAN APPROVAL REQUIRED"
    )

    print(
        "=================================================="
    )

    print(
        "\nThe AI recommends putting the ticket "
        "On Hold while waiting for the user "
        "to request/verify access."
    )

    approval = input(
        "\nApprove and update ticket to "
        "'On Hold'? (yes/no): "
    ).strip().lower()

    # --------------------------------------------------------
    # Reject
    # --------------------------------------------------------

    if approval != "yes":

        print(
            "\nAction cancelled by L2."
        )

        print(
            "Ticket status was NOT changed."
        )

        return

    # --------------------------------------------------------
    # Human approved
    # --------------------------------------------------------

    print(
        "\nL2 approved the recommendation."
    )

    print(
        "Updating ticket..."
    )

    result = update_ticket(
        incident_number=incident_number,
        status="On Hold"
    )

    updated_ticket = result[
        "ticket"
    ]

    print(
        "\nTicket updated successfully."
    )

    print(
        f"Incident: "
        f"{updated_ticket['incident_number']}"
    )

    print(
        f"New status: "
        f"{updated_ticket['status']}"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    incident_number = input("Enter incident number: ").strip()

    analyze_ticket(incident_number)