<script setup>
import { ref, computed, onMounted, watch } from 'vue'
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

// -----------------------------------------------------------------------
// Alerts tab — operational alerts raised by alerts.py
// -----------------------------------------------------------------------

const alertsData = ref(null)
const alertsOffset = ref(0)
const alertsLoading = ref(false)
const alertsError = ref('')
const alertsForbidden = ref(false)

// alerts.notify() records three outcomes and the difference matters:
// emailed=1 means an email task was queued; suppressed=1 means it was inside
// the cooldown window (or alerting is off) and was deliberately not sent; and
// neither flag set means it was raised with nowhere to go — no recipient is
// configured, so nothing was queued and nothing will ever arrive.
function alertStatus(a) {
  if (a.emailed) return { label: 'Queued', cls: 'bg-success' }
  if (a.suppressed) return { label: 'Throttled', cls: 'bg-secondary' }
  return { label: 'Not delivered', cls: 'bg-warning text-dark' }
}

async function loadAlerts() {
  alertsLoading.value = true
  alertsError.value = ''
  alertsForbidden.value = false
  try {
    alertsData.value = await apiGet(`/api/admin/alerts?limit=${PAGE_SIZE}&offset=${alertsOffset.value}`)
  } catch (e) {
    if (e.status === 403) alertsForbidden.value = true
    else alertsError.value = e.message || 'Failed to load alerts'
  } finally {
    alertsLoading.value = false
  }
}

function alertsNextPage() {
  if (!alertsData.value || alertsOffset.value + PAGE_SIZE >= alertsData.value.total) return
  alertsOffset.value += PAGE_SIZE
  loadAlerts()
}

function alertsPrevPage() {
  alertsOffset.value = Math.max(0, alertsOffset.value - PAGE_SIZE)
  loadAlerts()
}

// -----------------------------------------------------------------------
// Server tab — host usage and space
// -----------------------------------------------------------------------

const server = ref(null)
const serverLoading = ref(false)
const serverError = ref('')
const serverForbidden = ref(false)

async function loadServer() {
  serverLoading.value = true
  serverError.value = ''
  serverForbidden.value = false
  try {
    server.value = await apiGet('/api/admin/system?days=14')
  } catch (e) {
    if (e.status === 403) serverForbidden.value = true
    else serverError.value = e.message || 'Failed to load server stats'
  } finally {
    serverLoading.value = false
  }
}

// null means "this process could not measure it" (the Chroma volume belongs
// to another container in prod, /proc is absent on Windows dev). That is a
// different thing from zero and is shown as an em dash, never as 0.
function fmtBytes(n) {
  if (n === null || n === undefined) return '—'
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`
}

function fmtUptime(seconds) {
  if (!seconds) return '—'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

// Green/amber/red against the same threshold the self-check alerts on, so the
// bar and the alert can never disagree about what counts as a problem.
function usageClass(pct, alertAt) {
  if (pct === null || pct === undefined) return 'bg-secondary'
  const limit = alertAt || 85
  if (pct >= limit) return 'bg-danger'
  if (pct >= limit - 15) return 'bg-warning'
  return 'bg-success'
}

const diskPct = computed(() => server.value?.current?.disk?.used_pct ?? null)

// The areas this process CAN see, for the breakdown bar. Docker's share is
// deliberately not invented here — it comes from the deploy snapshot or not
// at all.
const areaRows = computed(() => {
  const a = server.value?.current?.areas
  if (!a) return []
  return [
    { label: 'Documents (MD corpus)', bytes: a.corpus_bytes },
    { label: 'Database', bytes: a.db_bytes },
    { label: 'Backups', bytes: a.backups_bytes },
    { label: 'Vector index', bytes: a.chroma_bytes },
  ]
})

// Scale the sparkline to the range actually present, not 0–100, or a climb
// from 65% to 76% looks like a flat line.
const trendBars = computed(() => {
  const t = server.value?.trend || []
  if (!t.length) return []
  const vals = t.map(d => d.max_pct).filter(v => v !== null)
  if (!vals.length) return []
  const lo = Math.min(...vals)
  const hi = Math.max(...vals)
  const span = Math.max(1, hi - lo)
  return t.map(d => ({
    ...d,
    height: d.max_pct === null ? 0 : 12 + ((d.max_pct - lo) / span) * 88,
  }))
})

// -----------------------------------------------------------------------
// Shared tab chrome
// -----------------------------------------------------------------------

const busy = computed(() => {
  if (tab.value === 'security') return loading.value
  if (tab.value === 'ai-usage') return usageLoading.value
  if (tab.value === 'server') return serverLoading.value
  return alertsLoading.value
})

function refresh() {
  if (tab.value === 'security') load()
  else if (tab.value === 'ai-usage') loadUsage()
  else if (tab.value === 'server') loadServer()
  else loadAlerts()
}

watch(tab, (t) => {
  if (t === 'ai-usage' && usage.value === null) loadUsage()
  if (t === 'alerts' && alertsData.value === null) loadAlerts()
  if (t === 'server' && server.value === null) loadServer()
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
        :disabled="busy"
        @click="refresh"
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
      <button
        class="btn"
        :class="tab === 'alerts' ? 'btn-primary' : 'btn-outline-secondary'"
        style="font-size: 0.78rem;"
        @click="tab = 'alerts'"
      ><i class="bi bi-bell me-1"></i>Alerts</button>
      <button
        class="btn"
        :class="tab === 'server' ? 'btn-primary' : 'btn-outline-secondary'"
        style="font-size: 0.78rem;"
        @click="tab = 'server'"
      ><i class="bi bi-hdd-stack me-1"></i>Server</button>
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
    <template v-else-if="tab === 'ai-usage'">
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

    <!-- ============================== Alerts ============================== -->
    <template v-else-if="tab === 'alerts'">
      <p class="mb-3" style="color: var(--text-muted-custom); font-size: 0.85rem;">
        Every operational alert the system has raised — failed jobs, low disk, self-check
        findings. Recorded here whether or not it was ever delivered.
      </p>

      <div v-if="alertsForbidden" class="card p-4 text-center" style="color: var(--text-muted-custom);">
        <i class="bi bi-shield-lock d-block mb-2" style="font-size: 2.5rem; opacity: 0.35;"></i>
        <div style="font-size: 0.85rem;">You don't have access to this page.</div>
      </div>

      <div v-else-if="alertsError" class="alert alert-danger py-2 mb-3" style="font-size: 0.82rem;">
        <i class="bi bi-exclamation-triangle me-1"></i>{{ alertsError }}
      </div>

      <div v-else-if="alertsLoading && !alertsData">
        <div v-for="i in 3" :key="i" class="card p-3 mb-2 placeholder-glow">
          <span class="placeholder col-6 d-block mb-1" style="height: 0.85rem; border-radius: 4px;"></span>
        </div>
      </div>

      <div v-else-if="alertsData && alertsData.summary.total === 0" class="card p-4 text-center" style="color: var(--text-muted-custom);">
        <i class="bi bi-bell-slash d-block mb-2" style="font-size: 2.5rem; opacity: 0.35;"></i>
        <div style="font-size: 0.85rem;">No alerts raised yet — nothing has gone wrong.</div>
      </div>

      <div v-else-if="alertsData">
        <!-- The failure mode this view exists for: an alert with nowhere to go -->
        <div
          v-if="alertsData.summary.undelivered > 0"
          class="alert alert-warning py-2 mb-3"
          style="font-size: 0.82rem;"
        >
          <i class="bi bi-exclamation-triangle me-1"></i>
          <strong>{{ alertsData.summary.undelivered }}</strong>
          alert(s) were raised but never delivered — no recipient is configured, so nothing
          was queued and nothing will arrive by email. This page is the only place they appear.
        </div>

        <!-- Summary tiles -->
        <div class="row g-2 mb-3">
          <div class="col-6 col-md-3">
            <div class="card p-2 text-center" style="background: var(--surface-card); border-color: var(--border-subtle);">
              <div style="font-size: 1.3rem; font-weight: 600;">{{ formatNum(alertsData.summary.total) }}</div>
              <div style="font-size: 0.7rem; color: var(--text-muted-custom);">Total</div>
            </div>
          </div>
          <div class="col-6 col-md-3">
            <div class="card p-2 text-center" style="background: var(--surface-card); border-color: var(--border-subtle);">
              <div style="font-size: 1.3rem; font-weight: 600;">{{ formatNum(alertsData.summary.last_24h) }}</div>
              <div style="font-size: 0.7rem; color: var(--text-muted-custom);">Last 24h</div>
            </div>
          </div>
          <div class="col-6 col-md-3">
            <div class="card p-2 text-center" style="background: var(--surface-card); border-color: var(--border-subtle);">
              <div
                style="font-size: 1.3rem; font-weight: 600;"
                :class="alertsData.summary.undelivered > 0 ? 'text-warning' : ''"
              >{{ formatNum(alertsData.summary.undelivered) }}</div>
              <div style="font-size: 0.7rem; color: var(--text-muted-custom);">Not delivered</div>
            </div>
          </div>
          <div class="col-6 col-md-3">
            <div class="card p-2 text-center" style="background: var(--surface-card); border-color: var(--border-subtle);">
              <div style="font-size: 1.3rem; font-weight: 600;">{{ formatNum(alertsData.summary.suppressed) }}</div>
              <div style="font-size: 0.7rem; color: var(--text-muted-custom);">Throttled</div>
            </div>
          </div>
        </div>

        <!-- By alert key -->
        <div class="card mb-3" style="background: var(--surface-card); border-color: var(--border-subtle);">
          <div class="card-header py-2" style="font-size: 0.78rem; font-weight: 600; background: transparent; border-color: var(--border-subtle);">By alert</div>
          <div class="table-responsive">
            <table class="table table-sm mb-0" style="font-size: 0.8rem;">
              <thead>
                <tr style="color: var(--text-muted-custom);">
                  <th class="ps-3">Alert</th>
                  <th class="text-end">Times</th>
                  <th class="text-end">Undelivered</th>
                  <th class="text-end pe-3">Last seen</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="k in alertsData.by_key" :key="k.alert_key">
                  <td class="ps-3"><code style="font-size: 0.78rem;">{{ k.alert_key }}</code></td>
                  <td class="text-end">{{ formatNum(k.count) }}</td>
                  <td class="text-end">{{ formatNum(k.undelivered) }}</td>
                  <td class="text-end pe-3">{{ formatTime(k.last_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Recent alerts -->
        <div class="card" style="background: var(--surface-card); border-color: var(--border-subtle);">
          <div class="card-header py-2" style="font-size: 0.78rem; font-weight: 600; background: transparent; border-color: var(--border-subtle);">Recent alerts</div>
          <div class="table-responsive">
            <table class="table table-sm mb-0" style="font-size: 0.82rem;">
              <thead>
                <tr style="color: var(--text-muted-custom);">
                  <th class="ps-3">Time</th>
                  <th>Alert</th>
                  <th>Subject</th>
                  <th class="pe-3">Status</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="a in alertsData.recent" :key="a.id">
                  <td class="ps-3" style="white-space: nowrap;">{{ formatTime(a.created_at) }}</td>
                  <td><code style="font-size: 0.78rem;">{{ a.alert_key }}</code></td>
                  <td class="text-truncate" style="max-width: 380px;" :title="a.body || a.subject">
                    {{ a.subject }}
                  </td>
                  <td class="pe-3">
                    <span class="badge" :class="alertStatus(a).cls">{{ alertStatus(a).label }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="d-flex align-items-center justify-content-between mt-2" style="font-size: 0.78rem; color: var(--text-muted-custom);">
          <span>{{ alertsOffset + 1 }}–{{ Math.min(alertsOffset + PAGE_SIZE, alertsData.total) }} of {{ alertsData.total }}</span>
          <div class="d-flex gap-1">
            <button class="btn btn-sm btn-outline-secondary" :disabled="alertsOffset === 0 || alertsLoading" @click="alertsPrevPage">Previous</button>
            <button class="btn btn-sm btn-outline-secondary" :disabled="alertsOffset + PAGE_SIZE >= alertsData.total || alertsLoading" @click="alertsNextPage">Next</button>
          </div>
        </div>
      </div>
    </template>

    <!-- ============================== Server ============================== -->
    <template v-else-if="tab === 'server'">
      <p class="mb-3" style="color: var(--text-muted-custom); font-size: 0.85rem;">
        How the box is doing and what is using its disk. Sampled every 15 minutes
        by the self-check.
      </p>

      <div v-if="serverForbidden" class="alert alert-warning py-2" style="font-size: 0.85rem;">
        Your account does not have permission to view server stats.
      </div>
      <div v-else-if="serverError" class="alert alert-danger py-2" style="font-size: 0.85rem;">
        {{ serverError }}
      </div>
      <div v-else-if="serverLoading && !server" class="text-center py-4">
        <div class="spinner-border spinner-border-sm" role="status"></div>
      </div>

      <div v-else-if="server">
        <!-- Plain-English reading, first thing on the page -->
        <div class="card mb-3" style="background: var(--card-bg-custom); border-color: var(--border-custom);">
          <div class="card-body py-3">
            <p
              v-for="(line, i) in server.summary"
              :key="i"
              class="mb-1"
              :class="i === 0 ? 'fw-semibold' : ''"
              style="font-size: 0.88rem; line-height: 1.5;"
            >{{ line }}</p>
          </div>
        </div>

        <!-- Disk -->
        <div class="card mb-3" style="background: var(--card-bg-custom); border-color: var(--border-custom);">
          <div class="card-body py-3">
            <div class="d-flex align-items-baseline justify-content-between mb-2">
              <span class="fw-semibold" style="font-size: 0.9rem;">Disk</span>
              <span style="font-size: 0.82rem; color: var(--text-muted-custom);">
                {{ fmtBytes(server.current.disk.used_bytes) }} used of
                {{ fmtBytes(server.current.disk.total_bytes) }} —
                {{ fmtBytes(server.current.disk.free_bytes) }} free
              </span>
            </div>
            <div class="progress" style="height: 1.25rem;">
              <div
                class="progress-bar"
                :class="usageClass(diskPct, server.current.disk.alert_pct)"
                :style="{ width: (diskPct || 0) + '%' }"
              >{{ diskPct === null ? '—' : diskPct + '%' }}</div>
            </div>
            <div class="mt-1" style="font-size: 0.75rem; color: var(--text-muted-custom);">
              Alert threshold {{ server.current.disk.alert_pct }}%
            </div>
          </div>
        </div>

        <div class="row g-3 mb-3">
          <!-- What this app owns -->
          <div class="col-12 col-lg-6">
            <div class="card h-100" style="background: var(--card-bg-custom); border-color: var(--border-custom);">
              <div class="card-body py-3">
                <div class="fw-semibold mb-2" style="font-size: 0.9rem;">This app&apos;s data</div>
                <table class="table table-sm mb-0" style="font-size: 0.82rem;">
                  <tbody>
                    <tr v-for="row in areaRows" :key="row.label">
                      <td class="ps-0" style="border-color: var(--border-custom);">{{ row.label }}</td>
                      <td class="text-end pe-0" style="border-color: var(--border-custom);">{{ fmtBytes(row.bytes) }}</td>
                    </tr>
                  </tbody>
                </table>
                <div class="mt-2" style="font-size: 0.75rem; color: var(--text-muted-custom);">
                  An em dash means this process cannot see that path from inside its container.
                </div>
              </div>
            </div>
          </div>

          <!-- Host -->
          <div class="col-12 col-lg-6">
            <div class="card h-100" style="background: var(--card-bg-custom); border-color: var(--border-custom);">
              <div class="card-body py-3">
                <div class="fw-semibold mb-2" style="font-size: 0.9rem;">Host</div>

                <template v-if="server.current.memory.used_pct !== null">
                  <div class="d-flex justify-content-between" style="font-size: 0.82rem;">
                    <span>Memory</span>
                    <span>
                      {{ fmtBytes(server.current.memory.total_bytes - server.current.memory.available_bytes) }}
                      of {{ fmtBytes(server.current.memory.total_bytes) }}
                    </span>
                  </div>
                  <div class="progress mt-1 mb-3" style="height: 0.5rem;">
                    <div
                      class="progress-bar"
                      :class="usageClass(server.current.memory.used_pct, 90)"
                      :style="{ width: server.current.memory.used_pct + '%' }"
                    ></div>
                  </div>
                </template>

                <table class="table table-sm mb-0" style="font-size: 0.82rem;">
                  <tbody>
                    <tr>
                      <td class="ps-0" style="border-color: var(--border-custom);">Uptime</td>
                      <td class="text-end pe-0" style="border-color: var(--border-custom);">
                        {{ fmtUptime(server.current.uptime_seconds) }}
                      </td>
                    </tr>
                    <tr>
                      <td class="ps-0" style="border-color: var(--border-custom);">Load average</td>
                      <td class="text-end pe-0" style="border-color: var(--border-custom);">
                        {{ server.current.load_average ? server.current.load_average.join('  ') : '—' }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>

        <!-- Trend -->
        <div class="card mb-3" style="background: var(--card-bg-custom); border-color: var(--border-custom);">
          <div class="card-body py-3">
            <div class="fw-semibold mb-1" style="font-size: 0.9rem;">Disk over time</div>
            <div class="mb-2" style="font-size: 0.75rem; color: var(--text-muted-custom);">
              Daily peak. Scaled to the range shown, not 0–100%, so small climbs stay visible.
            </div>

            <div v-if="!trendBars.length" style="font-size: 0.82rem; color: var(--text-muted-custom);">
              No samples recorded yet — the self-check writes one every 15 minutes.
            </div>
            <div v-else class="d-flex align-items-end gap-1" style="height: 90px;">
              <div
                v-for="d in trendBars"
                :key="d.day"
                class="flex-fill rounded-top"
                :class="usageClass(d.max_pct, server.current.disk.alert_pct)"
                :style="{ height: d.height + '%', minWidth: '6px' }"
                :title="d.day + ': peak ' + d.max_pct + '% (' + d.samples + ' sample(s))'"
              ></div>
            </div>
            <div v-if="trendBars.length" class="d-flex justify-content-between mt-1" style="font-size: 0.72rem; color: var(--text-muted-custom);">
              <span>{{ trendBars[0].day }} — {{ trendBars[0].max_pct }}%</span>
              <span>{{ trendBars[trendBars.length - 1].day }} — {{ trendBars[trendBars.length - 1].max_pct }}%</span>
            </div>
          </div>
        </div>

        <!-- Docker, as of last deploy -->
        <div class="card" style="background: var(--card-bg-custom); border-color: var(--border-custom);">
          <div class="card-body py-3">
            <div class="fw-semibold mb-1" style="font-size: 0.9rem;">Docker</div>
            <div v-if="!server.deploy" style="font-size: 0.82rem; color: var(--text-muted-custom);">
              No deploy snapshot yet. The backend runs without a Docker socket by
              design, so this is captured by <code>tooling/deploy-prod.sh</code> on
              the host and will appear after the next deploy.
            </div>
            <template v-else>
              <div class="mb-2" style="font-size: 0.75rem; color: var(--text-muted-custom);">
                As of the deploy at {{ server.deploy.captured_at }}
                <template v-if="server.deploy.commit">(<code>{{ server.deploy.commit }}</code>)</template>
                — a point-in-time reading, not live.
              </div>
              <table class="table table-sm mb-0" style="font-size: 0.82rem;">
                <tbody>
                  <tr>
                    <td class="ps-0" style="border-color: var(--border-custom);">Images</td>
                    <td class="text-end pe-0" style="border-color: var(--border-custom);">{{ fmtBytes(server.deploy.images_bytes) }}</td>
                  </tr>
                  <tr>
                    <td class="ps-0" style="border-color: var(--border-custom);">
                      Build cache
                      <span class="text-warning" v-if="server.deploy.build_cache_bytes > 2147483648">
                        <i class="bi bi-exclamation-triangle-fill ms-1"></i>
                      </span>
                    </td>
                    <td class="text-end pe-0" style="border-color: var(--border-custom);">{{ fmtBytes(server.deploy.build_cache_bytes) }}</td>
                  </tr>
                  <tr>
                    <td class="ps-0" style="border-color: var(--border-custom);">Containers</td>
                    <td class="text-end pe-0" style="border-color: var(--border-custom);">{{ fmtBytes(server.deploy.containers_bytes) }}</td>
                  </tr>
                  <tr>
                    <td class="ps-0" style="border-color: var(--border-custom);">Volumes</td>
                    <td class="text-end pe-0" style="border-color: var(--border-custom);">{{ fmtBytes(server.deploy.volumes_bytes) }}</td>
                  </tr>
                </tbody>
              </table>
            </template>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
