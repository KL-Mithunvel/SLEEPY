import { useAuthStore } from './stores/auth.js'

async function _fetch(method, path, body) {
  const auth = useAuthStore()
  const token = await auth.getToken()

  const headers = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  const res = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    const error = new Error(err.error || 'API error')
    error.status = res.status
    throw error
  }

  if (res.status === 204) return null
  return res.json()
}

export const apiGet    = (path)        => _fetch('GET',    path)
export const apiPost   = (path, body)  => _fetch('POST',   path, body)
export const apiPut    = (path, body)  => _fetch('PUT',    path, body)
export const apiDelete = (path)        => _fetch('DELETE', path)

/**
 * POST to a text/event-stream SSE endpoint and call onEvent for each parsed event.
 * Each SSE event is a `data: {...}` line followed by a blank line.
 */
export async function apiStream(path, body, onEvent) {
  const auth = useAuthStore()
  const token = await auth.getToken()

  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(path, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    const error = new Error(err.error || 'API error')
    error.status = res.status
    throw error
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE events separated by double newlines
    const parts = buffer.split('\n\n')
    buffer = parts.pop() // keep incomplete trailing part

    for (const part of parts) {
      for (const line of part.split('\n')) {
        if (line.startsWith('data: ')) {
          try {
            onEvent(JSON.parse(line.slice(6)))
          } catch {
            // malformed JSON — skip
          }
        }
      }
    }
  }
}
