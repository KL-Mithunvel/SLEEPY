import { defineStore } from 'pinia'
import { apiPost, apiStream } from '../api.js'

// A staged action's op ('write' | 'move' | 'delete') decides which fields it
// carries (rel_path vs. src_path/dst_path) and how to describe it in chat.
function describeStagedAction(action) {
  if (action.op === 'move') {
    return {
      content: `I'll move \`${action.src_path}\` to \`${action.dst_path}\` — ${action.summary}`,
      historyContent: `[Proposed move: ${action.src_path} -> ${action.dst_path}: ${action.summary}]`,
    }
  }
  if (action.op === 'delete') {
    return {
      content: `I'll delete \`${action.rel_path}\` — ${action.summary}`,
      historyContent: `[Proposed delete of ${action.rel_path}: ${action.summary}]`,
    }
  }
  return {
    content: `I'll update \`${action.rel_path}\` — ${action.summary}`,
    historyContent: `[Proposed edit to ${action.rel_path}: ${action.summary}]`,
  }
}

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
                const { content, historyContent } = describeStagedAction(action)
                this.messages.push({
                  role: 'assistant',
                  type: 'edit',
                  content,
                  historyContent,
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
        if (msg.edit.op === 'move') {
          msg.content = `Applied — moved \`${msg.edit.src_path}\` to \`${msg.edit.dst_path}\`.`
          msg.historyContent = `[Move applied: ${msg.edit.src_path} -> ${msg.edit.dst_path}]`
        } else if (msg.edit.op === 'delete') {
          msg.content = `Applied — \`${msg.edit.rel_path}\` deleted.`
          msg.historyContent = `[Delete applied: ${msg.edit.rel_path}]`
        } else {
          msg.content = `Applied — \`${msg.edit.rel_path}\` committed.`
          msg.historyContent = `[Edit applied to ${msg.edit.rel_path}: ${msg.edit.summary}]`
        }
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
        const label = msg.edit.op === 'move'
          ? `move of ${msg.edit.src_path} to ${msg.edit.dst_path}`
          : msg.edit.op === 'delete'
            ? `delete of ${msg.edit.rel_path}`
            : `edit to ${msg.edit.rel_path}`
        msg.historyContent = `[${label} was discarded by user — they will clarify]`
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
