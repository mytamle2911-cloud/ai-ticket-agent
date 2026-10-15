export function updateEmailDraft(current, field, value) {
  if (!current || !['subject', 'body'].includes(field)) return current
  return { ...current, [field]: value }
}

export function formatEmailDraftForCopy(draft) {
  if (!draft || typeof draft.subject !== 'string' || typeof draft.body !== 'string') return ''
  return `Subject: ${draft.subject}\n\n${draft.body}`
}

export function updateTicketCommunicationState(states, incidentId, updates) {
  if (!incidentId || !updates || typeof updates !== 'object') return states
  return { ...states, [incidentId]: { ...(states[incidentId] || {}), ...updates } }
}

export function canContactCustomer(selectedId, answer) {
  const investigation = answer?.investigation
  return Boolean(selectedId && investigation && !investigation.error && investigation.ticket?.incident_number === selectedId)
}

export function isCustomerCommunicationOpen(openIncidentId, selectedId, answer) {
  return openIncidentId === selectedId && canContactCustomer(selectedId, answer)
}
