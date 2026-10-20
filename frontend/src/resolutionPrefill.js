function explicitChatFact(messages, pattern) {
  for (const message of messages || []) {
    if (message.role !== 'user' || typeof message.content !== 'string') continue
    for (const line of message.content.split(/\r?\n/)) {
      const match = line.match(pattern)
      if (match?.[1]?.trim()) return match[1].trim()
    }
  }
  return ''
}

export function buildResolutionPrefill(ticket, investigation = {}, messages = [], savedRecord = null) {
  if (investigation.warnings?.length) return {
    proposals: { root_cause:'', resolution:'' },
    confirmed: { confirmed_root_cause:'', actions_performed:'', verification_evidence:'', consultant_id:'' },
  }
  const verifiedRecord = savedRecord?.verified === true
    ? savedRecord
    : investigation.verified_resolution_record?.verified === true
      ? investigation.verified_resolution_record
      : null
  const evidence = investigation.evidence?.unified_inbox || {}
  const scenario = evidence.scenario || investigation.scenario_evidence || {}
  const assessment = investigation.root_cause_assessment || {}
  const roleType = scenario.expected_missing_role_type || ticket.expected_missing_role_type || ''
  const roleId = roleType === 'Gateway'
    ? scenario.gateway_role_id || ticket.gateway_role_id || ''
    : roleType === 'Backend'
      ? scenario.backend_role_id || ticket.backend_role_id || ''
      : ''
  const sourceSystem = ticket.source_system || ticket.system || ''
  const reference = scenario.required_ticket_reference || ticket.scenario_reference || ''
  const expectedCause = roleType && roleId
    ? `Missing ${sourceSystem} ${roleType} Role — ${roleId}`
    : (scenario.root_cause?.statement || assessment.statement || '')
  const steps = [
    investigation.recommended_troubleshooting_steps,
    investigation.diagnostic_steps,
    scenario.recommended_actions,
  ].find((items) => Array.isArray(items) && items.length) || []
  const proposedResolution = roleType && roleId
    ? `Verify the user's ${sourceSystem} ${roleType} Role. If missing, request ${roleId} through authorized access management, then retest${reference ? ` ${reference}` : ' the affected task'}.`
    : steps.filter((step) => typeof step?.instruction === 'string' && step.instruction.trim())
      .slice(0, 3).map((step) => step.instruction.trim()).join('\n')

  const verifiedCause = assessment.state === 'consultant_verified' && assessment.verified === true
    ? assessment.statement : ''
  const rootCause = verifiedRecord?.confirmed_root_cause || verifiedCause || explicitChatFact(
    messages, /^\s*(?:confirmed root cause|root cause confirmed)\s*:\s*(.+)$/i,
  )
  const actions = verifiedRecord?.actions_performed
    ? verifiedRecord.actions_performed.join('\n')
    : explicitChatFact(messages, /^\s*(?:actions actually performed|completed actions)\s*:\s*(.+)$/i)
  const verification = verifiedRecord?.verification_evidence || explicitChatFact(
    messages, /^\s*(?:verification evidence|retest result)\s*:\s*(.+)$/i,
  )

  return {
    proposals: { root_cause:expectedCause, resolution:proposedResolution },
    confirmed: {
      confirmed_root_cause:rootCause,
      actions_performed:actions,
      verification_evidence:verification,
      // The prototype has no authenticated identity. Do not infer one from chat or AI text.
      consultant_id:verifiedRecord?.consultant_id || '',
    },
  }
}

export function buildEditableResolutionForm(prefill) {
  return {
    ...prefill.confirmed,
    confirmed_root_cause:prefill.confirmed.confirmed_root_cause || prefill.proposals.root_cause,
    actions_performed:prefill.confirmed.actions_performed || prefill.proposals.resolution,
  }
}

export function canSaveVerifiedResolution(form, consultantConfirmed) {
  return Boolean(
    form.confirmed_root_cause.trim()
    && form.actions_performed.trim()
    && form.verification_evidence.trim()
    && form.consultant_id.trim()
    && consultantConfirmed
  )
}
