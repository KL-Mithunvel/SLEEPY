<script setup>
import { ref, onMounted, watch } from 'vue'
import { apiGet } from '../api.js'

const PAGE_SIZE = 25

const tab = ref('security')

// -----------------------------------------------------------------------
// Security tab — login events
// -----------------------------------------------------------------------

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

// -----------------------------------------------------------------------
// AI Usage tab
// -----------------------------------------------------------------------

const usage = ref(null)
const usageOffset = ref(0)
const usageLoading = ref(false)
const usageError = ref('')
const usageForbidden = ref(false)

function formatDay(day) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day || '')
  if (!m) return day || ''
  const [, y, mo, d] = m
  return `${d}-${mo}-${y}`
}

function formatNum(n) {
  return (n ?? 0).toLocaleString()
}

async function loadUsage() {
  usageLoading.value = true
  usageError.value = ''
  usageForbidden.value = false
  try {
    usage.value = await apiGet(`/api/admin/ai-usage?limit=${PAGE_SIZE}&offset=${usageOffset.value}`)
  } catch (e) {
    if (e.status === 403) usageForbidden.value = true
    else usageError.value = e.message || 'Failed to load AI usage'
  } finally {
    usageLoading.value = false
  }
}

function usageNextPage() {
  if (!usage.value || usageOffset.value + PAGE_SIZE >= usage.value.total) return
  usageOffset.value += PAGE_SIZE
  loadUsage()
}

function usagePrevPage() {
  usageOffset.value = Math.max(0, usageOffset.value - PAGE_SIZE)
  loadUsage()
}

watch(tab, (t) => {
  if (t === 'ai-usage' && usage.value === null) loadUsage()
})

onMounted(load)
</script>

<template>
  <div>
    <div class="d-flex align-items-center justify-content-between mb-1">
      <h5 class="mb-0 fw-semibold">Admin</h5>
      <button
        class="btn btn-sm btn-outline-secondary"
        style="font-size: 0.78rem;"
        :disabled="tab === 'security' ? loading : usageLoading"
        @click="tab === 'security' ? load() : loadUsage()"
      >
        <i class="bi bi-arrow-clockwise me-1"></i>Refresh
      </button>
    </div>

    <div class="btn-group btn-group-sm mb-3">
      <button
        class="btn"
        :class="tab === 'security' ? 'btn-primary' : 'btn-outline-secondary'"
        style="font-size: 0.78rem;"
        @click="tab = 'security'"
      ><i class="bi bi-shield-lock me-1"></i>Security</button>
      <button
        class="btn"
        :class="tab === 'ai-usage' ? 'btn-primary' : 'btn-outline-secondary'"
        style="font-size: 0.78rem;"
        @click="tab = 'ai-usage'"
      ><i class="bi bi-cpu me-1"></i>AI Usage</button>
    </div>

    <!-- ============================= Security ============================= -->
    <template v-if="tab === 'security'">
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
    </template>

    <!-- ============================= AI Usage ============================= -->
    <template v-else>
      <p class="mb-3" style="color: var(--text-muted-custom); font-size: 0.85rem;">
        Every LLM call the assistant has made — model, tokens, and latency. Corpus-write
        proposals (move/delete/edit) aren't LLM calls themselves and aren't counted here.
      </p>

      <div v-if="usageForbidden" class="card p-4 text-center" style="color: var(--text-muted-custom);">
        <i class="bi bi-shield-lock d-block mb-2" style="font-size: 2.5rem; opacity: 0.35;"></i>
        <div style="font-size: 0.85rem;">You don't have access to this page.</div>
      </div>

      <div v-else-if="usageError" class="alert alert-danger py-2 mb-3" style="font-size: 0.82rem;">
        <i class="bi bi-exclamation-triangle me-1"></i>{{ usageError }}
      </div>

      <div v-else-if="usageLoading && !usage">
        <div v-for="i in 3" :key="i" class="card p-3 mb-2 placeholder-glow">
          <span class="placeholder col-6 d-block mb-1" style="height: 0.85rem; border-radius: 4px;"></span>
        </div>
      </div>

      <div v-else-if="usage && usage.summary.total_calls === 0" class="card p-4 text-center" style="color: var(--text-muted-custom);">
        <i class="bi bi-cpu d-block mb-2" style="font-size: 2.5rem; opacity: 0.35;"></i>
        <div style="font-size: 0.85rem;">No AI calls recorded yet.</div>
      </div>

      <div v-else-if="usage">
        <!-- Summary cards -->
        <div class="row g-2 mb-3">
          <div class="col-6 col-md-3">
            <div class="card p-2 text-center" style="background: var(--surface-card); border-color: var(--border-subtle);">
              <div style="font-size: 1.3rem; font-weight: 600;">{{ formatNum(usage.summary.total_calls) }}</div>
              <div style="font-size: 0.7rem; color: var(--text-muted-custom);">Total calls</div>
            </div>
          </div>
          <div class="col-6 col-md-3">
            <div class="card p-2 text-center" style="background: var(--surface-card); border-color: var(--border-subtle);">
              <div style="font-size: 1.3rem; font-weight: 600;">{{ formatNum(usage.summary.total_input_tokens) }}</div>
              <div style="font-size: 0.7rem; color: var(--text-muted-custom);">Input tokens</div>
            </div>
          </div>
          <div class="col-6 col-md-3">
            <div class="card p-2 text-center" style="background: var(--surface-card); border-color: var(--border-subtle);">
              <div style="font-size: 1.3rem; font-weight: 600;">{{ formatNum(usage.summary.total_output_tokens) }}</div>
              <div style="font-size: 0.7rem; color: var(--text-muted-custom);">Output tokens</div>
            </div>
          </div>
          <div class="col-6 col-md-3">
            <div class="card p-2 text-center" style="background: var(--surface-card); border-color: var(--border-subtle);">
              <div style="font-size: 1.3rem; font-weight: 600;">{{ usage.summary.avg_latency_ms ? Math.round(usage.summary.avg_latency_ms) + 'ms' : '—' }}</div>
              <div style="font-size: 0.7rem; color: var(--text-muted-custom);">Avg latency</div>
            </div>
          </div>
        </div>

        <div class="row g-3 mb-3">
          <!-- By model -->
          <div class="col-md-6">
            <div class="card h-100" style="background: var(--surface-card); border-color: var(--border-subtle);">
              <div class="card-header py-2" style="font-size: 0.78rem; font-weight: 600; background: transparent; border-color: var(--border-subtle);">By model</div>
              <div class="table-responsive">
                <table class="table table-sm mb-0" style="font-size: 0.8rem;">
                  <thead>
                    <tr style="color: var(--text-muted-custom);">
                      <th class="ps-3">Model</th>
                      <th class="text-end">Calls</th>
                      <th class="text-end">In</th>
                      <th class="text-end pe-3">Out</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="m in usage.by_model" :key="m.model">
                      <td class="ps-3">{{ m.model }}</td>
                      <td class="text-end">{{ formatNum(m.calls) }}</td>
                      <td class="text-end">{{ formatNum(m.input_tokens) }}</td>
                      <td class="text-end pe-3">{{ formatNum(m.output_tokens) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <!-- By event type -->
          <div class="col-md-6">
            <div class="card h-100" style="background: var(--surface-card); border-color: var(--border-subtle);">
              <div class="card-header py-2" style="font-size: 0.78rem; font-weight: 600; background: transparent; border-color: var(--border-subtle);">By type</div>
              <div class="table-responsive">
                <table class="table table-sm mb-0" style="font-size: 0.8rem;">
                  <thead>
                    <tr style="color: var(--text-muted-custom);">
                      <th class="ps-3">Type</th>
                      <th class="text-end">Calls</th>
                      <th class="text-end">In</th>
                      <th class="text-end pe-3">Out</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="t in usage.by_type" :key="t.event_type">
                      <td class="ps-3">{{ t.event_type }}</td>
                      <td class="text-end">{{ formatNum(t.calls) }}</td>
                      <td class="text-end">{{ formatNum(t.input_tokens) }}</td>
                      <td class="text-end pe-3">{{ formatNum(t.output_tokens) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>

        <!-- Daily trend -->
        <div v-if="usage.daily.length" class="card mb-3" style="background: var(--surface-card); border-color: var(--border-subtle);">
          <div class="card-header py-2" style="font-size: 0.78rem; font-weight: 600; background: transparent; border-color: var(--border-subtle);">Last 30 days</div>
          <div class="table-responsive" style="max-height: 260px; overflow-y: auto;">
            <table class="table table-sm mb-0" style="font-size: 0.8rem;">
              <thead>
                <tr style="color: var(--text-muted-custom);">
                  <th class="ps-3">Day</th>
                  <th class="text-end">Calls</th>
                  <th class="text-end">In</th>
                  <th class="text-end pe-3">Out</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="d in usage.daily" :key="d.day">
                  <td class="ps-3">{{ formatDay(d.day) }}</td>
                  <td class="text-end">{{ formatNum(d.calls) }}</td>
                  <td class="text-end">{{ formatNum(d.input_tokens) }}</td>
                  <td class="text-end pe-3">{{ formatNum(d.output_tokens) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Recent calls -->
        <div class="card" style="background: var(--surface-card); border-color: var(--border-subtle);">
          <div class="card-header py-2" style="font-size: 0.78rem; font-weight: 600; background: transparent; border-color: var(--border-subtle);">Recent calls</div>
          <div class="table-responsive">
            <table class="table table-sm mb-0" style="font-size: 0.82rem;">
              <thead>
                <tr style="color: var(--text-muted-custom);">
                  <th class="ps-3">Time</th>
                  <th>Type</th>
                  <th>Model</th>
                  <th class="text-end">In</th>
                  <th class="text-end">Out</th>
                  <th class="text-end pe-3">Latency</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="ev in usage.recent" :key="ev.id">
                  <td class="ps-3">{{ formatTime(ev.created_at) }}</td>
                  <td>{{ ev.event_type }}</td>
                  <td>{{ ev.model }}</td>
                  <td class="text-end">{{ formatNum(ev.input_tokens) }}</td>
                  <td class="text-end">{{ formatNum(ev.output_tokens) }}</td>
                  <td class="text-end pe-3">{{ ev.latency_ms ? ev.latency_ms + 'ms' : '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="d-flex align-items-center justify-content-between mt-2" style="font-size: 0.78rem; color: var(--text-muted-custom);">
          <span>{{ usageOffset + 1 }}–{{ Math.min(usageOffset + PAGE_SIZE, usage.total) }} of {{ usage.total }}</span>
          <div class="d-flex gap-1">
            <button class="btn btn-sm btn-outline-secondary" :disabled="usageOffset === 0 || usageLoading" @click="usagePrevPage">Previous</button>
            <button class="btn btn-sm btn-outline-secondary" :disabled="usageOffset + PAGE_SIZE >= usage.total || usageLoading" @click="usageNextPage">Next</button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
