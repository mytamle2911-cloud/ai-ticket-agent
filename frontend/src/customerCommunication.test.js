import test from 'node:test'
import assert from 'node:assert/strict'
import { canContactCustomer, formatEmailDraftForCopy, isCustomerCommunicationOpen, updateEmailDraft, updateTicketCommunicationState } from './customerCommunication.js'

test('email draft fields remain editable independently', () => {
  const original = { subject: 'Original subject', body: 'Original body' }
  const edited = updateEmailDraft(original, 'subject', 'Edited subject')
  assert.deepEqual(edited, { subject: 'Edited subject', body: 'Original body' })
  assert.deepEqual(updateEmailDraft(edited, 'body', 'Edited body'), {
    subject: 'Edited subject',
    body: 'Edited body',
  })
})

test('copy action formats the current edited subject and body', () => {
  assert.equal(
    formatEmailDraftForCopy({ subject: 'Reviewed subject', body: 'Reviewed body' }),
    'Subject: Reviewed subject\n\nReviewed body',
  )
  assert.equal(formatEmailDraftForCopy(null), '')
})

test('communication is only available after a completed matching investigation', () => {
  const answer = { investigation:{ ticket:{ incident_number:'INC100006' } } }
  assert.equal(canContactCustomer('INC100006', answer), true)
  assert.equal(canContactCustomer('INC100001', answer), false)
  assert.equal(canContactCustomer('INC100006', null), false)
  assert.equal(canContactCustomer('INC100006', { investigation:{ error:'failed', ticket:{ incident_number:'INC100006' } } }), false)
  assert.equal(isCustomerCommunicationOpen('INC100006', 'INC100001', answer), false)
  assert.equal(isCustomerCommunicationOpen('INC100006', 'INC100006', answer), true)
})

test('per-ticket drafts and edits survive ticket switches without crossing records', () => {
  let states = updateTicketCommunicationState({}, 'INC100006', { draft:{subject:'Old', body:'Body'} })
  states = updateTicketCommunicationState(states, 'INC100001', { draft:{subject:'Other', body:'Other body'} })
  states = updateTicketCommunicationState(states, 'INC100006', { draft:updateEmailDraft(states.INC100006.draft, 'subject', 'Edited') })
  assert.equal(states.INC100006.draft.subject, 'Edited')
  assert.equal(states.INC100001.draft.subject, 'Other')
})
