import { defineStore } from 'pinia'
import { apiGet, apiPost } from '../api.js'

export const useTodayStore = defineStore('today', {
  state: () => ({
    briefing: null,
    briefingAt: null,
    tasks: [],
    loadingToday: false,
    loadingBriefing: false,
    error: null,
  }),

  actions: {
    async fetchToday() {
      this.loadingToday = true
      this.error = null
      try {
        const data = await apiGet('/api/today')
        this.briefing = data.briefing
        this.briefingAt = data.briefing_at
        this.tasks = data.tasks || []
      } catch (e) {
        this.error = e.message
      } finally {
        this.loadingToday = false
      }
    },

    async generateBriefing() {
      this.loadingBriefing = true
      this.error = null
      try {
        const data = await apiPost('/api/today/briefing', {})
        this.briefing = data.briefing
        this.briefingAt = data.generated_at
      } catch (e) {
        this.error = e.message
      } finally {
        this.loadingBriefing = false
      }
    },
  },
})
