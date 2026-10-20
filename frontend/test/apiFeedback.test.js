import assert from 'node:assert/strict'
import test from 'node:test'
import { createFeedbackApi, knowledgeReviewModel } from '../src/apiFeedback.js'

const duplicate = {
  success:false, error:'Potential duplicate', governance_review_required:true,
  error_info:{ code:'DUPLICATE_KNOWLEDGE', message:'Potential duplicate', retryable:false },
  warnings:[{ code:'potential_duplicate', sources:[{ record_id:'KBA-A', title:'Existing task guidance', source_system:'SAP MM', issue_category:'Approval Action', source_incident:'INC990001' }] }],
}

test('normal successful operation does not open any popup', async () => {
  const popups = []
  const api = createFeedbackApi(async ()=>({ success:true, article:{ article_id:'A' } }), (info)=>popups.push(info))
  await api('/api/tickets/A/knowledge-articles', { method:'POST' })
  assert.equal(popups.length, 0)
})

test('duplicate warning retains source reference and delegates to confirmation modal', async () => {
  const popups = []
  const api = createFeedbackApi(async ()=>{ const error = new Error('Potential duplicate'); error.payload = duplicate; throw error }, (info)=>popups.push(info))
  await assert.rejects(api('/api/tickets/B/knowledge-articles'), /Potential duplicate/)
  const model = knowledgeReviewModel(duplicate)
  assert.equal(model.title, 'Similar Knowledge Article Found')
  assert.equal(model.sources[0].source_incident, 'INC990001')
  assert.equal(popups.length, 0) // The dedicated confirmation dialog handles this warning.
})

test('timeout popup Retry sends the same failed request again only on user action', async () => {
  let calls = 0
  let popup
  const options = { method:'POST', body:JSON.stringify({ message:'Investigate task', history:[] }) }
  const api = createFeedbackApi(async (url, actual)=>{
    assert.equal(url, '/api/tickets/A/chat')
    assert.deepEqual(actual, options)
    calls++
    if (calls === 1) throw new Error('private transport details')
    return { success:true, reply:'Completed' }
  }, (feedback)=>{ popup = feedback })
  const submit = ()=>api('/api/tickets/A/chat', { ...options, onRetry:submit })
  await assert.rejects(submit())
  assert.equal(calls, 1)
  assert.equal(popup.notices[0].message.includes('private transport details'), false)
  await popup.retry()
  assert.equal(calls, 2)
})

test('nonfatal missing evidence notices open a popup without discarding investigation', async () => {
  let popup
  const result = { success:true, investigation:{ ticket:{ incident_number:'A' } }, notices:[{ code:'INCOMPLETE_TICKET', title:'Insufficient Ticket Information', details:{ missing_fields:['short_description'] } }] }
  const api = createFeedbackApi(async ()=>result, (feedback)=>{ popup=feedback })
  assert.equal(await api('/api/tickets/A/investigation'), result)
  assert.deepEqual(popup.notices[0].details.missing_fields, ['short_description'])
})

test('save failure rejects success path while preserving unsaved form content', async () => {
  const form = { title:'My unsaved title', symptoms:'My unsaved symptoms' }
  let success = false
  let popup
  const api = createFeedbackApi(async ()=>({ success:false, error:'Could not persist.', error_info:{ code:'SAVE_FAILED', message:'Could not persist.', retryable:true } }), (feedback)=>{ popup=feedback })
  await assert.rejects(async ()=>{ await api('/api/knowledge-articles/A', { method:'PATCH', body:JSON.stringify(form) }); success=true })
  assert.equal(success, false)
  assert.deepEqual(form, { title:'My unsaved title', symptoms:'My unsaved symptoms' })
  assert.equal(popup.notices[0].code, 'SAVE_FAILED')
})
