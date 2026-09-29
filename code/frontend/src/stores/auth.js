import { defineStore } from 'pinia'
import { ref } from 'vue'

const TOKEN_KEY = 'sleepy_token'
// The token lives in sessionStorage, not localStorage: closing the tab or the
// browser ends the session, so an unattended machine can't reopen it.
const REFRESH_AFTER_MS = 4 * 60 * 1000  // server token TTL is 20 min; renew well inside it

function _loadStoredToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

function _storeToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // sessionStorage unavailable (private browsing, blocked site data, etc.) —
    // the session just won't persist across reloads.
  }
}

export const useAuthStore = defineStore('auth', () => {
  const loading = ref(true)
  const authenticated = ref(false)
  const user = ref(null)
  const error = ref('')
  const notice = ref('')            // non-error banner on the login screen (e.g. idle sign-out)
  const idleTimeoutMinutes = ref(15)
  const devBypass = ref(false)      // dev mode has no login, so no idle sign-out either

  // Internal — not exposed
  let _devBypass = false
  let _token = null
  let _lastRefresh = 0

  async function _fetchMe() {
    const headers = {}
    if (_token) headers['Authorization'] = `Bearer ${_token}`
    const res = await fetch('/api/auth/me', { headers })
    if (!res.ok) throw new Error('not authenticated')
    return res.json()
  }

  async function init() {
    try {
      const res = await fetch('/api/auth/config')
      const cfg = await res.json()
      if (cfg.idleTimeoutMinutes) idleTimeoutMinutes.value = cfg.idleTimeoutMinutes

      if (cfg.devBypass) {
        _devBypass = true
        devBypass.value = true
        user.value = await _fetchMe()
        authenticated.value = true
        return
      }

      _token = _loadStoredToken()
      if (_token) {
        try {
          user.value = await _fetchMe()
          authenticated.value = true
          refreshToken()
        } catch {
          _token = null
          _storeToken(null)
        }
      }
    } catch (e) {
      console.error('Auth init failed', e)
    } finally {
      loading.value = false
    }
  }

  async function login(username, password) {
    error.value = ''
    notice.value = ''
    let res
    try {
      res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
    } catch {
      error.value = 'Could not reach the server'
      return false
    }

    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      error.value = data.error || 'Login failed'
      return false
    }

    _token = data.token
    _lastRefresh = Date.now()
    _storeToken(_token)
    try {
      user.value = await _fetchMe()
      authenticated.value = true
      return true
    } catch {
      error.value = 'Login succeeded but session could not be established'
      _token = null
      _storeToken(null)
      return false
    }
  }

  /**
   * Swap the current token for a fresh one. The server issues short-lived
   * tokens, so this is what keeps an active session alive.
   */
  async function refreshToken() {
    if (_devBypass || !_token) return
    try {
      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        headers: { Authorization: `Bearer ${_token}` },
      })
      if (res.status === 401) {
        // Usually the AUTH_SESSION_MAX_HOURS cap — say so rather than dropping
        // to the login screen with no explanation.
        handleUnauthorized()
        notice.value = 'Your session has ended. Please sign in again.'
        return
      }
      if (!res.ok) return
      const data = await res.json()
      if (data.token) {
        _token = data.token
        _lastRefresh = Date.now()
        _storeToken(_token)
      }
    } catch {
      // network blip — try again on the next activity
    }
  }

  /** Renew only if the token is getting old — called on every user activity. */
  function refreshIfStale() {
    if (Date.now() - _lastRefresh > REFRESH_AFTER_MS) refreshToken()
  }

  /**
   * reason: 'idle' shows a notice on the login screen and signs out THIS
   * browser only. The server's logout revokes every token the user holds, so
   * calling it on idle would kick an actively-used phone out because a laptop
   * went quiet. The discarded token dies server-side within its short TTL
   * anyway. A deliberate logout still means "sign out everywhere".
   */
  async function logout(reason = '') {
    const token = _token
    // Drop the local session first so the UI is locked immediately, then tell
    // the server. The Bearer header is required: the route isn't public, so
    // without it the server answers 401 and never revokes anything.
    _token = null
    _storeToken(null)
    authenticated.value = false
    user.value = null
    notice.value = reason === 'idle' ? 'You were signed out after a period of inactivity.' : ''
    if (!token || _devBypass || reason === 'idle') return
    try {
      await fetch('/api/auth/logout', { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
    } catch {
      // best-effort — the token expires on its own within minutes regardless
    }
  }

  /** Called by api.js when a request comes back 401 — the token expired or was invalidated. */
  function handleUnauthorized() {
    if (_devBypass) return
    _token = null
    _storeToken(null)
    authenticated.value = false
    user.value = null
  }

  async function getToken() {
    if (_devBypass) return null
    return _token
  }

  return {
    loading, authenticated, user, error, notice, idleTimeoutMinutes, devBypass,
    init, login, logout, getToken, handleUnauthorized, refreshToken, refreshIfStale,
  }
})
