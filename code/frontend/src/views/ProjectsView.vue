<script setup>
import { onMounted } from 'vue'
import { useProjectsStore } from '../stores/projects.js'

const store = useProjectsStore()

const STATUS_STYLES = {
  'Active':     { bg: 'rgba(25,135,84,0.15)',  color: '#75b798' },
  'On Hold':    { bg: 'rgba(255,193,7,0.15)',   color: '#ffc107' },
  'Completed':  { bg: 'rgba(13,110,253,0.15)',  color: '#6ea8fe' },
  'Archived':   { bg: 'rgba(108,117,125,0.15)', color: '#adb5bd' },
}

function statusStyle(status) {
  return STATUS_STYLES[status] || { bg: 'rgba(108,117,125,0.1)', color: '#6c757d' }
}

onMounted(() => store.fetchProjects())
</script>

<template>
  <div>
    <div class="d-flex align-items-center justify-content-between mb-1">
      <h5 class="mb-0 fw-semibold">Projects</h5>
      <button
        class="btn btn-sm btn-outline-secondary"
        style="font-size: 0.78rem;"
        :disabled="store.loading"
        @click="store.fetchProjects()"
      >
        <i class="bi bi-arrow-clockwise me-1"></i>Refresh
      </button>
    </div>
    <p class="mb-3" style="color: var(--text-muted-custom); font-size: 0.85rem;">
      Every file in your corpus — projects, research notes, and everything else — grouped by folder.
    </p>

    <!-- Filter -->
    <div class="mb-3">
      <input
        v-model="store.filter"
        type="search"
        class="form-control form-control-sm"
        placeholder="Filter by name, OU, or status…"
        style="max-width: 320px;"
      />
    </div>

    <!-- Error -->
    <div v-if="store.error" class="alert alert-danger py-2 mb-3" style="font-size: 0.82rem;">
      <i class="bi bi-exclamation-triangle me-1"></i>{{ store.error }}
    </div>

    <!-- Loading -->
    <div v-if="store.loading">
      <div v-for="i in 3" :key="i" class="mb-3">
        <div class="placeholder-glow mb-2">
          <span class="placeholder col-3 d-block" style="height: 0.7rem; border-radius: 4px;"></span>
        </div>
        <div v-for="j in 2" :key="j" class="card p-3 mb-2 placeholder-glow">
          <span class="placeholder col-5 d-block mb-1" style="height: 0.85rem; border-radius: 4px;"></span>
          <span class="placeholder col-8 d-block" style="height: 0.72rem; border-radius: 4px;"></span>
        </div>
      </div>
    </div>

    <!-- Empty -->
    <div v-else-if="!store.loading && store.projects.length === 0" class="card p-4 text-center" style="color: var(--text-muted-custom);">
      <i class="bi bi-folder2-open d-block mb-2" style="font-size: 2.5rem; opacity: 0.35;"></i>
      <div style="font-size: 0.85rem;">No files found in your corpus.</div>
      <div style="font-size: 0.75rem; margin-top: 0.3rem; opacity: 0.6;">
        Create <code style="font-size: 0.73rem;">data/kla/&lt;OU&gt;/project.md</code> (or any other .md file) to populate this view.
      </div>
    </div>

    <!-- No filter results -->
    <div v-else-if="store.projects.length > 0 && store.ouList.length === 0" class="card p-3 text-center" style="color: var(--text-muted-custom); font-size: 0.84rem;">
      <i class="bi bi-search me-1"></i> No files match "{{ store.filter }}"
    </div>

    <!-- Projects grouped by OU -->
    <div v-else>
      <div v-for="ou in store.ouList" :key="ou" class="mb-4">
        <!-- OU header -->
        <div class="d-flex align-items-center gap-2 mb-2">
          <i class="bi bi-building" style="font-size: 0.8rem; color: var(--text-muted-custom);"></i>
          <span class="fw-semibold" style="font-size: 0.78rem; color: var(--text-muted-custom); text-transform: uppercase; letter-spacing: 0.05em;">
            {{ ou }}
          </span>
          <span style="font-size: 0.72rem; color: var(--text-muted-custom); opacity: 0.6;">
            {{ store.byOu[ou].length }} file{{ store.byOu[ou].length !== 1 ? 's' : '' }}
          </span>
        </div>

        <!-- Project cards -->
        <div
          v-for="project in store.byOu[ou]"
          :key="project.rel_path"
          class="card mb-2"
          style="cursor: pointer;"
          @click="store.selectProject(project.rel_path)"
        >
          <div class="p-3">
            <div class="d-flex align-items-start justify-content-between gap-2">
              <div class="d-flex align-items-center gap-2 flex-wrap">
                <span class="fw-medium" style="font-size: 0.88rem;">{{ project.name }}</span>
                <span
                  v-if="project.status"
                  class="badge"
                  :style="`background: ${statusStyle(project.status).bg}; color: ${statusStyle(project.status).color}; font-size: 0.7rem; font-weight: 500;`"
                >{{ project.status }}</span>
              </div>
              <div class="d-flex align-items-center gap-2 flex-shrink-0" style="font-size: 0.75rem; color: var(--text-muted-custom);">
                <span v-if="project.open_tasks > 0" class="d-flex align-items-center gap-1">
                  <i class="bi bi-square"></i>{{ project.open_tasks }} open
                </span>
                <span v-if="project.done_tasks > 0" class="d-flex align-items-center gap-1">
                  <i class="bi bi-check-square"></i>{{ project.done_tasks }} done
                </span>
                <i
                  class="bi ms-1"
                  :class="store.selectedPath === project.rel_path ? 'bi-chevron-up' : 'bi-chevron-down'"
                ></i>
              </div>
            </div>

            <div
              v-if="project.snippet"
              class="mt-1"
              style="font-size: 0.78rem; color: var(--text-muted-custom); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"
            >{{ project.snippet }}</div>
          </div>

          <!-- Expanded content -->
          <div
            v-if="store.selectedPath === project.rel_path"
            style="border-top: 1px solid var(--border-color, rgba(255,255,255,0.07));"
          >
            <div v-if="store.loadingContent" class="p-3 placeholder-glow">
              <span v-for="i in 5" :key="i" class="placeholder d-block mb-1"
                :class="`col-${[10, 8, 9, 7, 6][i - 1]}`"
                style="height: 0.75rem; border-radius: 3px;"
              ></span>
            </div>
            <pre
              v-else-if="store.selectedContent"
              class="p-3 mb-0"
              style="font-size: 0.78rem; white-space: pre-wrap; word-break: break-word; font-family: monospace; color: var(--text-main, #e0e0e0); max-height: 420px; overflow-y: auto;"
            >{{ store.selectedContent }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
