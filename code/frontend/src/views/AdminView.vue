<script setup>
import { ref, onMounted } from 'vue'
import { apiGet } from '../api.js'

const PAGE_SIZE = 25

const events = ref([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const error = ref('')
const forbidden = ref(false)

function formatTime(iso) {
  // SQLite's datetime('now', 'localtime') gives "YYYY-MM-DD HH:MM:SS"
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})/.exec(iso || '')
  if (!m) return iso || ''
  const [, y, mo, d, h, mi] = m
  return `${d}-${mo}-${y} ${h}:${mi}`
}

function location(ev) {
  if (ev.geo_city && ev.geo_country) return `${ev.geo_city}, ${ev.geo_country}`
  if (ev.geo_country) return ev.geo_country
  return '—'
}

async function load() {
  loading.value = true
  error.value = ''
  forbidden.value = false
  try {
    const data = await apiGet(`/api/admin/login-events?limit=${PAGE_SIZE}&offset=${offset.value}`)
    events.value = data.events
    total.value = data.total
  } catch (e) {
    if (e.status === 403) forbidden.value = true
    else error.value = e.message || 'Failed to load login events'
  } finally {
    loading.value = false
  }
}

function nextPage() {
  if (offset.value + PAGE_SIZE >= total.value) return
  offset.value += PAGE_SIZE
  load()
}

function prevPage() {
  offset.value = Math.max(0, offset.value - PAGE_SIZE)
  load()
}

onMounted(load)
</script>

<template>
  <div>
    <div class="d-flex align-items-center justify-content-between mb-1">
      <h5 class="mb-0 fw-semibold">Security</h5>
      <button class="btn btn-sm btn-outline-secondary" style="font-size: 0.78rem;" :disabled="loading" @click="load">
        <i class="bi bi-arrow-clockwise me-1"></i>Refresh
      </button>
    </div>
    <p class="mb-3" style="color: var(--text-muted-custom); font-size: 0.85rem;">
      Every login attempt — who, when, from where, and whether it succeeded.
    </p>

    <div v-if="forbidden" class="card p-4 text-center" style="color: var(--text-muted-custom);">
      <i class="bi bi-shield-lock d-block mb-2" style="font-size: 2.5rem; opacity: 0.35;"></i>
      <div style="font-size: 0.85rem;">You don't have access to this page.</div>
    </div>

    <div v-else-if="error" class="alert alert-danger py-2 mb-3" style="font-size: 0.82rem;">
      <i class="bi bi-exclamation-triangle me-1"></i>{{ error }}
    </div>

    <div v-else-if="loading && events.length === 0">
      <div v-for="i in 5" :key="i" class="card p-3 mb-2 placeholder-glow">
        <span class="placeholder col-6 d-block mb-1" style="height: 0.85rem; border-radius: 4px;"></span>
        <span class="placeholder col-3 d-block" style="height: 0.7rem; border-radius: 4px;"></span>
      </div>
    </div>

    <div v-else-if="events.length === 0" class="card p-4 text-center" style="color: var(--text-muted-custom);">
      <i class="bi bi-journal-text d-block mb-2" style="font-size: 2.5rem; opacity: 0.35;"></i>
      <div style="font-size: 0.85rem;">No login attempts recorded yet.</div>
    </div>

    <div v-else>
      <div class="card" style="background: var(--surface-card); border-color: var(--border-subtle);">
        <div class="table-responsive">
          <table class="table table-sm mb-0" style="font-size: 0.82rem;">
            <thead>
              <tr style="color: var(--text-muted-custom);">
                <th class="ps-3">Time</th>
                <th>Username</th>
                <th>Result</th>
                <th>IP</th>
                <th>Location</th>
                <th class="pe-3">Device</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="ev in events" :key="ev.id">
                <td class="ps-3">{{ formatTime(ev.created_at) }}</td>
                <td>{{ ev.username }}</td>
                <td>
                  <span class="badge" :class="ev.success ? 'bg-success' : 'bg-danger'">
                    {{ ev.success ? 'Success' : 'Failed' }}
                  </span>
                </td>
                <td>{{ ev.ip_address || '—' }}</td>
                <td>{{ location(ev) }}</td>
                <td class="pe-3 text-truncate" style="max-width: 260px;" :title="ev.user_agent">
                  {{ ev.user_agent || '—' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="d-flex align-items-center justify-content-between mt-2" style="font-size: 0.78rem; color: var(--text-muted-custom);">
        <span>{{ offset + 1 }}–{{ Math.min(offset + PAGE_SIZE, total) }} of {{ total }}</span>
        <div class="d-flex gap-1">
          <button class="btn btn-sm btn-outline-secondary" :disabled="offset === 0 || loading" @click="prevPage">Previous</button>
          <button class="btn btn-sm btn-outline-secondary" :disabled="offset + PAGE_SIZE >= total || loading" @click="nextPage">Next</button>
        </div>
      </div>
    </div>
  </div>
</template>
