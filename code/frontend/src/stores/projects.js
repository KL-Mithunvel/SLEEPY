import { defineStore } from 'pinia'
import { apiGet } from '../api.js'

export const useProjectsStore = defineStore('projects', {
  state: () => ({
    projects: [],
    loading: false,
    loadingContent: false,
    selectedPath: null,
    selectedContent: null,
    filter: '',
    error: null,
  }),

  getters: {
    byOu: (state) => {
      const q = state.filter.toLowerCase()
      const filtered = q
        ? state.projects.filter(p =>
            p.name.toLowerCase().includes(q) ||
            p.ou.toLowerCase().includes(q) ||
            p.status.toLowerCase().includes(q)
          )
        : state.projects

      const groups = {}
      for (const p of filtered) {
        if (!groups[p.ou]) groups[p.ou] = []
        groups[p.ou].push(p)
      }
      return groups
    },

    ouList: (state, getters) => Object.keys(getters.byOu).sort(),
  },

  actions: {
    async fetchProjects() {
      this.loading = true
      this.error = null
      try {
        const data = await apiGet('/api/projects')
        this.projects = data.projects || []
      } catch (e) {
        this.error = e.message
      } finally {
        this.loading = false
      }
    },

    async selectProject(relPath) {
      // Toggle off if already selected
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
        const data = await apiGet(`/api/projects/content?path=${encodeURIComponent(relPath)}`)
        this.selectedContent = data.content
      } catch (e) {
        this.error = e.message
      } finally {
        this.loadingContent = false
      }
    },
  },
})
