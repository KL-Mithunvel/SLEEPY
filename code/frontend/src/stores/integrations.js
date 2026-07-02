import { defineStore } from 'pinia'
import { apiGet, apiPost } from '../api.js'

export const useIntegrationsStore = defineStore('integrations', {
  state: () => ({
    status: null,          // {email} boolean
    loadingStatus: false,
    statusError: null,

    sending: false,
    sendResult: null,      // {ok: true, message: "..."} | {ok: false, error: "..."}
  }),

  actions: {
    async fetchStatus() {
      this.loadingStatus = true
      this.statusError = null
      try {
        this.status = await apiGet('/api/integrations/status')
      } catch (e) {
        this.statusError = e.message
      } finally {
        this.loadingStatus = false
      }
    },

    async queueEmail(to, subject, body) {
      this.sending = true
      this.sendResult = null
      try {
        await apiPost('/api/integrations/email', { to, subject, body })
        this.sendResult = { ok: true, message: 'Email queued for delivery.' }
      } catch (e) {
        this.sendResult = { ok: false, error: e.message }
      } finally {
        this.sending = false
      }
    },

    clearResult() {
      this.sendResult = null
    },
  },
})
