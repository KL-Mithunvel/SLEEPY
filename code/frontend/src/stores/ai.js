import { defineStore } from 'pinia'
import { apiPost } from '../api.js'

export const useAiStore = defineStore('ai', {
  state: () => ({
    messages: [],
    loading: false,
    error: null,
    inputText: '',
  }),

  getters: {
    // Serialised history for the backend — keeps the LLM aware of prior turns
    history(state) {
      return state.messages.map(m => ({
        role: m.role,
        content: m.historyContent ?? m.content,
      }))
    },
  },

  actions: {
    async send(text) {
      const trimmed = text.trim()
      if (!trimmed || this.loading) return

      this.messages.push({ role: 'user', content: trimmed, historyContent: trimmed })
      this.inputText = ''
      this.loading = true
      this.error = null

      try {
        const data = await apiPost('/api/ai/chat', {
          message: trimmed,
          history: this.history.slice(0, -1), // exclude the message we just pushed
        })

        if (data.type === 'edit') {
          this.messages.push({
            role: 'assistant',
            type: 'edit',
            content: `I'll update \`${data.rel_path}\` — ${data.summary}`,
            historyContent: `[Proposed edit to ${data.rel_path}: ${data.summary}]`,
            edit: data,
            settled: false,
            confirmed: false,
          })
        } else {
          this.messages.push({
            role: 'assistant',
            type: 'answer',
            content: data.content,
            historyContent: data.content,
          })
        }
      } catch (e) {
        this.error = e.message
        this.messages.push({
          role: 'assistant',
          type: 'error',
          content: e.message,
          historyContent: `[Error: ${e.message}]`,
        })
      } finally {
        this.loading = false
      }
    },

    async confirmEdit(idx) {
      const msg = this.messages[idx]
      if (!msg?.edit || msg.settled) return
      try {
        await apiPost(`/api/ai/edit/${msg.edit.event_id}/confirm`, {})
        msg.settled = true
        msg.confirmed = true
        msg.content = `Applied — \`${msg.edit.rel_path}\` committed.`
        msg.historyContent = `[Edit applied to ${msg.edit.rel_path}: ${msg.edit.summary}]`
      } catch (e) {
        this.error = e.message
      }
    },

    async discardEdit(idx) {
      const msg = this.messages[idx]
      if (!msg?.edit || msg.settled) return
      try {
        await apiPost(`/api/ai/edit/${msg.edit.event_id}/reject`, {})
        msg.settled = true
        msg.confirmed = false
        msg.content = `Discarded. Tell me what you meant and I'll try again.`
        msg.historyContent = `[Edit to ${msg.edit.rel_path} was discarded by user — they will clarify]`
      } catch (e) {
        this.error = e.message
      }
    },

    clear() {
      this.messages = []
      this.error = null
      this.inputText = ''
    },
  },
})
