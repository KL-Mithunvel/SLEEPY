<script setup>
import { ref, nextTick, watch } from 'vue'
import { useAiStore } from '../stores/ai.js'

const ai = useAiStore()
const messagesEl = ref(null)
const inputEl = ref(null)

function scrollToBottom() {
  nextTick(() => {
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    }
  })
}

watch(() => ai.messages.length, scrollToBottom)
watch(() => ai.contentVersion, scrollToBottom)

function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submit()
  }
}

async function submit() {
  if (!ai.inputText.trim() || ai.loading) return
  await ai.send(ai.inputText)
}

function diffLineClass(line) {
  if (line.startsWith('+++') || line.startsWith('---')) return 'diff-header'
  if (line.startsWith('+')) return 'diff-add'
  if (line.startsWith('-')) return 'diff-remove'
  if (line.startsWith('@@')) return 'diff-hunk'
  return 'diff-context'
}

const TOOL_LABELS = {
  load_skill:    'Loading skill',
  grep:          'Searching files',
  read_file:     'Reading file',
  read_src:      'Reading docs',
  list_src:      'Listing docs',
  list_files:    'Listing files',
  search_corpus: 'Searching corpus',
  send_email:    'Sending email',
}

function toolLabel(name) {
  return TOOL_LABELS[name] || `Using ${name}`
}
</script>

<template>
  <div class="ai-view">
    <!-- Header -->
    <div class="ai-header">
      <div class="d-flex align-items-center gap-2">
        <i class="bi bi-stars text-accent" style="font-size: 1.1rem;"></i>
        <span class="fw-semibold" style="font-size: 0.95rem;">AI Assistant</span>
      </div>
      <button
        class="btn btn-sm btn-outline-secondary"
        style="font-size: 0.75rem;"
        :disabled="ai.loading || !ai.messages.length"
        @click="ai.clear()"
      >
        <i class="bi bi-trash me-1"></i>Clear
      </button>
    </div>

    <!-- Messages -->
    <div ref="messagesEl" class="ai-messages">
      <!-- Empty state -->
      <div v-if="!ai.messages.length" class="ai-empty">
        <i class="bi bi-chat-dots" style="font-size: 2.2rem; opacity: 0.25;"></i>
        <p class="mt-3 mb-1" style="font-size: 0.9rem;">Tell me what to do or ask me anything.</p>
        <p style="font-size: 0.78rem; opacity: 0.5;">
          "What's the status of the infra project?"<br>
          "Add a task: call client on Friday to SMTW/finance.md"<br>
          "Mark the budget review as done"
        </p>
      </div>

      <template v-for="(msg, i) in ai.messages" :key="i">
        <!-- User bubble -->
        <div v-if="msg.role === 'user'" class="msg-row msg-row--user">
          <div class="msg-bubble msg-bubble--user">{{ msg.content }}</div>
        </div>

        <!-- AI answer (streaming or complete) -->
        <div v-else-if="msg.type === 'answer'" class="msg-row msg-row--ai">
          <div class="msg-bubble msg-bubble--ai">
            <!-- Tool progress pill -->
            <div v-if="msg.toolProgress" class="msg-tool-pill">
              <span class="spinner-border spinner-border-sm me-1"
                    style="width:9px;height:9px;border-width:1.5px;"></span>
              {{ toolLabel(msg.toolProgress) }}…
            </div>
            <!-- Thinking dots when connected but no content yet -->
            <div v-else-if="msg.streaming && !msg.content" class="msg-thinking" style="padding:0;">
              <span class="thinking-dot"></span>
              <span class="thinking-dot"></span>
              <span class="thinking-dot"></span>
            </div>
            <!-- Text content -->
            <div v-if="msg.content" class="msg-content" style="white-space: pre-wrap;">{{ msg.content }}<span v-if="msg.streaming" class="msg-cursor">█</span></div>
          </div>
        </div>

        <!-- AI edit proposal -->
        <div v-else-if="msg.type === 'edit'" class="msg-row msg-row--ai">
          <div class="msg-edit-card">
            <!-- Header -->
            <div class="msg-edit-header">
              <i class="bi bi-pencil-square me-2" style="color: var(--accent);"></i>
              <span style="font-size: 0.82rem;">
                <template v-if="msg.settled && msg.confirmed">
                  <i class="bi bi-check-circle-fill text-success me-1"></i>
                  Applied — <code style="font-size: 0.78rem;">{{ msg.edit.rel_path }}</code>
                </template>
                <template v-else-if="msg.settled && !msg.confirmed">
                  <i class="bi bi-x-circle me-1" style="color: #f87171;"></i>
                  Discarded
                </template>
                <template v-else>
                  Proposed edit to <code style="font-size: 0.78rem;">{{ msg.edit.rel_path }}</code>
                </template>
              </span>
            </div>

            <!-- Summary -->
            <div class="msg-edit-summary">{{ msg.edit.summary }}</div>

            <!-- Diff -->
            <div v-if="!msg.settled" class="diff-viewer">
              <div
                v-for="(line, li) in msg.edit.diff.split('\n')"
                :key="li"
                :class="['diff-line', diffLineClass(line)]"
              >{{ line }}</div>
            </div>

            <!-- Actions -->
            <div v-if="!msg.settled" class="msg-edit-actions">
              <button
                class="btn btn-sm btn-success"
                style="font-size: 0.78rem;"
                @click="ai.confirmEdit(i)"
              >
                <i class="bi bi-check-lg me-1"></i>Apply
              </button>
              <button
                class="btn btn-sm btn-outline-secondary"
                style="font-size: 0.78rem;"
                @click="ai.discardEdit(i)"
              >
                <i class="bi bi-x-lg me-1"></i>Discard
              </button>
              <span style="font-size: 0.72rem; color: var(--text-muted-custom); margin-left: auto;">
                Not right? Discard and explain what you meant.
              </span>
            </div>
          </div>
        </div>

        <!-- AI error -->
        <div v-else-if="msg.type === 'error'" class="msg-row msg-row--ai">
          <div class="msg-bubble msg-bubble--error">
            <i class="bi bi-exclamation-triangle me-1"></i>{{ msg.content }}
          </div>
        </div>
      </template>

      <!-- Thinking indicator — shown only before the streaming placeholder appears -->
      <div v-if="ai.loading && !ai.messages.some(m => m.streaming)" class="msg-row msg-row--ai">
        <div class="msg-bubble msg-bubble--ai msg-thinking">
          <span class="thinking-dot"></span>
          <span class="thinking-dot"></span>
          <span class="thinking-dot"></span>
        </div>
      </div>
    </div>

    <!-- Input bar -->
    <div class="ai-input-bar">
      <div class="input-group">
        <textarea
          ref="inputEl"
          v-model="ai.inputText"
          class="form-control form-control-sm"
          rows="2"
          placeholder="Ask a question or give an instruction… (Enter to send, Shift+Enter for newline)"
          :disabled="ai.loading"
          style="resize: none; font-size: 0.85rem; background: var(--bg-card); border-color: var(--border-color); color: var(--text-main);"
          @keydown="onKey"
        ></textarea>
        <button
          class="btn btn-primary btn-sm px-3"
          :disabled="ai.loading || !ai.inputText.trim()"
          @click="submit"
        >
          <span v-if="ai.loading" class="spinner-border spinner-border-sm" role="status"></span>
          <i v-else class="bi bi-send"></i>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-view {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 2rem);
  max-height: 100%;
}

.ai-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 0.75rem;
  margin-bottom: 0.75rem;
  border-bottom: 1px solid var(--border-color, rgba(255,255,255,0.07));
  flex-shrink: 0;
}

.ai-messages {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding-right: 0.25rem;
  padding-bottom: 0.5rem;
}

.ai-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--text-muted-custom);
  padding: 2rem;
}

.ai-input-bar {
  flex-shrink: 0;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border-color, rgba(255,255,255,0.07));
}

/* Message rows */
.msg-row {
  display: flex;
}
.msg-row--user { justify-content: flex-end; }
.msg-row--ai   { justify-content: flex-start; }

/* Bubbles */
.msg-bubble {
  max-width: 80%;
  padding: 0.55rem 0.85rem;
  border-radius: 12px;
  font-size: 0.84rem;
  line-height: 1.55;
}

.msg-bubble--user {
  background: var(--accent, #6ea8fe);
  color: #0d1117;
  border-bottom-right-radius: 4px;
}

.msg-bubble--ai {
  background: var(--bg-card, #1e1e2e);
  color: var(--text-main, #e0e0e0);
  border: 1px solid var(--border-color, rgba(255,255,255,0.07));
  border-bottom-left-radius: 4px;
}

.msg-bubble--error {
  background: rgba(248,113,113,0.12);
  color: #f87171;
  border: 1px solid rgba(248,113,113,0.25);
  border-radius: 8px;
  font-size: 0.82rem;
  padding: 0.5rem 0.8rem;
}

/* Edit card */
.msg-edit-card {
  max-width: 92%;
  background: var(--bg-card, #1e1e2e);
  border: 1px solid var(--border-color, rgba(255,255,255,0.07));
  border-radius: 10px;
  overflow: hidden;
}

.msg-edit-header {
  display: flex;
  align-items: center;
  padding: 0.6rem 0.85rem;
  border-bottom: 1px solid var(--border-color, rgba(255,255,255,0.07));
  background: rgba(110, 168, 254, 0.05);
}

.msg-edit-summary {
  padding: 0.45rem 0.85rem;
  font-size: 0.78rem;
  color: var(--text-muted-custom);
  border-bottom: 1px solid var(--border-color, rgba(255,255,255,0.07));
}

.msg-edit-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 0.85rem;
  border-top: 1px solid var(--border-color, rgba(255,255,255,0.07));
}

/* Diff viewer */
.diff-viewer {
  max-height: 280px;
  overflow-y: auto;
  font-family: monospace;
  font-size: 0.76rem;
  background: var(--bg-app, #13131f);
}

.diff-line {
  display: block;
  padding: 0 0.75rem;
  white-space: pre;
  line-height: 1.5;
}

.diff-add    { background: rgba(117,183,152,0.13); color: #75b798; }
.diff-remove { background: rgba(248,113,113,0.13); color: #f87171; }
.diff-hunk   { color: #6ea8fe; opacity: 0.75; }
.diff-header { color: var(--text-muted-custom); opacity: 0.6; }
.diff-context{ color: var(--text-muted-custom); }

/* Tool progress pill */
.msg-tool-pill {
  display: inline-flex;
  align-items: center;
  font-size: 0.72rem;
  color: var(--text-muted-custom);
  background: rgba(110,168,254,0.08);
  border: 1px solid rgba(110,168,254,0.18);
  border-radius: 20px;
  padding: 0.2rem 0.6rem;
  margin-bottom: 0.35rem;
}

/* Blinking cursor during streaming */
.msg-cursor {
  display: inline-block;
  opacity: 0.7;
  animation: blink-cursor 0.9s step-end infinite;
  font-size: 0.75em;
  vertical-align: middle;
  margin-left: 2px;
}

@keyframes blink-cursor {
  0%, 100% { opacity: 0.7; }
  50%       { opacity: 0; }
}

/* Thinking dots */
.msg-thinking {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 0.65rem 1rem;
}

.thinking-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-muted-custom);
  animation: blink 1.2s infinite;
}
.thinking-dot:nth-child(2) { animation-delay: 0.2s; }
.thinking-dot:nth-child(3) { animation-delay: 0.4s; }

@keyframes blink {
  0%, 80%, 100% { opacity: 0.2; transform: scale(0.85); }
  40%           { opacity: 1;   transform: scale(1); }
}
</style>
