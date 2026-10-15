import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity, AlertCircle, ArrowDownToLine, ArrowRight, Bot, BookOpen, Check, Copy, Mail,
  CheckCircle2, ChevronRight, Clock3, FileCheck2, LoaderCircle, RotateCcw,
  MessageCircle, Search, Send, ShieldCheck, Sparkles, TicketCheck, UserRound,
  X,
} from 'lucide-react'
import { buildEditableResolutionForm, buildResolutionPrefill, canSaveVerifiedResolution } from './resolutionPrefill'
import { canContactCustomer, formatEmailDraftForCopy, isCustomerCommunicationOpen, updateEmailDraft, updateTicketCommunicationState } from './customerCommunication'
import { createBrowserDemoApi } from './demoApi'

const browserDemoApi = createBrowserDemoApi({
  fetchImpl:(...args) => fetch(...args),
  storage:window.localStorage,
  locks:window.navigator?.locks,
  onUnauthorized:() => window.dispatchEvent(new Event('ticket-agent-auth-required')),
})

const requestDirectApi = async (url, options = {}) => {
  const response = await fetch(url, {
    cache: options.cache ?? 'no-store',
    credentials:'same-origin',
    ...options,
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(payload.error || `Request failed (${response.status})`)
    error.payload = payload
    error.status = response.status
    throw error
  }
  return payload
}

const api = (url, options = {}) => {
  if (!import.meta.env.PROD) return requestDirectApi(url, options)
  return browserDemoApi(url, options)
}
const statusLabel = (ticket) => ticket.status_label || (typeof ticket.status === 'number' ? ({ 1:'New',2:'In Progress',3:'On Hold',4:'Resolved',5:'Closed',6:'Cancelled' }[ticket.status] || 'Unknown') : ticket.status)
const summary = (ticket) => ticket.short_description || ticket.summary || 'Incident details unavailable'
const source = (ticket) => ticket.source_system || ticket.system || 'Unknown system'
const issueText = (ticket) => ticket.additional_comments || ticket.description || ticket.test_detail || 'No additional comments provided.'
const errorText = (ticket) => ticket.error_message || ticket.test_detail || ticket.test || ''
const titleCase = (value) => value || '—'

function Badge({ children, tone = 'slate' }) { return <span className={`badge badge-${tone}`}>{children}</span> }
function Field({ label, value }) { return <div><div className="field-label">{label}</div><div className="field-value">{value || '—'}</div></div> }

function DemoAccessGate({ onLogin }) {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError('')
    try { await onLogin(code); setCode('') }
    catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }
  return <main className="access-screen"><section className="access-card">
    <div className="access-mark"><Sparkles size={20}/></div>
    <span className="eyebrow">ACADEMIC DEMONSTRATION</span>
    <h1>Ticket Agent</h1>
    <p>Enter the Demo Access Code provided by your supervisor to continue.</p>
    <form onSubmit={submit}>
      <label htmlFor="demo-access-code">Demo Access Code</label>
      <input id="demo-access-code" type="password" autoComplete="current-password" value={code} onChange={(event)=>setCode(event.target.value)} required maxLength={256}/>
      {error && <div className="inline-error"><AlertCircle size={15}/>{error}</div>}
      <button className="primary-button wide-button" disabled={busy || !code}>{busy ? 'Verifying…' : 'Continue to workspace'}</button>
    </form>
    <small>Access expires automatically after four hours.</small>
  </section></main>
}

function InvestigationResult({ ticket, investigation, aiProvenance, aiModel, onContactCustomer, communicationOpen }) {
  const [copiedRole, setCopiedRole] = useState('')
  const evidence = investigation?.evidence?.unified_inbox || {}
  const scenario = evidence.scenario || investigation?.scenario_evidence || {}
  const sourceRecord = evidence.source_system_record || {}
  const assessment = investigation?.root_cause_assessment || {}
  const capturedKnowledge = investigation?.captured_knowledge_used || []
  const issue = ticket.issue_type || ticket.issue || ticket.category || ''
  const sourceSystem = ticket.source_system || ticket.system || 'Unknown system'
  const verified = assessment.state === 'consultant_verified' && assessment.verified === true
  const roleType = scenario.expected_missing_role_type || ticket.expected_missing_role_type || ''
  const roleId = roleType === 'Gateway'
    ? (scenario.gateway_role_id || ticket.gateway_role_id || '')
    : roleType === 'Backend'
      ? (scenario.backend_role_id || ticket.backend_role_id || '')
      : ''
  const workflow = evidence.workflow_capability || (sourceRecord.workflow_capabilities || []).find((item) => item.workflow_id === ticket.workflow_id)
  const operational = evidence.operational_incident
  const requestedLanguage = scenario.requested_language || ticket.requested_language
  const languages = evidence.supported_task_content_languages || []
  const alternatives = languages.filter((language) => language !== requestedLanguage)
  const issueHeading = {
    'Task Not Visible': roleType ? `${sourceSystem} ${roleType} Role to Verify` : 'Approval Task Not Visible',
    'Loading Time Too Slow': 'Source-System Incident May Affect Loading',
    'Cannot Set Up Substitution': 'Workflow Does Not Support Substitution',
    'Cannot See Specific Language': requestedLanguage ? `${requestedLanguage} Not Available in Task Content` : 'Requested Language Not Available',
  }[issue] || assessment.statement || 'Cause not established from available evidence'

  const operationalWindow = operational
    ? [operational.scheduled_start_at, operational.scheduled_end_at || operational.expected_recovery_at].filter(Boolean).join(' – ')
    : ''
  const timezone = sourceRecord.timezone || 'Europe/Berlin'
  const rawOperationalStatus = operational?.effective_status_at_simulation || operational?.incident_status_as_recorded
  const operationalStatus = rawOperationalStatus?.replaceAll('_', ' ').replace(/^\w/, (letter) => letter.toUpperCase())

  let resolutionLabel = ''
  let resolutionDetail = ''
  if (issue === 'Task Not Visible' && roleId) {
    resolutionLabel = `Required ${roleType} Role`
    resolutionDetail = `Compare the user's current assignments with this documented role. If an authorized check confirms it is absent, guide the user through the approved access-request process, then retest the task.`
  } else if (issue === 'Loading Time Too Slow') {
    resolutionLabel = 'Operational Incident Status'
    const statusText = operationalStatus ? `Status: ${operationalStatus}. ` : 'Current incident status is unavailable. '
    const windowText = operationalWindow ? `Downtime window: ${operationalWindow} (${timezone}). ` : `Germany-time downtime window is unavailable in the retrieved incident data. `
    resolutionDetail = `${statusText}${windowText}Confirm current status and recovery with the incident owner before retrying; escalate if loading remains slow outside a confirmed window.`
  } else if (issue === 'Cannot Set Up Substitution') {
    resolutionLabel = 'Workflow Capability'
    const workflowName = workflow?.workflow_name || ticket.workflow_id || scenario.workflow_id || 'the named workflow'
    resolutionDetail = `${workflow?.supports_substitution === false ? `${workflowName} is listed as not supporting substitution in the source-system capability reference; this has not been independently verified against the live system. ` : `Confirm substitution support for ${workflowName} with source-system support. `}Refer the user to source-system support for an approved alternative.`
  } else if (issue === 'Cannot See Specific Language') {
    resolutionLabel = requestedLanguage ? `Requested Task Language · ${requestedLanguage}` : 'Supported Task Languages'
    resolutionDetail = alternatives.length
      ? `The source-system reference lists ${alternatives.join(', ')} for task content. Confirm the request concerns task content, then guide the user to a listed alternative or source-system support.`
      : 'The available source-system catalogue does not provide a confirmed alternative. Verify supported task-content languages with source-system support.'
  } else {
    resolutionLabel = 'Next Investigation'
    resolutionDetail = 'Collect the missing evidence identified in the investigation before recommending a resolution.'
  }

  const reference = scenario.required_ticket_reference || ticket.scenario_reference
  const verification = verified
    ? (investigation?.verification_steps?.[0]?.instruction || 'Review the consultant-recorded verification evidence.')
    : issue === 'Task Not Visible' && reference
      ? `After any separately authorized change, confirm the reporter can see task ${reference}.`
      : investigation?.verification_steps?.[0]?.instruction
        || 'Ask the reporter to retry the affected workflow and record the result.'
  const subtitle = verified
    ? 'Consultant verified'
    : issue === 'Task Not Visible'
      ? 'Scenario-based · User assignment not verified'
      : 'Scenario-based · Not independently verified'

  const copyRole = async () => {
    if (!roleId) return
    try {
      await navigator.clipboard.writeText(roleId)
      setCopiedRole(roleId)
      window.setTimeout(() => setCopiedRole(''), 1600)
    } catch {
      setCopiedRole('')
    }
  }

  return <article className="investigation-card">
    <header className="investigation-header">
      <h3><Sparkles size={24} aria-hidden="true"/>AI Investigation</h3>
      <span>{ticket.incident_number} · {sourceSystem}</span>
    </header>
    {aiProvenance && <div className={`ai-provenance ${aiProvenance === 'live_gpt' ? 'live' : ''}`}>
      {aiProvenance === 'live_gpt' ? `Live GPT · ${aiModel || 'configured model'}` : aiProvenance === 'deterministic_fallback' ? 'Deterministic troubleshooting guidance · GPT unavailable' : ''}
    </div>}
    <section className="expected-cause">
      <div className="expected-label">{verified ? 'VERIFIED ROOT CAUSE' : 'EXPECTED ROOT CAUSE'}</div>
      <h4>{verified ? (assessment.statement || issueHeading) : issueHeading}</h4>
      <p>{subtitle}</p>
    </section>
    <section className="recommended-resolution">
      <h4>Recommended Resolution</h4>
      <div className="resolution-id-row">
        <div className="resolution-id">
          <span>{resolutionLabel}</span>
          {roleId && <code>{roleId}</code>}
        </div>
        {roleId && <button className="copy-role-button" onClick={copyRole} aria-label={`Copy ${roleId}`}>
          {copiedRole === roleId ? <Check size={17}/> : <Copy size={17}/>} {copiedRole === roleId ? 'Copied' : 'Copy'}
        </button>}
      </div>
      <p>{resolutionDetail}</p>
    </section>
    <div className="verification-divider"/>
    <section className="resolution-verification">
      <h4><CheckCircle2 size={22} aria-hidden="true"/>Resolution Verification</h4>
      <p>{verification}</p>
    </section>
    {capturedKnowledge.map((article) => <div className="knowledge-provenance" key={article.article_id}>
      Based on approved knowledge: {article.article_id} · {article.source_incident}
    </div>)}
    <button className="contact-customer-button" onClick={onContactCustomer}>
      <Mail size={15}/>{communicationOpen ? 'Hide Customer Communication' : 'Contact Customer'}
    </button>
  </article>
}

function CustomerCommunicationPanel({
  communication,
  loading,
  error,
  draft,
  draftError,
  generating,
  copyState,
  onGenerate,
  onRetryCustomer,
  onEdit,
  onCopy,
}) {
  const customer = communication?.customer
  const contact = communication?.contact_time
  const slot = contact?.suggested_slot
  return <section className="customer-communication" aria-labelledby="customer-communication-title">
    <div className="communication-heading">
      <div className="communication-heading-title"><Mail size={16}/><h3 id="customer-communication-title">CUSTOMER COMMUNICATION</h3></div>
      <span className="ai-assisted-badge"><Sparkles size={12}/> AI Assisted</span>
    </div>
    {loading ? <div className="communication-loading"><LoaderCircle className="spin" size={16}/> Loading customer details…</div>
      : error ? <><div className="communication-error"><AlertCircle size={15}/>{error}</div><button className="communication-retry-button" onClick={onRetryCustomer}>Retry loading customer details</button></>
        : customer ? <>
          <div className="customer-info-card">
            <strong>{customer.full_name}</strong>
            <span>{customer.user_id || customer.customer_id} · {customer.location}</span>
            <a href={"mailto:" + customer.email}>{customer.email}</a>
          </div>
          <div className="contact-time-card">
            <div className="contact-time-title"><Clock3 size={15}/><strong>Suggested Contact Time</strong></div>
            {slot ? <div className="contact-times">
              <div><strong>{slot.consultant.time}</strong><span>{slot.consultant.label} · {slot.consultant.date}</span></div>
              <div><strong>{slot.customer.time}</strong><span>{slot.customer.label} · {slot.customer.date}</span></div>
            </div> : <p className="no-contact-slot">{contact?.reason || 'No future 30-minute slot was found.'}</p>}
            {contact?.overlap_found && <span className="working-overlap-badge"><CheckCircle2 size={12}/> Working-hour overlap</span>}
            {slot && <span className="contact-time-note">Suggested only · subject to availability · not a booking</span>}
          </div>
          <div className="customer-email-card">
            <div className="customer-email-heading"><h4>AI Email Draft</h4>
              {draft?.ai_provenance === 'live_gpt' && <span className="ai-provenance live">Live GPT · {draft.model || 'configured model'}</span>}
              <button className="communication-generate-button" onClick={onGenerate} disabled={generating}>
                {generating ? <LoaderCircle className="spin" size={14}/> : <Sparkles size={14}/>}
                {generating ? 'Generating…' : draft ? 'Regenerate' : draftError ? 'Retry draft' : 'Generate Customer Reply'}
              </button>
            </div>
            {draftError && <div className="communication-error"><AlertCircle size={14}/>{draftError}</div>}
            {draft ? <div className="email-draft-fields">
              <label>Subject<input aria-label="Email subject" value={draft.subject} onChange={(event) => onEdit('subject', event.target.value)}/></label>
              <label>Email body<textarea aria-label="Email body" value={draft.body} onChange={(event) => onEdit('body', event.target.value)} rows={7}/></label>
              <button className="copy-email-button" onClick={onCopy} disabled={!draft.subject.trim() || !draft.body.trim()}>
                {copyState === 'copied' ? <Check size={14}/> : <Copy size={14}/>}
                {copyState === 'copied' ? 'Copied' : 'Copy Email Draft'}
              </button>
            </div> : <p className="email-draft-empty">Generate a draft to review and edit before copying it into your email client.</p>}
          </div>
        </> : null}
  </section>
}

function App() {
  const hosted = import.meta.env.PROD
  const [authStatus, setAuthStatus] = useState(hosted ? 'checking' : 'authenticated')
  const [authError, setAuthError] = useState('')
  const [tickets, setTickets] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadingTickets, setLoadingTickets] = useState(true)
  const [ticketError, setTicketError] = useState('')
  const [answer, setAnswer] = useState(null)
  const [messages, setMessages] = useState([])
  const [pendingStatus, setPendingStatus] = useState(null)
  const [captureOffer, setCaptureOffer] = useState(null)
  const statusSubmitting = useRef(false)
  const aiRequestInFlight = useRef(false)
  const [draftMessage, setDraftMessage] = useState('')
  const [workflowOpen, setWorkflowOpen] = useState(false)
  const [resolutionPrefilled, setResolutionPrefilled] = useState(false)
  const [resolutionConfirmed, setResolutionConfirmed] = useState(false)
  const [workflowStep, setWorkflowStep] = useState(1)
  const [resolutions, setResolutions] = useState([])
  const [articles, setArticles] = useState([])
  const [resolutionForm, setResolutionForm] = useState({ confirmed_root_cause:'', actions_performed:'', verification_evidence:'', consultant_id:'' })
  const [article, setArticle] = useState(null)
  const [reviewer, setReviewer] = useState('')
  const [workflowBusy, setWorkflowBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [statusFeedback, setStatusFeedback] = useState('')
  const [error, setError] = useState('')
  const [communicationByTicket, setCommunicationByTicket] = useState({})
  const [communicationOpenFor, setCommunicationOpenFor] = useState('')
  const communicationRequests = useRef(new Set())
  const emailRequests = useRef(new Set())

  const selected = useMemo(() => tickets.find((ticket) => ticket.incident_number === selectedId), [tickets, selectedId])
  const communicationState = communicationByTicket[selectedId] || {}
  const customerCommunication = communicationState.communication || null
  const communicationLoading = Boolean(communicationState.loadingCustomer)
  const communicationError = communicationState.customerError || ''
  const emailDraft = communicationState.draft || null
  const emailGenerating = Boolean(communicationState.generating)
  const emailDraftError = communicationState.draftError || ''
  const emailCopyState = communicationState.copyState || ''
  const customerCommunicationOpen = isCustomerCommunicationOpen(communicationOpenFor, selectedId, answer)
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return tickets
    return tickets.filter((ticket) => [ticket.incident_number, summary(ticket), source(ticket), ticket.category].join(' ').toLowerCase().includes(needle))
  }, [tickets, query])

  const loadTickets = useCallback(async (preferredId) => {
    setLoadingTickets(true); setTicketError('')
    try {
      const result = await api('/api/tickets')
      const rows = result.tickets || []
      setTickets(rows)
      const chosen = preferredId || selectedId
      setSelectedId(rows.some((row) => row.incident_number === chosen) ? chosen : rows[0]?.incident_number || '')
    } catch (err) { setTicketError(err.message) }
    finally { setLoadingTickets(false) }
  }, [selectedId])

  const loadTicketWorkflow = useCallback(async (incident) => {
    if (!incident) return
    try {
      const [resolutionResult, articleResult] = await Promise.all([
        api(`/api/tickets/${incident}/resolutions`),
        api(`/api/tickets/${incident}/knowledge-articles`),
      ])
      setResolutions(resolutionResult.resolutions || [])
      setArticles(articleResult.articles || [])
      setArticle((articleResult.articles || []).find((item) => item.review_status === 'Draft') || null)
    } catch (err) { setError(err.message) }
  }, [])

  useEffect(() => {
    if (!hosted) return
    let active = true
    requestDirectApi('/api/auth/session').then((result) => {
      if (active) { setAuthStatus(result.authenticated ? 'authenticated' : 'signed_out'); setAuthError(result.error || '') }
    }).catch((err) => {
      if (active) { setAuthStatus('signed_out'); setAuthError(err.message) }
    })
    const requireAuth = () => { setAuthStatus('signed_out'); setAuthError('Your demo session expired. Enter the access code again.') }
    window.addEventListener('ticket-agent-auth-required', requireAuth)
    return () => { active = false; window.removeEventListener('ticket-agent-auth-required', requireAuth) }
  }, [hosted])
  useEffect(() => { if (authStatus === 'authenticated') loadTickets('') }, [authStatus, loadTickets])
  useEffect(() => { setCommunicationOpenFor('') }, [selectedId])
  useEffect(() => {
    setAnswer(null); setMessages([]); setError(''); setNotice(''); setResolutions([]); setArticles([]); setArticle(null)
    setResolutionForm({ confirmed_root_cause:'', actions_performed:'', verification_evidence:'', consultant_id:'' })
    setResolutionPrefilled(false)
    setResolutionConfirmed(false)
    setWorkflowStep(1)
    setPendingStatus((pending) => pending?.explicit_target ? pending : null)
    if (selectedId) loadTicketWorkflow(selectedId)
  }, [selectedId, loadTicketWorkflow])

  const runTroubleshooting = async () => {
    if (!selected || aiRequestInFlight.current) return
    aiRequestInFlight.current = true
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api(`/api/tickets/${selected.incident_number}/investigation`, { method:'POST', body:JSON.stringify({}) })
      setAnswer(result)
      if (result.reply) setMessages([{ role:'assistant', content:result.reply, ai_provenance:result.ai_provenance, model:result.model }])
      if (result.error) setError(result.error)
    } catch (err) { if (err.payload?.investigation) setAnswer({ investigation:err.payload.investigation, error:err.message }); setError(err.message) }
    finally { aiRequestInFlight.current = false; setBusy(false) }
  }

  const sendMessage = async (event) => {
    event?.preventDefault()
    const message = draftMessage.trim()
    if (!message || !selected || busy || aiRequestInFlight.current) return
    aiRequestInFlight.current = true
    const prior = messages.slice(-12)
    setMessages([...prior, { role:'user', content:message }]); setDraftMessage(''); setBusy(true); setError(''); setStatusFeedback('')
    try {
      const result = await api(`/api/tickets/${selected.incident_number}/chat`, {
        method:'POST', body:JSON.stringify({ message, history:prior, request_investigation:!answer }),
      })
      if (result.status_update) {
        setCaptureOffer(null)
        setPendingStatus(result.status_update.state === 'pending_confirmation' ? result.status_update : null)
      }
      if (result.investigation) setAnswer((old) => ({ ...(old || {}), investigation:result.investigation }))
      setMessages((old) => [...old, { role:'assistant', content:result.reply || 'I could not produce a response.', ai_provenance:result.ai_provenance, model:result.model }])
      if (result.error) setError(result.error)
    } catch (err) {
      if (err.payload?.investigation) setAnswer((old) => ({ ...(old || {}), investigation:err.payload.investigation }))
      setMessages((old) => [...old, { role:'assistant', content:'The request could not be completed.' }]); setError(err.message)
    }
    finally { aiRequestInFlight.current = false; setBusy(false) }
  }

  const confirmStatusUpdate = async () => {
    if (!pendingStatus || pendingStatus.state !== 'pending_confirmation' || statusSubmitting.current) return
    statusSubmitting.current = true; setPendingStatus({ ...pendingStatus, state:'submitting' }); setError(''); setNotice('')
    setCaptureOffer(null)
    const target = pendingStatus.incident_number
    try {
      const result = await api(`/api/tickets/${target}`, {
        method:'PATCH',
        body:JSON.stringify({
          status:pendingStatus.requested_status,
          expected_current_status:pendingStatus.current_status,
          confirmed:true,
        }),
      })
      const updatedStatus = result.ticket ? statusLabel(result.ticket) : ''
      if (updatedStatus !== pendingStatus.requested_status) {
        throw new Error('The ticket service did not confirm the requested status. Refresh the ticket before continuing.')
      }
      await loadTickets(target)
      setSelectedId(target)
      setPendingStatus(null); setAnswer(null)
      setMessages((old) => [...old, { role:'assistant', content:`Ticket ${target} status updated to ${updatedStatus}.` }])
      setStatusFeedback(`Ticket ${target} status updated to ${updatedStatus}.`)
      if (updatedStatus === 'Closed' || updatedStatus === 'Resolved') {
        const sameTicket = target === selected?.incident_number
        setCaptureOffer({
          incident_number:target,
          status:updatedStatus,
          state:'offered',
          investigation:sameTicket ? answer?.investigation || null : null,
          messages:sameTicket ? [...messages] : [],
        })
      }
    } catch (err) {
      if (err.status === 409) {
        setPendingStatus(null)
        await loadTickets(target)
        setStatusFeedback('The ticket changed after this proposal. Review its latest status before requesting an update.')
      } else {
        setPendingStatus({ ...pendingStatus, state:'pending_confirmation' })
        setStatusFeedback(err.message)
      }
      setError(err.message)
    } finally { statusSubmitting.current = false }
  }

  const cancelStatusUpdate = () => {
    if (pendingStatus?.state === 'submitting') return
    setPendingStatus(null)
    setMessages((old) => [...old, { role:'assistant', content:'Status update cancelled. The ticket was not changed.' }])
  }

  const beginResolution = () => {
    setError(''); setNotice(''); setResolutionConfirmed(false)
    const verifiedRecord = resolutions.find((item) => item.verified && item.incident_number === selected?.incident_number)
    if (verifiedRecord) {
      setWorkflowStep(2)
      setWorkflowOpen(true)
      return
    }
    const prefill = buildResolutionPrefill(selected, answer?.investigation || {}, messages)
    const hasProposal = Boolean(prefill.proposals.root_cause || prefill.proposals.resolution)
    setResolutionForm(buildEditableResolutionForm(prefill))
    setResolutionPrefilled(hasProposal)
    setWorkflowStep(1)
    setWorkflowOpen(true)
  }
  const beginCaptureFromChat = () => {
    if (!captureOffer || captureOffer.incident_number !== selected?.incident_number) return
    const contextMessages = captureOffer.messages || []
    const contextInvestigation = captureOffer.investigation || {}
    const savedRecord = (resolutions.find((item) => item.verified && item.incident_number === captureOffer.incident_number)
      || contextInvestigation.verified_resolution_record)
    const prefill = buildResolutionPrefill(selected, contextInvestigation, contextMessages, savedRecord)
    const existingVerified = savedRecord?.verified === true
    setResolutionForm(buildEditableResolutionForm(prefill))
    setCaptureOffer(null)
    setResolutionPrefilled(!existingVerified)
    setResolutionConfirmed(false)
    setWorkflowStep(existingVerified ? 2 : 1)
    setError(''); setNotice(''); setWorkflowOpen(true)
  }
  const declineCaptureFromChat = () => {
    setCaptureOffer(null)
    setMessages((old) => [...old, { role:'assistant', content:'No problem. You can capture a verified resolution later from this ticket.' }])
  }
  const saveResolution = async (event) => {
    event.preventDefault()
    if (!selected) return
    const actions = resolutionForm.actions_performed.split('\n').map((line) => line.trim()).filter(Boolean)
    if (!actions.length || !canSaveVerifiedResolution(resolutionForm, resolutionConfirmed)) {
      setError('Complete the cause, actions, actual retest evidence, and consultant ID, then confirm the statement below.')
      return
    }
    setWorkflowBusy(true); setError('')
    try {
      const saved = await api(`/api/tickets/${selected.incident_number}/resolutions`, { method:'POST', body:JSON.stringify({ ...resolutionForm, actions_performed:actions, consultant_confirmed:true }) })
      setResolutions((old) => [...old, saved.resolution]); setWorkflowStep(2); setNotice('Verified resolution saved for this incident. Ticket status was not changed.')
    } catch (err) { setError(err.message) }
    finally { setWorkflowBusy(false) }
  }
  const generateArticle = async () => {
    const resolution = resolutions.find((item) => item.verified)
    if (!resolution || !selected) return
    setWorkflowBusy(true); setError(''); setNotice('')
    try {
      const result = await api(`/api/tickets/${selected.incident_number}/knowledge-articles/generate`, { method:'POST', body:JSON.stringify({ resolution_id:resolution.resolution_id }) })
      setArticle({ ...result.article, ai_provenance:result.ai_provenance }); setArticles((old) => old.some((item) => item.article_id === result.article.article_id) ? old : [...old, result.article])
      setNotice('AI draft created from the consultant-verified resolution. Review it before approval.')
    } catch (err) { setError(err.message) }
    finally { setWorkflowBusy(false) }
  }
  const saveArticle = async (approve = false) => {
    if (!article) return
    setWorkflowBusy(true); setError(''); setNotice('')
    try {
      const payload = { title:article.title, symptoms:article.symptoms }
      if (approve) { payload.approve = true; payload.approved_by = reviewer }
      const result = await api(`/api/knowledge-articles/${article.article_id}`, { method:'PATCH', body:JSON.stringify(payload) })
      setArticle(result.article); setArticles((old) => old.map((item) => item.article_id === article.article_id ? result.article : item))
      setNotice(approve ? 'Article approved and published to the reusable knowledge store.' : 'Draft edits saved.')
    } catch (err) { setError(err.message) }
    finally { setWorkflowBusy(false) }
  }
  const searchApprovedKnowledge = async () => {
    if (!selected) return
    try {
      const result = await api(`/api/knowledge-articles/search?source_system=${encodeURIComponent(source(selected))}&issue_category=${encodeURIComponent(selected.category || '')}&q=${encodeURIComponent(summary(selected))}`)
      setNotice(result.articles?.length ? `Found ${result.articles.length} approved captured article(s) for this incident context.` : 'No approved captured articles match this incident yet.')
    } catch (err) { setError(err.message) }
  }
  const loadCustomerCommunication = async (incident, force = false) => {
    const current = communicationByTicket[incident] || {}
    if (current.communication && !force) return current.communication
    if (communicationRequests.current.has(incident)) return null
    communicationRequests.current.add(incident)
    setCommunicationByTicket((old) => updateTicketCommunicationState(old, incident, { loadingCustomer:true, customerError:'' }))
    try {
      const result = await api('/api/tickets/' + incident + '/customer-communication')
      setCommunicationByTicket((old) => updateTicketCommunicationState(old, incident, { communication:result, customerError:'' }))
      return result
    } catch (err) {
      setCommunicationByTicket((old) => updateTicketCommunicationState(old, incident, { customerError:err.message }))
      return null
    } finally {
      communicationRequests.current.delete(incident)
      setCommunicationByTicket((old) => updateTicketCommunicationState(old, incident, { loadingCustomer:false }))
    }
  }
  const generateCustomerReply = async (incident = selected?.incident_number, force = false, suppliedCommunication = null) => {
    if (!incident || !canContactCustomer(incident, answer)) return
    const current = communicationByTicket[incident] || {}
    if (!force && current.draft) return
    if (emailRequests.current.has(incident)) return
    emailRequests.current.add(incident)
    setCommunicationByTicket((old) => updateTicketCommunicationState(old, incident, { generating:true, attempted:true, draftError:'', copyState:'' }))
    try {
      const communication = current.communication || suppliedCommunication || await loadCustomerCommunication(incident)
      if (!communication) return
      const result = await api('/api/tickets/' + incident + '/customer-email-draft', {
        method: 'POST',
        body: JSON.stringify({ investigation:answer.investigation, date:communication.contact_time?.selected_date }),
      })
      setCommunicationByTicket((old) => updateTicketCommunicationState(old, incident, {
        draft:{ subject:result.subject, body:result.body, ai_provenance:result.ai_provenance, model:result.generated_by },
        communication: old[incident]?.communication ? { ...old[incident].communication, contact_time:result.contact_time } : communication,
        draftError:'',
      }))
    } catch (err) {
      setCommunicationByTicket((old) => updateTicketCommunicationState(old, incident, { draftError:err.message }))
    } finally {
      emailRequests.current.delete(incident)
      setCommunicationByTicket((old) => updateTicketCommunicationState(old, incident, { generating:false }))
    }
  }
  const contactCustomer = async () => {
    if (!selected || !canContactCustomer(selected.incident_number, answer)) return
    const incident = selected.incident_number
    if (communicationOpenFor === incident) { setCommunicationOpenFor(''); return }
    setCommunicationOpenFor(incident)
    const communication = communicationByTicket[incident]?.communication || await loadCustomerCommunication(incident)
    if (!communication || communicationByTicket[incident]?.draft || communicationByTicket[incident]?.attempted || emailRequests.current.has(incident)) return
    await generateCustomerReply(incident, false, communication)
  }
  const editEmailDraft = (field, value) => {
    setCommunicationByTicket((old) => updateTicketCommunicationState(old, selectedId, {
      draft:updateEmailDraft(old[selectedId]?.draft, field, value), copyState:'',
    }))
  }
  const copyEmailDraft = async () => {
    const text = formatEmailDraftForCopy(emailDraft)
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCommunicationByTicket((old) => updateTicketCommunicationState(old, selectedId, { copyState:'copied' }))
      window.setTimeout(() => setCommunicationByTicket((old) => updateTicketCommunicationState(old, selectedId, { copyState:'' })), 1800)
    } catch {
      setCommunicationByTicket((old) => updateTicketCommunicationState(old, selectedId, { draftError:'Clipboard access was blocked. Select and copy the email text manually.' }))
    }
  }

  const investigation = answer?.investigation
  const resolutionSaveReady = canSaveVerifiedResolution(resolutionForm, resolutionConfirmed)
  const resetDemo = async () => {
    if (!window.confirm('Reset this browser’s demo data to its initial state? This resets ticket statuses, saved resolutions, and captured knowledge for this browser.')) return
    setBusy(true); setError(''); setNotice('')
    try {
      await api('/api/tickets', { method:'GET', demoReset:true })
      setAnswer(null); setMessages([]); setPendingStatus(null); setCaptureOffer(null)
      setResolutions([]); setArticles([]); setArticle(null); setWorkflowOpen(false)
      setResolutionForm({ confirmed_root_cause:'', actions_performed:'', verification_evidence:'', consultant_id:'' })
      setResolutionPrefilled(false); setResolutionConfirmed(false); setWorkflowStep(1)
      setCommunicationByTicket({}); setCommunicationOpenFor('')
      communicationRequests.current.clear(); emailRequests.current.clear()
      await loadTickets(selectedId)
      if (selectedId) await loadTicketWorkflow(selectedId)
      setStatusFeedback('Demo data reset to its initial state for this browser.')
    } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }
  const login = async (accessCode) => {
    const result = await requestDirectApi('/api/auth/login', {
      method:'POST', body:JSON.stringify({ access_code:accessCode }),
    })
    setAuthError(''); setAuthStatus(result.authenticated ? 'authenticated' : 'signed_out')
  }
  const logout = async () => {
    try { await requestDirectApi('/api/auth/logout', { method:'POST', body:'{}' }) }
    finally {
      setAuthStatus('signed_out'); setSelectedId(''); setTickets([]); setAnswer(null); setMessages([])
      setPendingStatus(null); setCaptureOffer(null); setCommunicationByTicket({})
    }
  }
  if (hosted && authStatus === 'checking') return <main className="access-screen"><section className="access-card"><LoaderCircle className="spin"/><p>Checking demo session…</p></section></main>
  if (hosted && authStatus !== 'authenticated') return <><DemoAccessGate onLogin={login}/>{authError && <div className="access-config-error" role="status">{authError}</div>}</>
  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-icon"><Sparkles size={19}/></span><div><div className="brand-name">Ticket Agent</div><div className="brand-sub">L2 SUPPORT WORKSPACE</div></div></div>
      <div className="topbar-right"><span className="live-dot"/> Demonstration environment <span className="top-divider"/> <UserRound size={17}/> Consultant workspace {import.meta.env.PROD && <><span className="top-divider"/><button className="text-button reset-demo-button" onClick={resetDemo} disabled={busy} title="Restore this browser's initial demo data"><RotateCcw size={15}/> Reset Demo</button><button className="text-button reset-demo-button" onClick={logout} title="End this demo session">Log out</button></>}</div>
    </header>
    <main className="workspace">
      <aside className="left-column">
        <div className="section-heading"><div><span className="eyebrow">WORKSPACE</span><h1>Incident queue</h1></div><button className="icon-button" onClick={() => loadTickets(selectedId)} title="Refresh tickets"><ArrowDownToLine size={17}/></button></div>
        <label className="search"><Search size={17}/><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search incidents"/><kbd>/</kbd></label>
        {ticketError && <div className="inline-error"><AlertCircle size={16}/>{ticketError}</div>}
        <div className="ticket-list">
          {loadingTickets ? <div className="empty-state"><LoaderCircle className="spin"/> Loading incidents…</div> : filtered.map((ticket) => <button key={ticket.incident_number} className={`ticket-row ${selectedId === ticket.incident_number ? 'ticket-selected' : ''}`} onClick={() => setSelectedId(ticket.incident_number)}>
            <span className="ticket-row-top"><span className="incident-number">{ticket.incident_number}</span><ChevronRight size={15}/></span>
            <span className="ticket-row-title">{summary(ticket)}</span>
            <span className="ticket-row-meta"><Badge tone={statusLabel(ticket) === 'On Hold' ? 'blue' : statusLabel(ticket) === 'Resolved' || statusLabel(ticket) === 'Closed' ? 'green' : 'slate'}>{statusLabel(ticket)}</Badge><span>{ticket.priority}</span><span className="meta-dot">·</span><span>{source(ticket)}</span></span>
          </button>)}
          {!loadingTickets && filtered.length === 0 && <div className="empty-state">No incidents match this search.</div>}
        </div>
        <div className="queue-footer"><span><Activity size={15}/>{tickets.length} incidents</span><span>Local incident records</span></div>
      </aside>

      <section className="ticket-column">
        {!selected ? <div className="center-empty"><TicketCheck size={32}/><h2>Select an incident</h2><p>Choose a ticket to see its details and begin troubleshooting.</p></div> : <>
          <div className="ticket-heading"><div><div className="eyebrow">INCIDENT RECORD <span className="record-separator">/</span> {selected.incident_number}</div><h2>{summary(selected)}</h2><p>{selected.category || selected.issue || 'Support incident'}{selected.subcategory ? ` · ${selected.subcategory}` : ''}</p></div><Badge tone={statusLabel(selected) === 'On Hold' ? 'blue' : 'slate'}>{statusLabel(selected)}</Badge></div>
          <div className="detail-card">
            <div className="detail-top"><div className="detail-icon"><TicketCheck size={18}/></div><div><strong>Incident details</strong><span>Customer-reported context</span></div><span className="priority">{selected.priority || '—'} priority</span></div>
            <div className="detail-fields"><Field label="Source system" value={source(selected)}/><Field label="Priority" value={selected.priority}/><Field label="Impact" value={titleCase(selected.impact)}/><Field label="Urgency" value={titleCase(selected.urgency)}/></div>
            <div className="description-block"><div className="field-label">Description</div><p>{selected.description || selected.short_description || selected.summary || 'No description provided.'}</p></div>
            {errorText(selected) && <div className="reported-error"><div className="error-label"><AlertCircle size={14}/> CUSTOMER ERROR MESSAGE</div><code>{errorText(selected)}</code></div>}
            {selected.notes && <details className="notes-details"><summary>Notes</summary><p>{selected.notes}</p></details>}
          </div>
          <div className="ticket-actions"><button className="secondary-button" onClick={searchApprovedKnowledge}><BookOpen size={16}/> Find approved knowledge</button><span><Clock3 size={15}/> AI recommendations never change ticket status</span></div>
          <div className="history-teaser"><ShieldCheck size={16}/><span>Related history and captured knowledge are used as supporting evidence only.</span></div>
        </>}
      </section>

      <section className="agent-column">
        <div className="agent-header"><div className="agent-avatar"><Bot size={21}/></div><div className="agent-title"><h2>AI Agent</h2><span>Evidence-led L2 copilot</span></div><span className="agent-ready"><span/> READY</span></div>
        {statusFeedback && <div className="status-feedback"><CheckCircle2 size={15}/>{statusFeedback}<button onClick={()=>setStatusFeedback('')} aria-label="Dismiss status update message"><X size={14}/></button></div>}
        {selected ? <>
          <div className="agent-intro"><div className="eyebrow">TICKET ASSISTANCE</div><h2>Let’s work through this incident.</h2><p>I’ll use the selected ticket, relevant knowledge, history, and source-system role catalogue to suggest next steps.</p></div>
          {!answer && <button className="troubleshoot-button" disabled={busy} onClick={runTroubleshooting}>{busy ? <LoaderCircle className="spin" size={18}/> : <Sparkles size={18}/>} {busy ? 'Investigating…' : 'Troubleshoot this ticket'} <ArrowRight size={17}/></button>}
          {error && <div className="inline-error agent-error"><AlertCircle size={16}/><span>{error}</span></div>}
          {canContactCustomer(selected.incident_number, answer) && <>
            <InvestigationResult ticket={selected} investigation={investigation} aiProvenance={answer.ai_provenance} aiModel={answer.model} onContactCustomer={contactCustomer} communicationOpen={customerCommunicationOpen}/>
            {customerCommunicationOpen && <CustomerCommunicationPanel
              communication={customerCommunication}
              loading={communicationLoading}
              error={communicationError}
              draft={emailDraft}
              draftError={emailDraftError}
              generating={emailGenerating}
              copyState={emailCopyState}
              onGenerate={() => generateCustomerReply(selected.incident_number, true)}
              onRetryCustomer={async () => {
                const communication = await loadCustomerCommunication(selected.incident_number, true)
                if (communication) await generateCustomerReply(selected.incident_number, false, communication)
              }}
              onEdit={editEmailDraft}
              onCopy={copyEmailDraft}
            />}
          </>}
          <div className="conversation">
            {!messages.length && <div className="conversation-hint"><MessageCircle size={16}/><span>Ask a follow-up about the ticket, evidence, or suggested steps.</span></div>}
            {messages.slice(answer ? 1 : 0).map((message,index) => <div key={index} className={`message message-${message.role}`}><div className="message-icon">{message.role === 'assistant' ? <Bot size={15}/> : <UserRound size={15}/>}</div><div>{message.content}{message.ai_provenance && <div className="ai-provenance">{message.ai_provenance === 'live_gpt' ? `Live GPT · ${message.model || 'configured model'}` : message.ai_provenance === 'deterministic_fallback' ? 'Deterministic troubleshooting guidance · GPT unavailable' : 'AI response unavailable'}</div>}</div></div>)}
            {captureOffer?.state === 'offered' && captureOffer.incident_number === selected.incident_number && <div className="knowledge-capture-offer">
              <p>Ticket {captureOffer.incident_number} has been updated to {captureOffer.status}. Would you like to capture this resolution as reusable knowledge?</p>
              <div><button className="secondary-button" onClick={declineCaptureFromChat}>Not Now</button><button className="primary-button" onClick={beginCaptureFromChat}>Yes, Capture</button></div>
            </div>}
            {['pending_confirmation','submitting'].includes(pendingStatus?.state) && <div className="status-confirm-card">
              <div className="status-card-heading"><div><Activity size={16}/></div><strong>Update Ticket Status</strong><Badge tone="amber">Confirmation required</Badge></div>
              <div className="status-card-fields"><Field label="Ticket" value={pendingStatus.incident_number}/><Field label="Current status" value={pendingStatus.current_status}/><Field label="New status" value={pendingStatus.requested_status}/></div>
              {pendingStatus.explicit_target && pendingStatus.incident_number !== selected.incident_number && <div className="target-note">This incident differs from the selected ticket. Confirm the incident number above.</div>}
              <div className="status-card-actions"><button className="secondary-button" onClick={cancelStatusUpdate} disabled={pendingStatus.state === 'submitting'}>Cancel</button><button className="primary-button" onClick={confirmStatusUpdate} disabled={pendingStatus.state === 'submitting'}>{pendingStatus.state === 'submitting' ? <LoaderCircle className="spin" size={15}/> : <Check size={15}/>} {pendingStatus.state === 'submitting' ? 'Updating…' : 'Confirm Update'}</button></div>
            </div>}
          </div>
          <form className="chat-form" onSubmit={sendMessage}><input value={draftMessage} onChange={(e)=>setDraftMessage(e.target.value)} placeholder="Ask a ticket-aware follow-up…" disabled={busy || ['pending_confirmation','submitting'].includes(pendingStatus?.state)}/><button aria-label="Send message" disabled={busy || ['pending_confirmation','submitting'].includes(pendingStatus?.state) || !draftMessage.trim()}>{busy ? <LoaderCircle className="spin" size={18}/> : <Send size={18}/>}</button></form>
          <div className="agent-bottom"><button className="text-button" onClick={beginResolution}><CheckCircle2 size={15}/> Confirm actual resolution</button><span>Requires consultant verification</span></div>
        </> : <div className="center-empty agent-empty"><Sparkles size={32}/><h2>Ready when you are</h2><p>Select an incident to start an evidence-led investigation.</p></div>}
      </section>
    </main>
    {workflowOpen && selected && <div className="modal-backdrop" onMouseDown={(e)=>{if(e.target===e.currentTarget)setWorkflowOpen(false)}}><section className="workflow-modal" role="dialog" aria-modal="true" aria-labelledby="workflow-title">
      <div className="modal-head"><div><span className="eyebrow">RESOLUTION & KNOWLEDGE</span><h2 id="workflow-title">{workflowStep === 1 ? 'Confirm Actual Resolution' : 'Knowledge Article'} — {selected.incident_number}</h2></div><button className="icon-button" onClick={()=>setWorkflowOpen(false)} aria-label="Close"><X size={19}/></button></div>
      <div className="workflow-content">
        {workflowStep === 1 ? <section className="workflow-section">
          {resolutionPrefilled && <div className="resolution-draft-note">AI-prepared draft. Review and confirm the actual resolution before saving.</div>}
          <form onSubmit={saveResolution} className="resolution-form">
            <label>Root Cause<textarea value={resolutionForm.confirmed_root_cause} onChange={(e)=>{setResolutionForm({...resolutionForm,confirmed_root_cause:e.target.value});setResolutionConfirmed(false)}} rows="2" placeholder="Enter the cause confirmed through investigation" required/></label>
            <label>Resolution — actions actually performed<textarea value={resolutionForm.actions_performed} onChange={(e)=>{setResolutionForm({...resolutionForm,actions_performed:e.target.value});setResolutionConfirmed(false)}} rows="3" placeholder="Edit the proposal to describe actions actually performed" required/></label>
            <label>Verification Evidence<textarea value={resolutionForm.verification_evidence} onChange={(e)=>{setResolutionForm({...resolutionForm,verification_evidence:e.target.value});setResolutionConfirmed(false)}} rows="2" placeholder="Enter actual retest evidence" required/></label>
            <label>Consultant Identifier<input value={resolutionForm.consultant_id} onChange={(e)=>{setResolutionForm({...resolutionForm,consultant_id:e.target.value});setResolutionConfirmed(false)}} placeholder="Your consultant ID" required/></label>
            <label className="resolution-confirm"><input type="checkbox" checked={resolutionConfirmed} onChange={(e)=>setResolutionConfirmed(e.target.checked)}/> <span>I confirm that the root cause, actions performed, and verification evidence accurately reflect the actual resolution.</span></label>
            <button className="primary-button" disabled={workflowBusy || !resolutionSaveReady}><Check size={16}/>{workflowBusy ? 'Saving…' : 'Confirm and Save Verified Resolution'}</button>
          </form>
        </section> : <section className="workflow-section">
          <div className="workflow-section-title"><span className="step-marker">2</span><div><h3>Knowledge Article</h3><p>Generate a draft from the saved, consultant-verified resolution.</p></div></div>
          {!resolutions.some((item)=>item.verified) ? <div className="locked-note"><ShieldCheck size={16}/> Save a consultant-verified resolution before drafting an article.</div> : <>
            {!article && <button className="secondary-button wide-button" onClick={generateArticle} disabled={workflowBusy}><Sparkles size={16}/>{workflowBusy ? 'Generating draft…' : 'Generate AI knowledge draft'}</button>}
            {article && <div className="article-editor"><div className="article-status"><FileCheck2 size={16}/><span>{article.review_status === 'Approved' ? 'Approved & published' : 'Draft · review required'}</span>{article.ai_provenance === 'live_gpt' && <span className="ai-provenance live">Generated by Live GPT</span>}</div>
              <label>Article title<input value={article.title} disabled={article.review_status !== 'Draft'} onChange={(e)=>setArticle({...article,title:e.target.value})}/></label>
              <label>Symptoms<textarea rows="3" value={article.symptoms} disabled={article.review_status !== 'Draft'} onChange={(e)=>setArticle({...article,symptoms:e.target.value})}/></label>
              <div className="article-facts"><div><span>Confirmed cause</span><p>{article.confirmed_root_cause}</p></div><div><span>Verified actions</span><p>{(article.verified_troubleshooting_steps || []).join(' · ')}</p></div><div><span>Verification</span><p>{article.verification_method}</p></div></div>
              {article.review_status === 'Draft' ? <><button className="secondary-button wide-button" onClick={()=>saveArticle(false)} disabled={workflowBusy}>Save draft edits</button><label>Reviewer identifier<input value={reviewer} onChange={(e)=>setReviewer(e.target.value)} placeholder="Reviewer ID"/></label><button className="primary-button wide-button" onClick={()=>saveArticle(true)} disabled={workflowBusy || !reviewer.trim()}><CheckCircle2 size={16}/>{workflowBusy ? 'Publishing…' : 'Approve and publish article'}</button></> : <div className="published-note"><CheckCircle2 size={16}/> Approved articles are available to future matching investigations.</div>}
            </div>}
          </>}
          {!!articles.length && <div className="article-history"><BookOpen size={15}/>{articles.length} article record{articles.length===1?'':'s'} linked to this incident</div>}
        </section>}
      </div>
      {notice && <div className="success-toast"><CheckCircle2 size={16}/>{notice}</div>}{error && <div className="inline-error modal-error"><AlertCircle size={16}/>{error}</div>}
    </section></div>}
  </div>
}

export default App
