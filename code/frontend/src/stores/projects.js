import { defineStore } from 'pinia'
import { apiGet, apiPost, apiPut } from '../api.js'

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

    // Structured editor (project files only)
    mode: 'read',            // 'read' | 'edit' | 'raw'
    structured: null,        // parse_structured() result for the selected file
    loadingStructured: false,
    structuredError: null,
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
      this.structured = null
      this.structuredError = null
      this.mode = 'read'
      try {
        const data = await apiGet(`/api/projects/content?path=${encodeURIComponent(relPath)}`)
        this.selectedContent = data.content
        this.editedContent = data.content
      } catch (e) {
        this.error = e.message
      } finally {
        this.loadingContent = false
      }
      // Structured data is only meaningful for project files, but fetching it
      // is cheap and self-contained — a non-project file just gets a 404/empty
      // result the view ignores (it only shows the structured editor when
      // selectedMeta is set, i.e. the file is a known project entry).
      this.fetchStructured()
    },

    closeFile() {
      this.selectedPath = null
      this.selectedContent = null
      this.editedContent = null
      this.saveError = null
      this.structured = null
      this.structuredError = null
      this.mode = 'read'
    },

    async fetchStructured() {
      if (!this.selectedPath) return
      this.loadingStructured = true
      this.structuredError = null
      try {
        this.structured = await apiGet(`/api/projects/structured?path=${encodeURIComponent(this.selectedPath)}`)
      } catch (e) {
        this.structured = null
        this.structuredError = e.message
      } finally {
        this.loadingStructured = false
      }
    },

    async addTask(text, priority, due) {
      await apiPost('/api/projects/tasks', { path: this.selectedPath, action: 'add', text, priority, due })
      await this.fetchStructured()
      this.fetchProjects()
    },

    async editTask(task, { description, done, priority, due }) {
      await apiPost('/api/projects/tasks', {
        path: this.selectedPath, action: 'edit', old_line: task.line,
        text: description ?? task.description, done: done ?? task.done,
        priority: priority !== undefined ? priority : task.priority,
        due: due !== undefined ? due : task.due,
      })
      await this.fetchStructured()
      this.fetchProjects()
    },

    async toggleTask(task) {
      await this.editTask(task, { done: !task.done })
    },

    async removeTask(task) {
      await apiPost('/api/projects/tasks', { path: this.selectedPath, action: 'remove', old_line: task.line })
      await this.fetchStructured()
      this.fetchProjects()
    },

    async addListItem(section, text) {
      await apiPost('/api/projects/list-item', { path: this.selectedPath, section, action: 'add', text })
      await this.fetchStructured()
    },

    async removeListItem(section, text) {
      await apiPost('/api/projects/list-item', { path: this.selectedPath, section, action: 'remove', text })
      await this.fetchStructured()
    },

    async saveSection(heading, text) {
      await apiPut('/api/projects/section', { path: this.selectedPath, heading, text })
      await this.fetchStructured()
    },

    async setStatus(status) {
      const data = await apiPut('/api/projects/status', { path: this.selectedPath, status })
      if (data.rel_path !== this.selectedPath) {
        this.selectedPath = data.rel_path
      }
      await this.fetchStructured()
      this.fetchProjects()
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
