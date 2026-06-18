import { defineStore } from 'pinia'
import { apiGet } from '../api.js'

export const useLogsStore = defineStore('logs', {
  state: () => ({
    logs: [],
    loading: false,
    loadingContent: false,
    selectedPath: null,
    selectedContent: null,
    typeFilter: 'all',   // 'all' | 'daily' | 'weekly'
    error: null,
  }),

  getters: {
    filtered: (state) =>
      state.typeFilter === 'all'
        ? state.logs
        : state.logs.filter(l => l.type === state.typeFilter),
  },

  actions: {
    async fetchLogs() {
      this.loading = true
      this.error = null
      try {
        const data = await apiGet('/api/logs')
        this.logs = data.logs || []
      } catch (e) {
        this.error = e.message
      } finally {
        this.loading = false
      }
    },

    async selectLog(relPath) {
      if (this.selectedPath === relPath) {
        this.selectedPath = null
        this.selectedContent = null
        return
      }
      this.selectedPath = relPath
      this.loadingContent = true
      this.selectedContent = null
      this.error = null
      try {
        const data = await apiGet(`/api/logs/content?path=${encodeURIComponent(relPath)}`)
        this.selectedContent = data.content
      } catch (e) {
        this.error = e.message
      } finally {
        this.loadingContent = false
      }
    },
  },
})
