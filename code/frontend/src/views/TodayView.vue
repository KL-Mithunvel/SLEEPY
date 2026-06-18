<script setup>
import { onMounted } from 'vue'
import { useAuthStore } from '../stores/auth.js'
import { useTodayStore } from '../stores/today.js'

const auth = useAuthStore()
const today = useTodayStore()

const now = new Date()
const greeting = now.getHours() < 12 ? 'Good morning' : now.getHours() < 17 ? 'Good afternoon' : 'Good evening'

function onCaptureKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    today.capture()
  }
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

    <!-- Quick Capture -->
    <div class="card p-3">
      <div class="d-flex align-items-center gap-2 mb-2">
        <i class="bi bi-plus-circle text-accent"></i>
        <span class="fw-semibold" style="font-size: 0.85rem;">Quick Capture</span>
      </div>

      <div class="input-group">
        <input
          v-model="today.captureText"
          type="text"
          class="form-control form-control-sm"
          placeholder="Capture a thought or task… (saved to inbox.md)"
          :disabled="today.capturing"
          @keydown="onCaptureKey"
        />
        <button
          class="btn btn-primary btn-sm"
          :disabled="today.capturing || !today.captureText.trim()"
          @click="today.capture()"
        >
          <span v-if="today.capturing" class="spinner-border spinner-border-sm" role="status"></span>
          <i v-else class="bi bi-send"></i>
        </button>
      </div>

      <!-- Success flash -->
      <div
        v-if="today.captureSuccess"
        class="mt-2 d-flex align-items-center gap-1"
        style="font-size: 0.76rem; color: #75b798;"
      >
        <i class="bi bi-check-circle-fill"></i> Saved to inbox.md
      </div>
      <div v-else style="font-size: 0.72rem; color: var(--text-muted-custom); margin-top: 0.4rem;">
        Press Enter to capture · Writes to inbox.md via the safe edit flow
      </div>
    </div>
  </div>
</template>
