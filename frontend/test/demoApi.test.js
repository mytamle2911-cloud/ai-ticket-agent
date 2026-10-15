import assert from 'node:assert/strict'
import test from 'node:test'
import { createBrowserDemoApi, DEMO_STATE_KEY } from '../src/demoApi.js'

function response(result, state, statusCode = 200, httpStatus = 200) {
  return {
    ok:httpStatus >= 200 && httpStatus < 300,
    status:httpStatus,
    async json() {
      return { result, status_code:statusCode, demo_state:state }
    },
  }
}

function memoryStorage() {
  const values = new Map()
  return {
    getItem:(key) => values.get(key) || null,
    setItem:(key, value) => values.set(key, value),
  }
}

test('browser state is sent on later requests and survives an app refresh', async () => {
  const storage = memoryStorage()
  const calls = []
  let revision = 0
  const fetchImpl = async (_url, init) => {
    calls.push(JSON.parse(init.body))
    return response({ ok:true }, 'signed-state-' + (++revision))
  }
  const firstApp = createBrowserDemoApi({ fetchImpl, storage })
  await firstApp('/api/tickets')
  await firstApp('/api/tickets/INC100001', {
    method:'PATCH',
    body:JSON.stringify({ status:'In Progress', confirmed:true }),
  })
  const refreshedApp = createBrowserDemoApi({ fetchImpl, storage })
  await refreshedApp('/api/tickets')

  assert.equal(calls[0].state, undefined)
  assert.equal(calls[1].state, 'signed-state-1')
  assert.equal(calls[1].payload.status, 'In Progress')
  assert.equal(calls[2].state, 'signed-state-2')
  assert.equal(storage.getItem(DEMO_STATE_KEY), 'signed-state-3')
})

test('resolution and article write requests share the updated state token', async () => {
  const storage = memoryStorage()
  const calls = []
  let revision = 0
  const api = createBrowserDemoApi({
    storage,
    fetchImpl:async (_url, init) => {
      calls.push(JSON.parse(init.body))
      return response({ saved:true }, 'state-' + (++revision))
    },
  })
  await api('/api/tickets/INC100006/resolutions', {
    method:'POST', body:JSON.stringify({ consultant_confirmed:true }),
  })
  await api('/api/knowledge-articles/KBA-1', {
    method:'PATCH', body:JSON.stringify({ approve:true, approved_by:'REVIEWER-1' }),
  })
  await api('/api/knowledge-articles/search?source_system=SAP%20SD')
  assert.equal(calls[1].state, 'state-1')
  assert.equal(calls[1].payload.approve, true)
  assert.equal(calls[2].state, 'state-2')
})

test('reset request omits the previous browser token and saves the fresh snapshot', async () => {
  const storage = memoryStorage()
  storage.setItem(DEMO_STATE_KEY, 'old-state')
  let resetRequest
  const api = createBrowserDemoApi({
    storage,
    fetchImpl:async (_url, init) => {
      resetRequest = JSON.parse(init.body)
      return response({ tickets:[] }, 'initial-state')
    },
  })
  await api('/api/tickets', { demoReset:true })
  assert.equal(resetRequest.reset, true)
  assert.equal(resetRequest.state, undefined)
  assert.equal(storage.getItem(DEMO_STATE_KEY), 'initial-state')
})

test('backend error status is preserved and the returned signed snapshot is retained', async () => {
  const storage = memoryStorage()
  const api = createBrowserDemoApi({
    storage,
    fetchImpl:async () => response({ error:'Ticket not found' }, 'current-state', 404),
  })
  await assert.rejects(api('/api/missing'), (error) => {
    assert.equal(error.status, 404)
    assert.equal(error.message, 'Ticket not found')
    return true
  })
  assert.equal(storage.getItem(DEMO_STATE_KEY), 'current-state')
})

test('hosted requests send same-origin cookies and notify the app when a session expires', async () => {
  const storage = memoryStorage()
  storage.setItem(DEMO_STATE_KEY, 'signed-demo-state')
  let request
  let unauthorized = 0
  const api = createBrowserDemoApi({
    storage,
    onUnauthorized:() => { unauthorized += 1 },
    fetchImpl:async (_url, init) => {
      request = init
      return { ok:false, status:401, async json() { return { error:'Sign in required.' } } }
    },
  })
  await assert.rejects(api('/api/tickets'), /Sign in required/)
  assert.equal(request.credentials, 'same-origin')
  assert.equal(unauthorized, 1)
  assert.equal(storage.getItem(DEMO_STATE_KEY), 'signed-demo-state')
})
