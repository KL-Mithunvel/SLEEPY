import { defineStore } from 'pinia'
import { apiGet, apiPut } from '../api.js'

export const useProjectsStore = defineStore('projects', {
  state: () => ({
    projects: [],
    loading: false,
    loadingContent: false,
    selectedPath: null,
    selectedContent: null,   // last content loaded from / saved to the server
    editedContent: null,     // textarea's current (possibly unsaved) content
    saving: false,
    saveError: null,
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

    // Pinia option-store getters take a single arg (the store itself, state + other
    // getters merged) — not Vuex's (state, getters) pair. `state.byOu` resolves fine.
    ouList: (state) => Object.keys(state.byOu).sort(),

    isDirty: (state) => state.selectedContent !== null && state.editedContent !== state.selectedContent,
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

    async selectFile(relPath) {
      this.selectedPath = relPath
      this.loadingContent = true
      this.selectedContent = null
      this.editedContent = null
      this.saveError = null
      this.error = null
      try {
        const data = await apiGet(`/api/projects/content?path=${encodeURIComponent(relPath)}`)
        this.selectedContent = data.content
        this.editedContent = data.content
      } catch (e) {
        this.error = e.message
      } finally {
        this.loadingContent = false
      }
    },

    closeFile() {
      this.selectedPath = null
      this.selectedContent = null
      this.editedContent = null
      this.saveError = null
    },

    discardEdits() {
      this.editedContent = this.selectedContent
      this.saveError = null
    },

    async saveContent() {
      if (!this.selectedPath || this.editedContent === null) return
      this.saving = true
      this.saveError = null
      try {
        await apiPut('/api/projects/content', { path: this.selectedPath, content: this.editedContent })
        this.selectedContent = this.editedContent
        // Refresh the list so status/task-count badges reflect the edit
        this.fetchProjects()
      } catch (e) {
        this.saveError = e.message
      } finally {
        this.saving = false
      }
    },
  },
})
