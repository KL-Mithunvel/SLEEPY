<script setup>
import { ref, computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { useAuthStore } from '../stores/auth.js'
import { useTodayStore } from '../stores/today.js'
import { useAiStore } from '../stores/ai.js'

const auth = useAuthStore()
const today = useTodayStore()
const ai = useAiStore()

const now = new Date()
const greeting = now.getHours() < 12 ? 'Good morning' : now.getHours() < 17 ? 'Good afternoon' : 'Good evening'

// Last message pair for the mini chat display
const lastUserMsg = computed(() => {
  const u = [...ai.messages].reverse().find(m => m.role === 'user')
  return u || null
})
const lastAiMsg = computed(() => {
  const msgs = ai.messages
  if (!msgs.length) return null
  const last = msgs[msgs.length - 1]
  return last.role === 'assistant' ? last : null
})

function onAiKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendToAi()
  }
}

async function sendToAi() {
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

function truncate(text, max = 180) {
  if (!text) return ''
  const flat = text.replace(/\n+/g, ' ').trim()
  return flat.length > max ? flat.slice(0, max) + '…' : flat
}

onMounted(() => today.fetchToday())
</script>

<template>
  <div>
    <!-- Header -->
    <h5 class="mb-1 fw-semibold">{{ greeting }}, {{ auth.user?.name?.split(' ')[0] }}</h5>
    <p class="mb-4" style="color: var(--text-muted-custom); font-size: 0.85rem;">
      Here's what needs your attention today.
    </p>

    <!-- Global error -->
    <div v-if="today.error" class="alert alert-danger py-2 mb-3" style="font-size: 0.82rem;">
      <i class="bi bi-exclamation-triangle me-1"></i>{{ today.error }}
    </div>

    <!-- Morning Briefing -->
    <div class="card p-3 mb-3">
      <div class="d-flex align-items-center justify-content-between mb-2">
        <div class="d-flex align-items-center gap-2">
          <i class="bi bi-stars text-accent"></i>
          <span class="fw-semibold" style="font-size: 0.85rem;">Morning Briefing</span>
          <span v-if="today.briefingAt" style="font-size: 0.72rem; color: var(--text-muted-custom);">
            {{ today.briefingAt }}
          </span>
        </div>
        <button
          class="btn btn-sm"
          :class="today.briefing ? 'btn-outline-secondary' : 'btn-outline-primary'"
          :disabled="today.loadingBriefing || today.loadingToday"
          style="font-size: 0.78rem;"
          @click="today.generateBriefing()"
        >
          <span v-if="today.loadingBriefing" class="spinner-border spinner-border-sm me-1" role="status"></span>
          <i v-else class="bi bi-lightning-charge me-1"></i>
          {{ today.briefing ? 'Regenerate' : 'Generate Briefing' }}
        </button>
      </div>

      <!-- Loading skeleton -->
      <div v-if="today.loadingToday" class="py-2">
        <div class="placeholder-glow">
          <span class="placeholder col-10 mb-1 d-block" style="height: 0.8rem; border-radius: 4px;"></span>
          <span class="placeholder col-8 mb-1 d-block" style="height: 0.8rem; border-radius: 4px;"></span>
          <span class="placeholder col-6 d-block" style="height: 0.8rem; border-radius: 4px;"></span>
        </div>
      </div>

      <!-- Briefing text -->
      <div
        v-else-if="today.briefing"
        style="font-size: 0.84rem; white-space: pre-wrap; line-height: 1.6; color: var(--text-main, #e0e0e0);"
      >{{ today.briefing }}</div>

      <!-- Empty state -->
      <div v-else class="text-center py-3" style="color: var(--text-muted-custom); font-size: 0.84rem;">
        <i class="bi bi-moon-stars d-block mb-2" style="font-size: 1.8rem; opacity: 0.35;"></i>
        No briefing yet — click Generate to ask the AI what to focus on.
      </div>
    </div>

    <!-- Today's Tasks -->
    <div class="card p-3 mb-3">
      <div class="d-flex align-items-center justify-content-between mb-2">
        <div class="d-flex align-items-center gap-2">
          <i class="bi bi-list-check text-accent"></i>
          <span class="fw-semibold" style="font-size: 0.85rem;">Active Tasks</span>
          <span
            v-if="!today.loadingToday && today.tasks.length"
            class="badge rounded-pill"
            style="background: var(--accent, #6ea8fe20); color: var(--accent, #6ea8fe); font-size: 0.7rem;"
          >{{ today.tasks.length }}</span>
        </div>
        <button
          class="btn btn-sm btn-outline-secondary"
          style="font-size: 0.78rem;"
          :disabled="today.loadingToday"
          @click="today.fetchToday()"
        >
          <i class="bi bi-arrow-clockwise me-1"></i>Refresh
        </button>
      </div>

      <!-- Loading skeleton -->
      <div v-if="today.loadingToday">
        <div v-for="i in 3" :key="i" class="placeholder-glow mb-2 p-2 rounded" style="background: var(--bg-card, #1e1e2e);">
          <span class="placeholder col-4 mb-1 d-block" style="height: 0.7rem; border-radius: 3px;"></span>
          <span class="placeholder col-9 d-block" style="height: 0.75rem; border-radius: 3px;"></span>
        </div>
      </div>

      <!-- Task chunks -->
      <div v-else-if="today.tasks.length">
        <div
          v-for="(task, i) in today.tasks"
          :key="i"
          class="mb-2 p-2 rounded"
          style="background: var(--bg-app, #13131f); border: 1px solid var(--border-color, rgba(255,255,255,0.07));"
        >
          <div class="d-flex align-items-center gap-1 mb-1" style="font-size: 0.72rem; color: var(--text-muted-custom);">
            <i class="bi bi-file-earmark-text"></i>
            <span>{{ task.file_path }}</span>
            <span v-if="task.heading" class="ms-1">
              <i class="bi bi-chevron-right" style="font-size: 0.6rem;"></i>
              {{ task.heading }}
            </span>
            <span class="ms-auto" style="opacity: 0.5;">{{ Math.round(task.score * 100) }}%</span>
          </div>
          <div style="font-size: 0.8rem; font-family: monospace; color: var(--text-main, #e0e0e0);">
            {{ truncate(task.content) }}
          </div>
        </div>
      </div>

      <!-- Empty state -->
      <div v-else class="text-center py-3" style="color: var(--text-muted-custom); font-size: 0.84rem;">
        <i class="bi bi-inbox d-block mb-2" style="font-size: 1.8rem; opacity: 0.35;"></i>
        No tasks found in the corpus.<br>
        <span style="font-size: 0.76rem;">Run a corpus reindex to surface your active tasks.</span>
      </div>
    </div>

    <!-- AI Assistant (mini chat) -->
    <div class="card p-3">
      <div class="d-flex align-items-center gap-2 mb-2">
        <i class="bi bi-stars text-accent"></i>
        <span class="fw-semibold" style="font-size: 0.85rem;">AI Assistant</span>
        <RouterLink
          to="/ai"
          style="font-size: 0.72rem; margin-left: auto; color: var(--accent, #6ea8fe); text-decoration: none;"
        >Full chat →</RouterLink>
      </div>

      <!-- Last AI response -->
      <div v-if="lastAiMsg" class="mb-2">
        <!-- Answer -->
        <div
          v-if="lastAiMsg.type === 'answer'"
          class="p-2 rounded"
          style="background: var(--bg-app, #13131f); border: 1px solid var(--border-color, rgba(255,255,255,0.07)); font-size: 0.81rem; color: var(--text-main, #e0e0e0); white-space: pre-wrap; max-height: 120px; overflow-y: auto;"
        >{{ lastAiMsg.content }}</div>

        <!-- Edit proposal -->
        <div
          v-else-if="lastAiMsg.type === 'edit'"
          class="rounded"
          style="background: var(--bg-app, #13131f); border: 1px solid var(--border-color, rgba(255,255,255,0.07)); overflow: hidden; font-size: 0.8rem;"
        >
          <div class="px-2 py-1" style="border-bottom: 1px solid var(--border-color); background: rgba(110,168,254,0.05);">
            <template v-if="lastAiMsg.settled && lastAiMsg.confirmed">
              <i class="bi bi-check-circle-fill text-success me-1"></i>
              Applied — <code style="font-size: 0.75rem;">{{ lastAiMsg.edit.rel_path }}</code>
            </template>
            <template v-else-if="lastAiMsg.settled">
              <i class="bi bi-x-circle me-1" style="color: #f87171;"></i> Discarded
            </template>
            <template v-else>
              <i class="bi bi-pencil-square me-1" style="color: var(--accent);"></i>
              Edit <code style="font-size: 0.75rem;">{{ lastAiMsg.edit.rel_path }}</code>
              — {{ lastAiMsg.edit.summary }}
            </template>
          </div>

          <!-- Compact diff (collapsed, scrollable) -->
          <div
            v-if="!lastAiMsg.settled"
            style="font-family: monospace; font-size: 0.72rem; max-height: 100px; overflow-y: auto; background: var(--bg-app);"
          >
            <div
              v-for="(line, li) in lastAiMsg.edit.diff.split('\n').slice(0, 30)"
              :key="li"
              :class="['diff-line-sm', diffLineClass(line)]"
            >{{ line }}</div>
          </div>

          <!-- Apply / Discard -->
          <div v-if="!lastAiMsg.settled" class="d-flex gap-2 px-2 py-1" style="border-top: 1px solid var(--border-color);">
            <button class="btn btn-success btn-sm py-0" style="font-size: 0.74rem;" @click="ai.confirmEdit(ai.messages.length - 1)">
              <i class="bi bi-check-lg me-1"></i>Apply
            </button>
            <button class="btn btn-outline-secondary btn-sm py-0" style="font-size: 0.74rem;" @click="ai.discardEdit(ai.messages.length - 1)">
              <i class="bi bi-x-lg me-1"></i>Discard
            </button>
            <RouterLink to="/ai" style="font-size: 0.72rem; margin-left: auto; color: var(--accent); align-self: center; text-decoration: none;">
              View full diff →
            </RouterLink>
          </div>
        </div>

        <!-- Error -->
        <div
          v-else-if="lastAiMsg.type === 'error'"
          class="p-2 rounded"
          style="background: rgba(248,113,113,0.1); color: #f87171; font-size: 0.8rem; border: 1px solid rgba(248,113,113,0.2);"
        >
          <i class="bi bi-exclamation-triangle me-1"></i>{{ lastAiMsg.content }}
        </div>
      </div>

      <!-- Input -->
      <div class="input-group">
        <input
          v-model="ai.inputText"
          type="text"
          class="form-control form-control-sm"
          placeholder="Ask anything or give an instruction… (Enter to send)"
          :disabled="ai.loading"
          style="background: var(--bg-app); border-color: var(--border-color); color: var(--text-main);"
          @keydown="onAiKey"
        />
        <button
          class="btn btn-primary btn-sm"
          :disabled="ai.loading || !ai.inputText.trim()"
          @click="sendToAi"
        >
          <span v-if="ai.loading" class="spinner-border spinner-border-sm" role="status"></span>
          <i v-else class="bi bi-send"></i>
        </button>
      </div>
      <div style="font-size: 0.71rem; color: var(--text-muted-custom); margin-top: 0.35rem;">
        AI decides what to do — updates project files, answers questions, or captures to inbox.
      </div>
    </div>
  </div>
</template>

<style scoped>
.diff-line-sm {
  display: block;
  padding: 0 0.5rem;
  white-space: pre;
  line-height: 1.4;
}
.diff-add    { background: rgba(117,183,152,0.13); color: #75b798; }
.diff-remove { background: rgba(248,113,113,0.13); color: #f87171; }
.diff-hunk   { color: #6ea8fe; opacity: 0.75; }
.diff-header { color: var(--text-muted-custom); opacity: 0.5; }
.diff-context{ color: var(--text-muted-custom); }
</style>
