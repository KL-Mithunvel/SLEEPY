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
    addTaskError: null,

    // News panel
    newsItems: [],
    loadingNews: false,
    newsError: null,
    newsWatchStatus: null,   // null | 'queued' | 'processing' | 'no_projects' | 'error'
    _newsWatchTimer: null,
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
      this.cancelNewsWatchPoll()
      this.newsWatchStatus = 'queued'
      try {
        await apiPost('/api/corpus/news-watch', {})
        this._pollNewsWatchStatus(0)
      } catch (e) {
        this.newsWatchStatus = 'error'
      }
    },

    // Submitting the batch is instant, but results only land after Anthropic
    // finishes processing (the every-5-min news_watch_finalize cron) — poll
    // status until it's done, then pull the fresh items in automatically.
    async _pollNewsWatchStatus(attempt) {
      const MAX_ATTEMPTS = 40 // ~10 min at 15s
      let data
      try {
        data = await apiGet('/api/corpus/news-watch/status')
      } catch (e) {
        this.newsWatchStatus = 'error'
        return
      }
      if (data.status === 'complete') {
        this.newsWatchStatus = null
        await this.fetchNews()
        return
      }
      if (data.status === 'no_projects' || data.status === 'no_requests') {
        this.newsWatchStatus = 'no_projects'
        return
      }
      if (data.status === 'error') {
        this.newsWatchStatus = 'error'
        return
      }
      if (attempt >= MAX_ATTEMPTS) {
        this.newsWatchStatus = 'error'
        return
      }
      // 'no_batch' (submit task hasn't drained yet) / 'in_progress' / 'processing_results'
      this.newsWatchStatus = 'processing'
      this._newsWatchTimer = setTimeout(() => this._pollNewsWatchStatus(attempt + 1), 15000)
    },

    cancelNewsWatchPoll() {
      if (this._newsWatchTimer) {
        clearTimeout(this._newsWatchTimer)
        this._newsWatchTimer = null
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

    async cancelTask(task) {
      try {
        await apiPost('/api/today/tasks/cancel', { rel_path: task.rel_path, text: task.text })
        this.tasks = this.tasks.filter(t => t !== task)
      } catch (e) {
        this.error = e.message
      }
    },

    async addTask({ projectRelPath, text, priority, due }) {
      this.addTaskError = null
      try {
        await apiPost('/api/today/tasks/add', {
          project_rel_path: projectRelPath,
          text,
          priority: priority || null,
          due: due || null,
        })
        await this.fetchToday()
        return true
      } catch (e) {
        this.addTaskError = e.message
        return false
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
