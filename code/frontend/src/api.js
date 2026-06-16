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
