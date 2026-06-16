import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  const loading = ref(true)
  const authenticated = ref(false)
  const user = ref(null)

  // Internal — not exposed
  let _keycloak = null
  let _devBypass = false

  async function init() {
    try {
      const res = await fetch('/api/auth/config')
      const cfg = await res.json()

      if (cfg.devBypass) {
        _devBypass = true
        const meRes = await fetch('/api/auth/me')
        if (meRes.ok) {
          user.value = await meRes.json()
          authenticated.value = true
        }
        loading.value = false
        return
      }

      // Keycloak PKCE flow
      const Keycloak = (await import('keycloak-js')).default
      _keycloak = new Keycloak({ url: cfg.url, realm: cfg.realm, clientId: cfg.clientId })

      const authed = await _keycloak.init({ onLoad: 'login-required', pkceMethod: 'S256' })
      if (authed) {
        const meRes = await fetch('/api/auth/me', {
          headers: { Authorization: `Bearer ${_keycloak.token}` },
        })
        if (meRes.ok) {
          user.value = await meRes.json()
          authenticated.value = true
        }
      }
    } catch (e) {
      console.error('Auth init failed', e)
    } finally {
      loading.value = false
    }
  }

  async function getToken() {
    if (_devBypass) return null
    if (_keycloak) {
      await _keycloak.updateToken(30)
      return _keycloak.token
    }
    return null
  }

  return { loading, authenticated, user, init, getToken }
})
