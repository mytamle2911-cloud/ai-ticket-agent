export const DEMO_STATE_KEY = 'ticket-agent-demo-state-v1'

export function createBrowserDemoApi({ fetchImpl, storage, locks, onUnauthorized }) {
  let queue = Promise.resolve()

  const request = async (url, options = {}) => {
    const method = (options.method || 'GET').toUpperCase()
    let payload
    if (options.body) {
      try { payload = JSON.parse(options.body) }
      catch { throw new Error('API request body must be valid JSON.') }
    }
    const reset = options.demoReset === true
    const currentState = storage.getItem(DEMO_STATE_KEY)
    const envelope = {
      path:url,
      method,
      ...(payload !== undefined ? { payload } : {}),
      ...(reset ? { reset:true } : {}),
      ...(!reset && currentState ? { state:currentState } : {}),
    }
    const response = await fetchImpl('/api/demo/dispatch', {
      method:'POST',
      cache:'no-store',
      credentials:'same-origin',
      headers:{ 'Content-Type':'application/json' },
      body:JSON.stringify(envelope),
    })
    const responsePayload = await response.json().catch(() => ({}))
    if (!response.ok) {
      if (response.status === 401) onUnauthorized?.()
      const error = new Error(responsePayload.error || `Request failed (${response.status})`)
      error.payload = responsePayload
      error.status = response.status
      throw error
    }
    if (typeof responsePayload.demo_state !== 'string' || !Object.hasOwn(responsePayload, 'result')) {
      throw new Error('The demo API returned an invalid state response.')
    }
    storage.setItem(DEMO_STATE_KEY, responsePayload.demo_state)
    if (responsePayload.status_code < 200 || responsePayload.status_code >= 300) {
      const error = new Error(responsePayload.result?.error || `Request failed (${responsePayload.status_code})`)
      error.payload = responsePayload.result || {}
      error.status = responsePayload.status_code
      throw error
    }
    return responsePayload.result
  }

  return (url, options = {}) => {
    const pending = queue.then(() => locks?.request
      ? locks.request('ticket-agent-browser-demo-state', () => request(url, options))
      : request(url, options))
    queue = pending.catch(() => {})
    return pending
  }
}
