import { defineStore } from 'pinia'
import { apiPost, apiStream } from '../api.js'

export const useAiStore = defineStore('ai', {
  state: () => ({
    messages: [],
    loading: false,
    error: null,
    inputText: '',
    contentVersion: 0,  // incremented on each delta; watch in view for scroll
  }),

  getters: {
    // Conversation history for the backend in Anthropic message format.
    // Excludes in-progress streaming placeholders (incomplete content).
    history(state) {
      return state.messages
        .filter(m => !m.streaming)
        .map(m => ({
          role: m.role,
          content: m.historyContent ?? m.content,
        }))
    },

    hasStreamingContent(state) {
      return state.messages.some(m => m.streaming && m.content.length > 0)
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

      // Capture history before pushing placeholder so it's not included
      const historyForApi = this.history

      const placeholderIdx = this.messages.length
      this.messages.push({
        role: 'assistant',
        type: 'answer',
        content: '',
        historyContent: '',
        streaming: true,
        toolProgress: null,
      })

      try {
        await apiStream(
          '/api/ai/chat',
          { messages: historyForApi },
          (event) => {
            if (event.type === 'delta') {
              this.messages[placeholderIdx].content += event.text
              this.contentVersion++

            } else if (event.type === 'tool_progress') {
              const e = event.event
              if (e.type === 'tool_start') {
                this.messages[placeholderIdx].toolProgress = e.name
              } else if (e.type === 'tool_end') {
                this.messages[placeholderIdx].toolProgress = null
              }

            } else if (event.type === 'done') {
              const result = event.result
              this.messages[placeholderIdx].streaming = false
              this.messages[placeholderIdx].toolProgress = null
              this.messages[placeholderIdx].historyContent =
                this.messages[placeholderIdx].content

              for (const action of (result.actions || [])) {
                this.messages.push({
                  role: 'assistant',
                  type: 'edit',
                  content: `I'll update \`${action.rel_path}\` — ${action.summary}`,
                  historyContent: `[Proposed edit to ${action.rel_path}: ${action.summary}]`,
                  edit: action,
                  settled: false,
                  confirmed: false,
                })
              }

            } else if (event.type === 'error') {
              this.messages[placeholderIdx].type = 'error'
              this.messages[placeholderIdx].content = event.message
              this.messages[placeholderIdx].streaming = false
              this.messages[placeholderIdx].toolProgress = null
              this.error = event.message
            }
          }
        )
      } catch (e) {
        this.messages[placeholderIdx].type = 'error'
        this.messages[placeholderIdx].content = e.message
        this.messages[placeholderIdx].streaming = false
        this.messages[placeholderIdx].toolProgress = null
        this.error = e.message
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
      this.contentVersion = 0
    },
  },
})
