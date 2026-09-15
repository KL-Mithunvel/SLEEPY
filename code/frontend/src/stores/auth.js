import { defineStore } from 'pinia'
import { ref } from 'vue'

const TOKEN_KEY = 'sleepy_token'

function _loadStoredToken() {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

function _storeToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // localStorage unavailable (private browsing, blocked site data, etc.) —
    // the session just won't persist across reloads.
  }
}

export const useAuthStore = defineStore('auth', () => {
  const loading = ref(true)
  const authenticated = ref(false)
  const user = ref(null)
  const error = ref('')

  // Internal — not exposed
  let _devBypass = false
  let _token = null

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

      if (cfg.devBypass) {
        _devBypass = true
        user.value = await _fetchMe()
        authenticated.value = true
        return
      }

      _token = _loadStoredToken()
      if (_token) {
        try {
          user.value = await _fetchMe()
          authenticated.value = true
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

  async function logout() {
    try {
      await fetch('/api/auth/logout', { method: 'POST' })
    } catch {
      // best-effort — the client-side token discard below is what matters
    }
    _token = null
    _storeToken(null)
    authenticated.value = false
    user.value = null
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

  return { loading, authenticated, user, error, init, login, logout, getToken, handleUnauthorized }
})
