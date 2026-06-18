<script setup>
import { onMounted } from 'vue'
import { useLogsStore } from '../stores/logs.js'

const store = useLogsStore()

const FILTERS = [
  { key: 'all',    label: 'All' },
  { key: 'daily',  label: 'Daily' },
  { key: 'weekly', label: 'Weekly' },
]

onMounted(() => store.fetchLogs())
</script>

<template>
  <div>
    <div class="d-flex align-items-center justify-content-between mb-1">
      <h5 class="mb-0 fw-semibold">Logs</h5>
      <button
        class="btn btn-sm btn-outline-secondary"
        style="font-size: 0.78rem;"
        :disabled="store.loading"
        @click="store.fetchLogs()"
      >
        <i class="bi bi-arrow-clockwise me-1"></i>Refresh
      </button>
    </div>
    <p class="mb-3" style="color: var(--text-muted-custom); font-size: 0.85rem;">
      Daily logs and weekly reviews.
    </p>

    <!-- Type filter -->
    <div class="d-flex gap-1 mb-3">
      <button
        v-for="f in FILTERS"
        :key="f.key"
        class="btn btn-sm"
        :class="store.typeFilter === f.key ? 'btn-primary' : 'btn-outline-secondary'"
        style="font-size: 0.78rem;"
        @click="store.typeFilter = f.key; store.selectedPath = null; store.selectedContent = null"
      >{{ f.label }}</button>
    </div>

    <!-- Error -->
    <div v-if="store.error" class="alert alert-danger py-2 mb-3" style="font-size: 0.82rem;">
      <i class="bi bi-exclamation-triangle me-1"></i>{{ store.error }}
    </div>

    <!-- Loading skeletons -->
    <div v-if="store.loading">
      <div v-for="i in 5" :key="i" class="card p-3 mb-2 placeholder-glow">
        <span class="placeholder col-4 d-block mb-1" style="height: 0.85rem; border-radius: 4px;"></span>
        <span class="placeholder col-2 d-block" style="height: 0.7rem; border-radius: 4px;"></span>
      </div>
    </div>

    <!-- Empty: no logs dir -->
    <div v-else-if="store.logs.length === 0" class="card p-4 text-center" style="color: var(--text-muted-custom);">
      <i class="bi bi-journal-text d-block mb-2" style="font-size: 2.5rem; opacity: 0.35;"></i>
      <div style="font-size: 0.85rem;">No log files found.</div>
      <div style="font-size: 0.75rem; margin-top: 0.3rem; opacity: 0.6;">
        Create <code style="font-size: 0.73rem;">data/kla/logs/YYYY-MM-DD.md</code> files to populate this view.
      </div>
    </div>

    <!-- Empty after filter -->
    <div v-else-if="store.filtered.length === 0" class="card p-3 text-center" style="color: var(--text-muted-custom); font-size: 0.84rem;">
      No {{ store.typeFilter }} logs found.
    </div>

    <!-- Log list -->
    <div v-else>
      <div
        v-for="log in store.filtered"
        :key="log.rel_path"
        class="card mb-2"
        style="cursor: pointer;"
        @click="store.selectLog(log.rel_path)"
      >
        <div class="p-3 d-flex align-items-center justify-content-between gap-2">
          <div class="d-flex align-items-center gap-2">
            <i
              class="bi"
              :class="log.type === 'weekly' ? 'bi-calendar-week' : 'bi-calendar-day'"
              style="font-size: 0.9rem; color: var(--accent, #6ea8fe);"
            ></i>
            <div>
              <div class="fw-medium" style="font-size: 0.87rem;">{{ log.date }}</div>
              <div style="font-size: 0.72rem; color: var(--text-muted-custom); text-transform: capitalize;">
                {{ log.type }}
              </div>
            </div>
          </div>
          <i
            class="bi"
            :class="store.selectedPath === log.rel_path ? 'bi-chevron-up' : 'bi-chevron-down'"
            style="font-size: 0.8rem; color: var(--text-muted-custom);"
          ></i>
        </div>

        <!-- Expanded content -->
        <div
          v-if="store.selectedPath === log.rel_path"
          style="border-top: 1px solid var(--border-color, rgba(255,255,255,0.07));"
        >
          <div v-if="store.loadingContent" class="p-3 placeholder-glow">
            <span v-for="i in 6" :key="i" class="placeholder d-block mb-1"
              :class="`col-${[10, 8, 9, 6, 7, 5][i - 1]}`"
              style="height: 0.75rem; border-radius: 3px;"
            ></span>
          </div>
          <pre
            v-else-if="store.selectedContent"
            class="p-3 mb-0"
            style="font-size: 0.78rem; white-space: pre-wrap; word-break: break-word; font-family: monospace; color: var(--text-main, #e0e0e0); max-height: 500px; overflow-y: auto;"
          >{{ store.selectedContent }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>
