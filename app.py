"""
AI Ticket Investigation Workspace
Streamlit entry point: streamlit run app.py

Thesis prototype demonstrating an AI Agent supporting
Level 2 product support ticket investigation.
"""

import requests
import streamlit as st

from agent.llm_agent import run_investigation, update_ticket

# ──────────────────────────────────────────────────────────────
# PAGE CONFIG
# ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="AI Ticket Investigation Workspace",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────────────────────
# CUSTOM CSS
# ──────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* ── Global ── */
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background-color: #f0f4f8; }
.block-container { padding-top: 1.5rem; padding-bottom: 3rem; }

/* ── Workspace header ── */
.ws-header {
    background: linear-gradient(135deg, #1e3a5f 0%, #2d5a8e 100%);
    border-radius: 12px;
    padding: 22px 28px 18px;
    margin-bottom: 20px;
    color: white;
}
.ws-header-title {
    font-size: 1.4rem;
    font-weight: 700;
    color: white;
    margin: 0 0 4px 0;
    letter-spacing: -0.02em;
}
.ws-header-sub {
    font-size: 0.84rem;
    color: rgba(255,255,255,0.72);
    margin: 0;
}
.ws-header-synthetic {
    display: inline-block;
    background: rgba(255,255,255,0.15);
    color: rgba(255,255,255,0.85);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-top: 8px;
}

/* ── Metric cards ── */
.metric-row { display: flex; gap: 12px; margin-bottom: 20px; }
.metric-card {
    flex: 1;
    background: white;
    border-radius: 10px;
    padding: 16px 18px;
    border: 1px solid #d1d9e6;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
.metric-val {
    font-size: 1.9rem;
    font-weight: 700;
    color: #1e3a5f;
    line-height: 1;
    margin-bottom: 5px;
}
.metric-lbl {
    font-size: 0.74rem;
    color: #64748b;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.metric-card.m-active .metric-val { color: #b45309; }
.metric-card.m-hold .metric-val { color: #1d4ed8; }
.metric-card.m-resolved .metric-val { color: #15803d; }

/* ── Status badges ── */
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.76rem;
    font-weight: 600;
    white-space: nowrap;
    letter-spacing: 0.01em;
}
.s-InProgress { background:#fef3c7; color:#b45309; }
.s-OnHold     { background:#dbeafe; color:#1d4ed8; }
.s-New        { background:#fee2e2; color:#dc2626; }
.s-Resolved   { background:#dcfce7; color:#15803d; }
.s-Closed     { background:#f1f5f9; color:#475569; }
.p-P1         { background:#fee2e2; color:#dc2626; }
.p-P2         { background:#ffedd5; color:#ea580c; }
.p-P3         { background:#fef3c7; color:#b45309; }
.p-P4         { background:#dcfce7; color:#15803d; }

/* ── Cards ── */
.card {
    background: white;
    border-radius: 12px;
    border: 1px solid #d1d9e6;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    padding: 20px 24px;
    margin-bottom: 14px;
}
.card-title {
    font-size: 0.74rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: #64748b;
    margin: 0 0 14px 0;
    padding-bottom: 10px;
    border-bottom: 1px solid #f0f4f8;
}

/* ── Ticket header card ── */
.ticket-id-label {
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: #94a3b8;
    margin-bottom: 4px;
}
.ticket-summary-title {
    font-size: 1.2rem;
    font-weight: 700;
    color: #1a202c;
    margin: 0 0 12px 0;
    line-height: 1.35;
}
.ticket-badges { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.ticket-meta-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 12px 20px;
    padding-top: 14px;
    border-top: 1px solid #f0f4f8;
}
.meta-item label {
    display: block;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #94a3b8;
    margin-bottom: 3px;
}
.meta-item span {
    font-size: 0.88rem;
    color: #1a202c;
    font-weight: 500;
}
.error-box {
    background: #fafafa;
    border: 1px solid #e2e8f0;
    border-left: 3px solid #e53e3e;
    border-radius: 6px;
    padding: 10px 14px;
    font-family: 'Courier New', monospace;
    font-size: 0.82rem;
    color: #374151;
    margin-top: 12px;
    word-break: break-word;
}
.desc-text {
    font-size: 0.9rem;
    color: #374151;
    line-height: 1.65;
    margin-top: 10px;
}

/* ── Timeline ── */
.tl-item {
    display: flex;
    gap: 14px;
    margin-bottom: 10px;
    align-items: flex-start;
}
.tl-dot {
    width: 28px; height: 28px;
    border-radius: 50%;
    background: #1e3a5f;
    color: white;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.72rem; font-weight: 700;
    flex-shrink: 0;
    margin-top: 2px;
}
.tl-body {
    flex: 1;
    background: white;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 10px 14px;
}
.tl-body.found { border-left: 3px solid #15803d; }
.tl-body.empty { border-left: 3px solid #f59e0b; }
.tl-body.neutral { border-left: 3px solid #94a3b8; }
.tl-tool { font-size: 0.82rem; font-weight: 700; color: #1e3a5f; margin-bottom: 3px; }
.tl-summary { font-size: 0.8rem; color: #64748b; line-height: 1.4; }

/* ── Evidence card ── */
.ev-row {
    display: flex; align-items: baseline; gap: 10px;
    margin-bottom: 9px; font-size: 0.87rem;
}
.ev-icon { font-size: 0.95rem; flex-shrink: 0; }
.ev-label { font-weight: 600; color: #374151; min-width: 190px; }
.ev-val { color: #1a202c; }
.ev-found { color: #15803d; }
.ev-missing { color: #b45309; }
.role-pill {
    display: inline-block;
    background: #e0e7ff; color: #3730a3;
    border-radius: 5px; padding: 1px 8px;
    font-size: 0.75rem; font-family: monospace;
    font-weight: 600; margin: 2px 3px 2px 0;
}
.uncertainty-note {
    background: #f8fafc; border: 1px solid #e2e8f0;
    border-radius: 7px; padding: 9px 13px;
    font-size: 0.8rem; color: #64748b;
    margin-top: 12px; line-height: 1.55;
}

/* ── Decision panel ── */
.dp {
    border-radius: 12px;
    padding: 18px 22px;
    margin-bottom: 14px;
}
.dp.dp-action { background:#fffbeb; border:1px solid #fcd34d; }
.dp.dp-hold   { background:#eff6ff; border:1px solid #bfdbfe; }
.dp.dp-ok     { background:#f0fdf4; border:1px solid #86efac; }
.dp.dp-info   { background:#f8fafc; border:1px solid #cbd5e1; }
.dp.dp-cont   { background:#f0f9ff; border:1px solid #bae6fd; }
.dp-title { font-size: 0.95rem; font-weight: 700; margin: 0 0 6px 0; color: #1a202c; }
.dp-body  { font-size: 0.86rem; color: #374151; line-height: 1.55; margin: 0; }

/* ── Empty / landing state ── */
.landing {
    text-align: center;
    padding: 80px 20px;
    color: #94a3b8;
}
.landing-icon { font-size: 3.5rem; margin-bottom: 18px; }
.landing h3   { color: #475569; font-size: 1.1rem; margin-bottom: 8px; }
.landing p    { font-size: 0.88rem; max-width: 400px; margin: 0 auto; line-height: 1.6; }

/* ── Sidebar ── */
[data-testid="stSidebar"] { background-color: #f8fafc !important; }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { font-size: 0.82rem; }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────

BASE_URL = "http://127.0.0.1:5000"

STATUS_CSS = {
    "In Progress": "s-InProgress",
    "On Hold":     "s-OnHold",
    "New":         "s-New",
    "Resolved":    "s-Resolved",
    "Closed":      "s-Closed",
}
STATUS_ICON = {
    "In Progress": "●", "On Hold": "◉",
    "New": "○", "Resolved": "✓", "Closed": "✕",
}
PRIORITY_CSS = {"P1": "p-P1", "P2": "p-P2", "P3": "p-P3", "P4": "p-P4"}
PRIORITY_ICON = {"P1": "🔴", "P2": "🟠", "P3": "🟡", "P4": "🟢"}

TOOL_ICON = {
    "get_ticket": "🎫",
    "search_product_docs": "📚",
    "verify_roles": "🔐",
}

# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────

def fetch_tickets() -> list:
    try:
        r = requests.get(f"{BASE_URL}/api/tickets", timeout=5)
        r.raise_for_status()
        return r.json().get("tickets", [])
    except Exception as exc:
        st.error(f"⚠️ Could not reach Mock API — {exc}")
        return []


def sbadge(status: str) -> str:
    css = STATUS_CSS.get(status, "s-Closed")
    icon = STATUS_ICON.get(status, "●")
    return f'<span class="badge {css}">{icon} {status}</span>'


def pbadge(priority: str) -> str:
    css = PRIORITY_CSS.get(priority, "")
    return f'<span class="badge {css}">{priority}</span>'


def summarize_tool_call(tc: dict) -> tuple[str, str]:
    """Return (summary_text, css_modifier: found | empty | neutral)."""
    name = tc["name"]
    args = tc.get("arguments", {})
    res  = tc.get("result")

    if name == "get_ticket":
        inc = args.get("incident_number", "?")
        if isinstance(res, dict):
            return (
                f"Retrieved {inc} · Status: {res.get('status','?')} · System: {res.get('system','?')}",
                "neutral",
            )
        return f"Attempted to retrieve {inc}", "neutral"

    if name == "search_product_docs":
        system = args.get("source_system", "?")
        if isinstance(res, list) and res:
            ids = ", ".join(d.get("document_id", "?") for d in res)
            return f"Found {len(res)} document(s) for {system}: {ids}", "found"
        return f"No matching documentation found for {system}", "empty"

    if name == "verify_roles":
        system = args.get("source_system", "?")
        if isinstance(res, dict):
            gw = res.get("gateway_roles", [])
            be = res.get("backend_roles", [])
            return (
                f"Verified {len(gw)} gateway role(s) and {len(be)} backend role(s) for {system}",
                "found" if (gw or be) else "empty",
            )
        return f"Role verification for {system}", "neutral"

    return f"Called {name}", "neutral"


def extract_evidence(result: dict) -> tuple[list, dict | None]:
    """Extract docs_found and roles_verified from agent steps."""
    docs_found: list = []
    roles_verified: dict | None = None
    for step in result.get("steps", []):
        for tc in step.get("tool_calls", []):
            if tc["name"] == "search_product_docs" and isinstance(tc.get("result"), list):
                docs_found.extend(tc["result"])
            elif tc["name"] == "verify_roles" and isinstance(tc.get("result"), dict):
                roles_verified = tc["result"]
    return docs_found, roles_verified


def get_evidence_level(result: dict) -> str:
    if result.get("approval_needed") or result.get("already_on_hold"):
        return "sufficient"
    return "insufficient"


# ──────────────────────────────────────────────────────────────
# SESSION STATE
# ──────────────────────────────────────────────────────────────

for key, default in [
    ("selected_ticket", None),
    ("investigation", None),
    ("investigating", False),
    ("approval_done", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ──────────────────────────────────────────────────────────────
# DATA — fetch once per render
# ──────────────────────────────────────────────────────────────

tickets = fetch_tickets()

# ──────────────────────────────────────────────────────────────
# SIDEBAR — TICKET QUEUE
# ──────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### 🎫 Ticket Queue")
    st.caption("Workflow L2 Support · Synthetic demo data")

    col_r, col_title = st.columns([1, 3])
    with col_r:
        if st.button("⟳", help="Refresh ticket list", use_container_width=True):
            st.session_state.selected_ticket = None
            st.session_state.investigation   = None
            st.session_state.approval_done   = None
            st.rerun()

    st.divider()

    if tickets:
        search = st.text_input(
            "Search", placeholder="Incident # or keyword…",
            label_visibility="collapsed",
        )

        col_s, col_sys = st.columns(2)
        with col_s:
            statuses = sorted({t.get("status", "") for t in tickets})
            f_status = st.selectbox("Status", ["All"] + statuses,
                                    label_visibility="collapsed")
        with col_sys:
            systems = sorted({t.get("system", "") for t in tickets})
            f_system = st.selectbox("System", ["All"] + systems,
                                    label_visibility="collapsed")

        filtered = [
            t for t in tickets
            if (f_status == "All" or t.get("status") == f_status)
            and (f_system == "All" or t.get("system") == f_system)
            and (
                not search
                or search.lower() in t.get("incident_number", "").lower()
                or search.lower() in t.get("summary", "").lower()
                or search.lower() in t.get("system", "").lower()
            )
        ]

        st.caption(f"{len(filtered)} of {len(tickets)} tickets")
        st.divider()

        for t in filtered:
            inc = t["incident_number"]
            is_sel = (
                st.session_state.selected_ticket is not None
                and st.session_state.selected_ticket["incident_number"] == inc
            )
            p_icon = PRIORITY_ICON.get(t.get("priority", ""), "⚪")
            s_icon = STATUS_ICON.get(t.get("status", ""), "●")
            label = (
                f"{p_icon} **{inc}**  \n"
                f"{s_icon} {t.get('status','')} · `{t.get('system','')}`  \n"
                f"_{t.get('summary','')[:38]}_"
            )
            if st.button(
                label,
                key=f"tkt_{inc}",
                use_container_width=True,
                type="primary" if is_sel else "secondary",
            ):
                if not is_sel:
                    st.session_state.selected_ticket = t
                    st.session_state.investigation   = None
                    st.session_state.approval_done   = None
                    st.rerun()
    else:
        st.warning("No tickets found.\nIs the Mock API running?")

# ──────────────────────────────────────────────────────────────
# MAIN — HEADER + METRICS
# ──────────────────────────────────────────────────────────────

st.markdown("""
<div class="ws-header">
  <div class="ws-header-title">🔍 AI Ticket Investigation Workspace</div>
  <div class="ws-header-sub">AI-assisted L2 incident investigation and decision support</div>
  <div class="ws-header-synthetic">⚠ Synthetic / Demo Data</div>
</div>
""", unsafe_allow_html=True)

# Metrics — calculated from live ticket data
if tickets:
    total     = len(tickets)
    active    = sum(1 for t in tickets if t.get("status") in ("In Progress", "New"))
    on_hold   = sum(1 for t in tickets if t.get("status") == "On Hold")
    resolved  = sum(1 for t in tickets if t.get("status") in ("Resolved", "Closed"))

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown(f"""
        <div class="metric-card">
          <div class="metric-val">{total}</div>
          <div class="metric-lbl">Total Tickets</div>
        </div>""", unsafe_allow_html=True)
    with m2:
        st.markdown(f"""
        <div class="metric-card m-active">
          <div class="metric-val">{active}</div>
          <div class="metric-lbl">Active / Open</div>
        </div>""", unsafe_allow_html=True)
    with m3:
        st.markdown(f"""
        <div class="metric-card m-hold">
          <div class="metric-val">{on_hold}</div>
          <div class="metric-lbl">On Hold</div>
        </div>""", unsafe_allow_html=True)
    with m4:
        st.markdown(f"""
        <div class="metric-card m-resolved">
          <div class="metric-val">{resolved}</div>
          <div class="metric-lbl">Resolved / Closed</div>
        </div>""", unsafe_allow_html=True)

st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────
# LANDING — no ticket selected
# ──────────────────────────────────────────────────────────────

if st.session_state.selected_ticket is None:
    st.markdown("""
    <div class="landing">
      <div class="landing-icon">🔍</div>
      <h3>Select a ticket to begin</h3>
      <p>Choose an incident from the queue on the left to view details
         and run an AI-assisted investigation.</p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ──────────────────────────────────────────────────────────────
# WORKSPACE — ticket selected
# ──────────────────────────────────────────────────────────────

ticket = st.session_state.selected_ticket
inc    = ticket["incident_number"]
reported = ticket.get("reported_by", {})

# ── Ticket header card ────────────────────────────────────────

st.markdown(f"""
<div class="card">
  <div class="ticket-id-label">{inc}</div>
  <div class="ticket-summary-title">{ticket.get('summary', '—')}</div>
  <div class="ticket-badges">
    {sbadge(ticket.get('status','—'))}
    {pbadge(ticket.get('priority','—'))}
    <span class="badge" style="background:#f1f5f9;color:#475569;">
      {ticket.get('system','—')}
    </span>
    <span class="badge" style="background:#f1f5f9;color:#475569;">
      {ticket.get('category','—')}
    </span>
  </div>
  <div class="ticket-meta-grid">
    <div class="meta-item">
      <label>Incident Type</label>
      <span>{ticket.get('incident_type','—')}</span>
    </div>
    <div class="meta-item">
      <label>Assignment Group</label>
      <span>{ticket.get('assignment_group','—')}</span>
    </div>
    <div class="meta-item">
      <label>Impact</label>
      <span>{ticket.get('impact','—')}</span>
    </div>
    <div class="meta-item">
      <label>Urgency</label>
      <span>{ticket.get('urgency','—')}</span>
    </div>
    <div class="meta-item">
      <label>Reported By</label>
      <span>{reported.get('user_id','—')} ({reported.get('email','—')})</span>
    </div>
    <div class="meta-item">
      <label>Created</label>
      <span>{ticket.get('created_datetime','—')}</span>
    </div>
  </div>
  <div class="desc-text">{ticket.get('description','—')}</div>
  {"<div class='error-box'>" + ticket['test_detail'] + "</div>" if ticket.get('test_detail') else ""}
</div>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────
# PRE-INVESTIGATION — run button
# ──────────────────────────────────────────────────────────────

if st.session_state.investigation is None:
    st.markdown(
        "<div class='card-title' style='margin-top:8px'>AI Investigation</div>",
        unsafe_allow_html=True,
    )
    st.info(
        "**Ready to investigate.** The AI agent will retrieve the ticket, "
        "search product documentation, and verify required roles. "
        "All status changes require explicit L2 approval.",
        icon="ℹ️",
    )
    if st.button(
        "▶  Run AI Investigation",
        type="primary",
        use_container_width=True,
        disabled=st.session_state.investigating,
        key="run_btn",
    ):
        st.session_state.investigating  = True
        st.session_state.approval_done  = None

        with st.spinner(f"Investigating {inc}… this may take 30–60 seconds."):
            result = run_investigation(inc)

        st.session_state.investigation  = result
        st.session_state.investigating  = False
        st.rerun()

    st.stop()

# ──────────────────────────────────────────────────────────────
# POST-INVESTIGATION
# ──────────────────────────────────────────────────────────────

result = st.session_state.investigation

# ── Error state ──────────────────────────────────────────────

if result.get("error"):
    st.markdown(f"""
    <div class="card">
      <div class="card-title">Investigation Error</div>
      <div class="error-box">{result['error']}</div>
    </div>""", unsafe_allow_html=True)
    c1, c2 = st.columns([1, 5])
    with c1:
        if st.button("Retry", type="secondary", use_container_width=True):
            st.session_state.investigation = None
            st.rerun()
    st.stop()

# ── Derive evidence from steps ───────────────────────────────

docs_found, roles_verified = extract_evidence(result)
evidence_level = get_evidence_level(result)

# ── 1. Investigation Timeline ─────────────────────────────────

st.markdown("<div class='card-title'>Investigation Timeline</div>", unsafe_allow_html=True)

all_calls = [
    (step_i + 1, tc)
    for step_i, step in enumerate(result.get("steps", []))
    for tc in step.get("tool_calls", [])
]

if not all_calls:
    st.caption("No tool calls were recorded.")
else:
    for step_num, tc in all_calls:
        summary, css_mod = summarize_tool_call(tc)
        tool_icon = TOOL_ICON.get(tc["name"], "🔧")

        st.markdown(f"""
        <div class="tl-item">
          <div class="tl-dot">{step_num}</div>
          <div class="tl-body {css_mod}">
            <div class="tl-tool">{tool_icon} {tc['name']}</div>
            <div class="tl-summary">{summary}</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander(f"Raw output — {tc['name']}", expanded=False):
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**Arguments**")
                st.json(tc.get("arguments", {}))
            with col_b:
                st.markdown("**Result**")
                st.json(tc.get("result", {}))

# ── 2. Evidence Assessment ────────────────────────────────────

st.markdown("<div class='card-title'>Evidence Assessment</div>", unsafe_allow_html=True)

ev_rows = ""

# Documentation
if docs_found:
    doc_list = ", ".join(
        f"<code>{d.get('document_id','?')}</code> ({d.get('topic','?')})"
        for d in docs_found
    )
    ev_rows += f"""
    <div class="ev-row">
      <div class="ev-icon">✅</div>
      <div class="ev-label">Matching documentation</div>
      <div class="ev-val ev-found">{doc_list}</div>
    </div>"""
else:
    ev_rows += """
    <div class="ev-row">
      <div class="ev-icon">⚠️</div>
      <div class="ev-label">Matching documentation</div>
      <div class="ev-val ev-missing">No documentation found for this system / error combination</div>
    </div>"""

# Required roles (from documentation)
if docs_found:
    all_gw = [r["role_id"] for d in docs_found for r in d.get("gateway_roles", [])]
    all_be = [r["role_id"] for d in docs_found for r in d.get("backend_roles", [])]
    gw_pills = "".join(f'<span class="role-pill">{r}</span>' for r in all_gw) if all_gw else "<em style='color:#94a3b8'>None</em>"
    be_pills = "".join(f'<span class="role-pill">{r}</span>' for r in all_be) if all_be else "<em style='color:#94a3b8'>None</em>"
    ev_rows += f"""
    <div class="ev-row">
      <div class="ev-icon">📋</div>
      <div class="ev-label">Documented gateway roles</div>
      <div class="ev-val">{gw_pills}</div>
    </div>
    <div class="ev-row">
      <div class="ev-icon">📋</div>
      <div class="ev-label">Documented backend roles</div>
      <div class="ev-val">{be_pills}</div>
    </div>"""

# Verified roles (from verify_roles tool)
if roles_verified:
    vgw = roles_verified.get("gateway_roles", [])
    vbe = roles_verified.get("backend_roles", [])
    vgw_pills = "".join(f'<span class="role-pill">{r["role_id"]}</span>' for r in vgw) if vgw else "<em style='color:#94a3b8'>None</em>"
    vbe_pills = "".join(f'<span class="role-pill">{r["role_id"]}</span>' for r in vbe) if vbe else "<em style='color:#94a3b8'>None</em>"
    ev_rows += f"""
    <div class="ev-row">
      <div class="ev-icon">🔐</div>
      <div class="ev-label">Verified gateway roles</div>
      <div class="ev-val ev-found">{vgw_pills}</div>
    </div>
    <div class="ev-row">
      <div class="ev-icon">🔐</div>
      <div class="ev-label">Verified backend roles</div>
      <div class="ev-val ev-found">{vbe_pills}</div>
    </div>"""

# Evidence level
if evidence_level == "sufficient":
    ev_rows += """
    <div class="ev-row">
      <div class="ev-icon">✅</div>
      <div class="ev-label">Evidence sufficiency</div>
      <div class="ev-val ev-found"><strong>Sufficient</strong> — access-related cause identified</div>
    </div>"""
else:
    ev_rows += """
    <div class="ev-row">
      <div class="ev-icon">⚠️</div>
      <div class="ev-label">Evidence sufficiency</div>
      <div class="ev-val ev-missing"><strong>Insufficient</strong> — access cause not confirmed</div>
    </div>"""

st.markdown(f"""
<div class="card">
  {ev_rows}
  <div class="uncertainty-note">
    ℹ️ <strong>Uncertainty note:</strong> This ticket does not include the user's currently
    assigned roles. The agent can identify <em>documented required roles</em> from product
    knowledge but cannot confirm whether the user holds them. Role verification confirms
    what the system defines as required — not what the user currently has.
  </div>
</div>
""", unsafe_allow_html=True)

# ── 3. AI Analysis & Recommendation ──────────────────────────

st.markdown("<div class='card-title'>AI Analysis &amp; Recommendation</div>", unsafe_allow_html=True)

final_analysis = result.get("final_analysis", "")

if final_analysis:
    # Show a contained, scrollable analysis block then full text in expander
    st.markdown(
        f"<div class='card' style='max-height:340px;overflow-y:auto;'>"
        f"</div>",
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        st.markdown(final_analysis)
else:
    st.warning("No final analysis was generated (agent reached maximum iterations).")

# ── 4. L2 Decision Panel ─────────────────────────────────────

st.markdown("<div class='card-title'>L2 Decision</div>", unsafe_allow_html=True)

approval_done = st.session_state.approval_done

if approval_done == "approved":
    st.markdown("""
    <div class="dp dp-ok">
      <div class="dp-title">✅ Status Updated — On Hold</div>
      <div class="dp-body">The ticket has been moved to <strong>On Hold</strong>
      pending the user's access request. No further action is needed at this stage.</div>
    </div>""", unsafe_allow_html=True)

elif approval_done == "rejected":
    st.markdown("""
    <div class="dp dp-info">
      <div class="dp-title">🚫 Update Rejected</div>
      <div class="dp-body">The proposed status change was rejected by L2.
      The ticket status remains unchanged. Continue investigation or escalate as needed.</div>
    </div>""", unsafe_allow_html=True)

elif result.get("already_on_hold"):
    st.markdown(f"""
    <div class="dp dp-hold">
      <div class="dp-title">ℹ️ Already On Hold</div>
      <div class="dp-body">Ticket <strong>{inc}</strong> is already in <strong>On Hold</strong>
      status. No duplicate status update is required.</div>
    </div>""", unsafe_allow_html=True)

elif result.get("approval_needed"):
    st.markdown("""
    <div class="dp dp-action">
      <div class="dp-title">⚠️ L2 Approval Required</div>
      <div class="dp-body">
        The AI agent has identified sufficient evidence of an access-related issue
        and recommends placing this ticket <strong>On Hold</strong> while the user
        requests or verifies the required roles through the designated access management process.<br><br>
        <strong>This action requires explicit L2 approval. Review the evidence and analysis above
        before deciding.</strong>
      </div>
    </div>""", unsafe_allow_html=True)

    col_approve, col_reject, col_spacer = st.columns([2, 2, 4])

    with col_approve:
        if st.button(
            "✅  Approve — Move to On Hold",
            type="primary",
            use_container_width=True,
            key="btn_approve",
        ):
            try:
                update_ticket(inc, "On Hold")
                st.session_state.approval_done = "approved"
                refreshed = requests.get(f"{BASE_URL}/api/tickets/{inc}", timeout=5)
                if refreshed.ok:
                    st.session_state.selected_ticket = refreshed.json()
                st.rerun()
            except Exception as exc:
                st.error(f"Update failed: {exc}")

    with col_reject:
        if st.button(
            "🚫  Reject",
            type="secondary",
            use_container_width=True,
            key="btn_reject",
        ):
            st.session_state.approval_done = "rejected"
            st.rerun()

else:
    st.markdown("""
    <div class="dp dp-cont">
      <div class="dp-title">🔄 Continue Investigation</div>
      <div class="dp-body">
        The available evidence does not sufficiently support placing this ticket On Hold.
        Possible reasons: no matching documentation was found for the reported system and error,
        the error does not indicate an access-related cause, or further investigation is needed.
        <br><br>Ticket status has not been changed.
      </div>
    </div>""", unsafe_allow_html=True)

# ── Re-run ────────────────────────────────────────────────────

st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
if st.button(
    "🔄  Re-run Investigation",
    use_container_width=True,
    key="btn_rerun",
):
    st.session_state.investigation = None
    st.session_state.approval_done = None
    st.rerun()
