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

test('contact display includes both dates, full thirty-minute ranges and seasonal UTC offsets', async () => {
  const { formatContactSlot } = await import('./customerCommunication.js')
  const vietnam = formatContactSlot({ date:'2026-07-20', time:'14:00', end_time:'14:30', end_date:'2026-07-20', timezone:'Asia/Ho_Chi_Minh', utc_offset:'UTC+07:00' })
  const munich = formatContactSlot({ date:'2026-07-20', time:'09:00', end_time:'09:30', end_date:'2026-07-20', timezone:'Europe/Berlin', utc_offset:'UTC+02:00' })
  assert.equal(vietnam.date, 'Monday, 20 Jul 2026')
  assert.equal(vietnam.timeRange, '14:00–14:30')
  assert.equal(vietnam.zone, 'Vietnam Time, UTC+07:00')
  assert.equal(munich.date, 'Monday, 20 Jul 2026')
  assert.equal(munich.timeRange, '09:00–09:30')
  assert.equal(munich.zone, 'Munich Time, UTC+02:00')
  const winter = formatContactSlot({ date:'2026-10-26', time:'09:00', end_time:'09:30', end_date:'2026-10-26', timezone:'Europe/Berlin', utc_offset:'UTC+01:00' })
  assert.equal(winter.zone, 'Munich Time, UTC+01:00')
  assert.equal(winter.date, 'Monday, 26 Oct 2026')
})

test('contact display does not shift local dates according to the host timezone', async () => {
  const { execFileSync } = await import('node:child_process')
  const moduleUrl = new URL('./customerCommunication.js', import.meta.url).href
  const script = `import { formatContactSlot } from ${JSON.stringify(moduleUrl)}; process.stdout.write(JSON.stringify(formatContactSlot({date:'2026-07-20', time:'09:00', end_time:'09:30', timezone:'Europe/Berlin', utc_offset:'UTC+02:00'})))`
  for (const timezone of ['Pacific/Honolulu', 'Asia/Tokyo', 'UTC']) {
    const actual = JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e', script], { env:{ ...process.env, TZ:timezone }, encoding:'utf8' }))
    assert.equal(actual.date, 'Monday, 20 Jul 2026')
    assert.equal(actual.timeRange, '09:00–09:30')
  }
})

test('contact display retains individual local dates and never invents an unavailable slot', async () => {
  const { formatContactSlot } = await import('./customerCommunication.js')
  assert.equal(formatContactSlot(null), null)
  const customer = formatContactSlot({ date:'2026-07-13', end_date:'2026-07-13', time:'16:00', end_time:'16:30', timezone:'Pacific/Honolulu', label:'Hawaii', utc_offset:'UTC-10:00' })
  assert.equal(customer.date, 'Monday, 13 Jul 2026')
  assert.equal(customer.zone, 'Hawaii, UTC-10:00')
})
