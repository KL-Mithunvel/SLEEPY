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

    // News panel
    newsItems: [],
    loadingNews: false,
    newsError: null,
    newsWatchStatus: null,   // null | 'queued' | 'error'
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

    async fetchNews() {
      this.loadingNews = true
      this.newsError = null
      try {
        const data = await apiGet('/api/corpus/news-items?limit=15')
        this.newsItems = data.items || []
      } catch (e) {
        this.newsError = e.message
      } finally {
        this.loadingNews = false
      }
    },

    async triggerNewsWatch() {
      this.newsWatchStatus = 'queued'
      try {
        await apiPost('/api/corpus/news-watch', {})
      } catch (e) {
        this.newsWatchStatus = 'error'
      }
    },

    async submitFeedback(bullet, feedback) {
      try {
        await apiPost('/api/corpus/news-feedback', { bullet, feedback })
        const item = this.newsItems.find(n => n.bullet === bullet)
        if (item) item.feedback = feedback
      } catch { /* silent */ }
    },

    async toggleTask(task) {
      try {
        await apiPost('/api/today/tasks/toggle', { rel_path: task.rel_path, text: task.text })
        this.tasks = this.tasks.filter(t => t !== task)
      } catch (e) {
        this.error = e.message
      }
    },

    async markClicked(bullet) {
      try {
        await apiPost('/api/corpus/news-click', { bullet })
        const item = this.newsItems.find(n => n.bullet === bullet)
        if (item) item.clicked = true
      } catch { /* silent — clicking through shouldn't block on tracking */ }
    },
  },
})
