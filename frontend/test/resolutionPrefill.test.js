import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import {
  buildEditableResolutionForm,
  buildResolutionPrefill,
  canSaveVerifiedResolution,
} from '../src/resolutionPrefill.js'

const root = new URL('../../data/', import.meta.url)
const ticketsData = JSON.parse(await readFile(fileURLToPath(new URL('tickets.json', root)), 'utf8'))
const scenariosData = JSON.parse(await readFile(fileURLToPath(new URL('troubleshooting_scenarios.json', root)), 'utf8'))
const ticketById = new Map(ticketsData.tickets.map((ticket) => [ticket.incident_number, ticket]))
const scenarioById = new Map(scenariosData.synthetic_scenarios.map((scenario) => [scenario.scenario_id, scenario]))

function investigationFor(ticket) {
  const scenario = scenarioById.get(ticket.scenario_id)
  return {
    evidence: { unified_inbox: { scenario } },
    scenario_evidence: scenario,
    root_cause_assessment: {
      state: 'scenario_expected_unverified',
      statement: scenario.root_cause.statement,
      verified: false,
    },
    recommended_troubleshooting_steps: scenario.recommended_actions,
  }
}

for (const [incident, roleType, roleId, reference] of [
  ['INC100001', 'Gateway', 'XX:GATEWAY_PM7:XX', 'PR-1001'],
  ['INC100003', 'Gateway', 'XX:GATEWAY_FICO:XX', 'EXP-3002'],
  ['INC100004', 'Backend', 'XX:BACKEND_BTRAVEL:XX', 'TR-4008'],
]) {
  test(`${incident} offers editable scenario guidance without treating it as completed evidence`, () => {
    const ticket = ticketById.get(incident)
    const prefill = buildResolutionPrefill(ticket, investigationFor(ticket))
    const form = buildEditableResolutionForm(prefill)

    assert.match(prefill.proposals.root_cause, new RegExp(`Missing .* ${roleType} Role — ${roleId}`))
    assert.match(prefill.proposals.resolution, new RegExp(`${roleId.replaceAll(':', '\\:')}.*${reference}`))
    assert.equal(form.confirmed_root_cause, prefill.proposals.root_cause)
    assert.equal(form.actions_performed, prefill.proposals.resolution)
    assert.equal(form.verification_evidence, '')
    assert.equal(form.consultant_id, '')
    assert.equal(canSaveVerifiedResolution(form, true), false)
  })
}

test('only explicit consultant statements or an existing verified record fill confirmed fields', () => {
  const ticket = ticketById.get('INC100001')
  const investigation = investigationFor(ticket)
  const chatPrefill = buildResolutionPrefill(ticket, investigation, [
    { role: 'assistant', content: 'AI says a role may be missing.' },
    { role: 'user', content: 'Actions actually performed: Compared assignments with the catalogue.' },
    { role: 'user', content: 'Verification evidence: Reporter confirmed PR-1001 is visible.' },
  ])

  assert.equal(chatPrefill.confirmed.confirmed_root_cause, '')
  assert.equal(chatPrefill.confirmed.actions_performed, 'Compared assignments with the catalogue.')
  assert.equal(chatPrefill.confirmed.verification_evidence, 'Reporter confirmed PR-1001 is visible.')
  assert.equal(chatPrefill.confirmed.consultant_id, '')

  const verifiedPrefill = buildResolutionPrefill(ticket, investigation, [], {
    verified: true,
    confirmed_root_cause: 'Consultant-confirmed cause',
    actions_performed: ['Consultant-confirmed action'],
    verification_evidence: 'Consultant-confirmed retest',
    consultant_id: 'CONS-01',
  })
  assert.equal(verifiedPrefill.confirmed.confirmed_root_cause, 'Consultant-confirmed cause')
  assert.equal(verifiedPrefill.confirmed.actions_performed, 'Consultant-confirmed action')
  assert.equal(verifiedPrefill.confirmed.consultant_id, 'CONS-01')
})

test('unverified saved data is not copied into the confirmation form', () => {
  const ticket = ticketById.get('INC100004')
  const prefill = buildResolutionPrefill(ticket, investigationFor(ticket), [], {
    verified: false,
    confirmed_root_cause: 'Unverified claim',
    actions_performed: ['Unverified action'],
    verification_evidence: 'Unverified retest',
    consultant_id: 'UNVERIFIED',
  })
  assert.deepEqual(prefill.confirmed, {
    confirmed_root_cause: '', actions_performed: '', verification_evidence: '', consultant_id: '',
  })
})

test('saving requires actual verification evidence and explicit consultant affirmation', () => {
  const complete = {
    confirmed_root_cause: 'Consultant-confirmed cause',
    actions_performed: 'Actions actually performed',
    verification_evidence: 'Reporter retested successfully',
    consultant_id: 'CONS-02',
  }
  assert.equal(canSaveVerifiedResolution({ ...complete, verification_evidence: '' }, true), false)
  assert.equal(canSaveVerifiedResolution(complete, false), false)
  assert.equal(canSaveVerifiedResolution(complete, true), true)
})
