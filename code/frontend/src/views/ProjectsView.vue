<script setup>
import { computed, onMounted } from 'vue'
import { useProjectsStore } from '../stores/projects.js'
import FileTreeNode from '../components/FileTreeNode.vue'

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

// Selected file's project metadata (badges), if it's a known project entry
const selectedMeta = computed(() =>
  store.projects.find(p => p.rel_path === store.selectedPath) || null
)

// Build a folder tree from the (filter-respecting) flat project list.
// Reads store.selectedPath so the tree rebuilds (with fresh default-expand
// state) whenever the selection changes, not just when the file list does.
const tree = computed(() => {
  const filtered = Object.values(store.byOu).flat()
  const sorted = [...filtered].sort((a, b) => a.rel_path.localeCompare(b.rel_path))
  const selected = store.selectedPath

  const root = []
  const folderMap = new Map()

  function getOrCreateFolder(pathParts) {
    const key = pathParts.join('/')
    if (folderMap.has(key)) return folderMap.get(key)
    // Top-level (OU) folders always start open so the domain grouping is
    // visible at a glance; deeper folders only auto-open along the path to
    // whatever file is currently selected, keeping the rest collapsed.
    const isTopLevel = pathParts.length === 1
    const isAncestorOfSelection = !!selected && selected.startsWith(key + '/')
    const node = {
      name: pathParts[pathParts.length - 1], path: key, isFile: false,
      expanded: isTopLevel || isAncestorOfSelection, children: [],
    }
    folderMap.set(key, node)
    if (pathParts.length === 1) {
      root.push(node)
    } else {
      getOrCreateFolder(pathParts.slice(0, -1)).children.push(node)
    }
    return node
  }

  for (const p of sorted) {
    const parts = p.rel_path.split('/')
    const fileNode = { name: parts[parts.length - 1], path: p.rel_path, isFile: true, project: p }
    if (parts.length === 1) {
      root.push(fileNode)
    } else {
      getOrCreateFolder(parts.slice(0, -1)).children.push(fileNode)
    }
  }

  function sortChildren(nodes) {
    nodes.sort((a, b) => (a.isFile !== b.isFile ? (a.isFile ? 1 : -1) : a.name.localeCompare(b.name)))
    for (const n of nodes) if (!n.isFile) sortChildren(n.children)
  }
  sortChildren(root)

  return root
})

function selectFile(path) {
  if (store.isDirty && !confirm('Discard unsaved changes?')) return
  store.selectFile(path)
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
      Every file in your corpus — projects, research notes, and everything else — browse the tree and edit directly.
    </p>

    <div class="mb-3">
      <input
        v-model="store.filter"
        type="search"
        class="form-control form-control-sm"
        placeholder="Filter by name, OU, or status…"
        style="max-width: 320px;"
      />
    </div>

    <div v-if="store.error" class="alert alert-danger py-2 mb-3" style="font-size: 0.82rem;">
      <i class="bi bi-exclamation-triangle me-1"></i>{{ store.error }}
    </div>

    <div v-if="store.loading" class="card p-4 text-center" style="color: var(--text-muted-custom);">
      <div class="spinner-border spinner-border-sm mx-auto"></div>
    </div>

    <div v-else-if="store.projects.length === 0" class="card p-4 text-center" style="color: var(--text-muted-custom);">
      <i class="bi bi-folder2-open d-block mb-2" style="font-size: 2.5rem; opacity: 0.35;"></i>
      <div style="font-size: 0.85rem;">No files found in your corpus.</div>
    </div>

    <!-- Two-pane: tree + editor -->
    <div v-else class="row g-3">
      <!-- Tree pane -->
      <div class="col-12 col-lg-4 col-xl-3">
        <div class="card p-2" style="max-height: 620px; overflow-y: auto;">
          <div v-if="tree.length === 0" class="text-center py-3" style="color: var(--text-muted-custom); font-size: 0.82rem;">
            No files match "{{ store.filter }}"
          </div>
          <FileTreeNode
            v-for="node in tree"
            :key="node.path"
            :node="node"
            :selected-path="store.selectedPath"
            @select="selectFile"
          />
        </div>
      </div>

      <!-- Editor pane -->
      <div class="col-12 col-lg-8 col-xl-9">
        <div v-if="!store.selectedPath" class="card p-5 text-center" style="color: var(--text-muted-custom);">
          <i class="bi bi-file-earmark-text d-block mb-2" style="font-size: 2rem; opacity: 0.3;"></i>
          <div style="font-size: 0.85rem;">Select a file from the tree to view and edit it.</div>
        </div>

        <div v-else class="card p-3">
          <!-- Header -->
          <div class="d-flex align-items-start justify-content-between gap-2 mb-2 flex-wrap">
            <div class="d-flex align-items-center gap-2 flex-wrap">
              <span class="fw-medium" style="font-size: 0.85rem;">{{ store.selectedPath }}</span>
              <span
                v-if="selectedMeta?.status"
                class="badge"
                :style="`background: ${statusStyle(selectedMeta.status).bg}; color: ${statusStyle(selectedMeta.status).color}; font-size: 0.7rem; font-weight: 500;`"
              >{{ selectedMeta.status }}</span>
              <span v-if="selectedMeta?.open_tasks" style="font-size: 0.75rem; color: var(--text-muted-custom);">
                <i class="bi bi-square me-1"></i>{{ selectedMeta.open_tasks }} open
              </span>
              <span v-if="selectedMeta?.done_tasks" style="font-size: 0.75rem; color: var(--text-muted-custom);">
                <i class="bi bi-check-square me-1"></i>{{ selectedMeta.done_tasks }} done
              </span>
            </div>
            <div class="d-flex gap-2 flex-shrink-0">
              <button
                class="btn btn-sm btn-outline-secondary"
                style="font-size: 0.76rem;"
                :disabled="!store.isDirty || store.saving"
                @click="store.discardEdits()"
              >Discard</button>
              <button
                class="btn btn-sm btn-primary"
                style="font-size: 0.76rem;"
                :disabled="!store.isDirty || store.saving"
                @click="store.saveContent()"
              >
                <span v-if="store.saving" class="spinner-border spinner-border-sm me-1"></span>
                Save
              </button>
            </div>
          </div>

          <div v-if="store.saveError" class="alert alert-danger py-2 mb-2" style="font-size: 0.8rem;">
            {{ store.saveError }}
          </div>

          <div v-if="store.loadingContent" class="p-3 placeholder-glow">
            <span v-for="i in 6" :key="i" class="placeholder d-block mb-1"
              :class="`col-${[10, 8, 9, 7, 6, 8][i - 1]}`"
              style="height: 0.8rem; border-radius: 3px;"
            ></span>
          </div>

          <textarea
            v-else
            v-model="store.editedContent"
            class="form-control dark-input"
            spellcheck="false"
            style="font-size: 0.8rem; font-family: monospace; min-height: 480px; resize: vertical; white-space: pre; overflow-wrap: normal; overflow-x: auto;"
          ></textarea>
        </div>
      </div>
    </div>
  </div>
</template>
