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

export function formatContactSlot(localSlot) {
  if (!localSlot?.date || !localSlot?.time) return null
  // Backend supplies local dates/times. Format the date in UTC solely to avoid
  // shifting that calendar date according to the browser/server timezone.
  const date = new Intl.DateTimeFormat('en-GB', {
    weekday:'long', day:'2-digit', month:'short', year:'numeric', timeZone:'UTC',
  }).format(new Date(`${localSlot.date}T00:00:00Z`))
  const zoneLabels = { 'Asia/Ho_Chi_Minh':'Vietnam Time', 'Europe/Berlin':'Munich Time' }
  const label = zoneLabels[localSlot.timezone] || localSlot.label || localSlot.timezone
  const endDate = localSlot.end_date && localSlot.end_date !== localSlot.date ? ` (${localSlot.end_date})` : ''
  return {
    date,
    timeRange:`${localSlot.time}${localSlot.end_time ? `–${localSlot.end_time}${endDate}` : ''}`,
    zone:`${label}${localSlot.utc_offset ? `, ${localSlot.utc_offset}` : ''}`,
  }
}
