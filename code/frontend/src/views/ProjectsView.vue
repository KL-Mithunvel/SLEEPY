<script setup>
import { computed, reactive, ref, watch, onMounted } from 'vue'
import { useProjectsStore } from '../stores/projects.js'
import FileTreeNode from '../components/FileTreeNode.vue'
import { renderMd } from '../mdRender.js'

const store = useProjectsStore()

// Folder expand/collapse state, keyed by path. Lives here (not on the tree
// nodes themselves) because `tree` below is a computed that rebuilds fresh
// plain objects on every dependency change — mutating a property on one of
// those objects isn't reactive and silently does nothing. This reactive map
// is what FileTreeNode's toggle-folder event actually writes to, and it
// survives tree rebuilds since it's keyed by path, not object identity.
const expandOverrides = reactive({})

function defaultExpanded(path, selected) {
  const isTopLevel = !path.includes('/')
  const isAncestorOfSelection = !!selected && selected.startsWith(path + '/')
  return isTopLevel || isAncestorOfSelection
}

function toggleFolder(path) {
  const current = path in expandOverrides ? expandOverrides[path] : defaultExpanded(path, store.selectedPath)
  expandOverrides[path] = !current
}

const STATUS_STYLES = {
  'active':    { bg: 'rgba(25,135,84,0.15)',  color: '#75b798', label: 'Active' },
  'on_hold':   { bg: 'rgba(255,193,7,0.15)',   color: '#ffc107', label: 'On Hold' },
  'completed': { bg: 'rgba(13,110,253,0.15)',  color: '#6ea8fe', label: 'Completed' },
  'archived':  { bg: 'rgba(108,117,125,0.15)', color: '#adb5bd', label: 'Archived' },
}
const PRIORITY_STYLES = {
  high:   { bg: 'rgba(220,53,69,0.15)',  color: '#ea868f' },
  medium: { bg: 'rgba(255,193,7,0.15)',  color: '#ffc107' },
  low:    { bg: 'rgba(108,117,125,0.15)', color: '#adb5bd' },
}

function statusStyle(status) {
  const key = String(status || '').toLowerCase()
  return STATUS_STYLES[key] || { bg: 'rgba(108,117,125,0.1)', color: '#6c757d', label: status || 'Unknown' }
}

// Selected file's project metadata (badges), if it's a known project entry
const selectedMeta = computed(() =>
  store.projects.find(p => p.rel_path === store.selectedPath) || null
)

// Project files sit at <OU>/<slug>.md or <OU>/Archive/<slug>.md — everything
// else (Daily/Recur/Plans/Govern/People subfolders, root-level files like
// inbox.md) only gets the raw editor, no structured form.
const isProjectFile = computed(() => {
  if (!store.selectedPath) return false
  const parts = store.selectedPath.split('/')
  return parts.length === 2 || (parts.length === 3 && parts[1] === 'Archive')
})

// Build a folder tree from the (filter-respecting) flat project list.
// Reads store.selectedPath and expandOverrides so the tree rebuilds whenever
// selection changes (fresh auto-expand-to-selection) or the user manually
// toggles a folder (expandOverrides write).
const tree = computed(() => {
  const filtered = Object.values(store.byOu).flat()
  const sorted = [...filtered].sort((a, b) => a.rel_path.localeCompare(b.rel_path))
  const selected = store.selectedPath

  const root = []
  const folderMap = new Map()

  function getOrCreateFolder(pathParts) {
    const key = pathParts.join('/')
    if (folderMap.has(key)) return folderMap.get(key)
    const expanded = key in expandOverrides ? expandOverrides[key] : defaultExpanded(key, selected)
    const node = {
      name: pathParts[pathParts.length - 1], path: key, isFile: false,
      expanded, children: [],
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

// --- Structured editor local state -----------------------------------------

const FREEFORM_HEADINGS = ['Goal', 'Why', 'Current State', 'Notes']
const sectionDrafts = reactive({})   // heading -> textarea buffer, only for dirty-tracking
const sectionSaving = reactive({})

watch(() => store.structured, (s) => {
  for (const key of Object.keys(sectionDrafts)) delete sectionDrafts[key]
  if (!s) return
  for (const heading of FREEFORM_HEADINGS) {
    sectionDrafts[heading] = s.sections?.[heading] || ''
  }
}, { immediate: true })

async function saveSectionDraft(heading) {
  sectionSaving[heading] = true
  try {
    await store.saveSection(heading, sectionDrafts[heading])
  } finally {
    sectionSaving[heading] = false
  }
}

const newTask = reactive({ text: '', priority: '', due: '' })
async function submitNewTask() {
  if (!newTask.text.trim()) return
  await store.addTask(newTask.text.trim(), newTask.priority || null, newTask.due || null)
  newTask.text = ''
  newTask.priority = ''
  newTask.due = ''
}

const editingTask = ref(null)   // task.line of the row currently being edited
const taskEditBuffer = reactive({ description: '', priority: '', due: '' })

function startEditTask(task) {
  editingTask.value = task.line
  taskEditBuffer.description = task.description
  taskEditBuffer.priority = task.priority || ''
  taskEditBuffer.due = task.due || ''
}

async function saveEditTask(task) {
  await store.editTask(task, {
    description: taskEditBuffer.description,
    priority: taskEditBuffer.priority || null,
    due: taskEditBuffer.due || null,
  })
  editingTask.value = null
}

const newListItem = reactive({ Decisions: '', 'Open Questions': '' })
async function submitListItem(section) {
  const text = newListItem[section].trim()
  if (!text) return
  await store.addListItem(section, text)
  newListItem[section] = ''
}

async function onStatusChange(e) {
  const prevOu = store.selectedPath?.split('/')[0]
  await store.setStatus(e.target.value)
  const newOu = store.selectedPath?.split('/')[0]
  if (newOu !== prevOu || e.target.value === 'archived') {
    // path may have changed (archive/unarchive move) — tree will re-select via store.selectedPath
  }
}

async function archiveProject() {
  const name = store.selectedPath?.split('/').pop() || 'this project'
  if (!window.confirm(`Archive ${name}? This moves the file to its OU's Archive folder.`)) return
  await store.setStatus('archived')
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
            @toggle-folder="toggleFolder"
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
              >{{ statusStyle(selectedMeta.status).label }}</span>
              <span v-if="selectedMeta?.open_tasks" style="font-size: 0.75rem; color: var(--text-muted-custom);">
                <i class="bi bi-square me-1"></i>{{ selectedMeta.open_tasks }} open
              </span>
              <span v-if="selectedMeta?.done_tasks" style="font-size: 0.75rem; color: var(--text-muted-custom);">
                <i class="bi bi-check-square me-1"></i>{{ selectedMeta.done_tasks }} done
              </span>
            </div>

            <div class="d-flex align-items-center gap-2 flex-shrink-0">
              <!-- Read/Edit/Raw mode toggle — only for project files -->
              <div v-if="isProjectFile" class="btn-group btn-group-sm">
                <button
                  v-for="m in ['read', 'edit', 'raw']" :key="m"
                  class="btn"
                  :class="store.mode === m ? 'btn-primary' : 'btn-outline-secondary'"
                  style="font-size: 0.74rem; text-transform: capitalize;"
                  @click="store.mode = m"
                >{{ m }}</button>
              </div>
              <template v-if="!isProjectFile || store.mode === 'raw'">
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
              </template>
            </div>
          </div>

          <div v-if="store.saveError" class="alert alert-danger py-2 mb-2" style="font-size: 0.8rem;">
            {{ store.saveError }}
          </div>
          <div v-if="store.structuredError" class="alert alert-danger py-2 mb-2" style="font-size: 0.8rem;">
            {{ store.structuredError }}
          </div>

          <div v-if="store.loadingContent" class="p-3 placeholder-glow">
            <span v-for="i in 6" :key="i" class="placeholder d-block mb-1"
              :class="`col-${[10, 8, 9, 7, 6, 8][i - 1]}`"
              style="height: 0.8rem; border-radius: 3px;"
            ></span>
          </div>

          <!-- Raw mode (also the only mode for non-project files) -->
          <textarea
            v-else-if="!isProjectFile || store.mode === 'raw'"
            v-model="store.editedContent"
            class="form-control dark-input"
            spellcheck="false"
            style="font-size: 0.8rem; font-family: monospace; min-height: 480px; resize: vertical; white-space: pre; overflow-wrap: normal; overflow-x: auto;"
          ></textarea>

          <!-- Structured editor: not yet loaded -->
          <div v-else-if="!store.structured" class="p-3 placeholder-glow">
            <span class="placeholder col-6 d-block mb-1" style="height: 0.8rem;"></span>
          </div>

          <!-- READ mode -->
          <div v-else-if="store.mode === 'read'" style="font-size: 0.85rem;">
            <h6 v-if="store.structured.title" class="fw-semibold mb-3">{{ store.structured.title }}</h6>

            <template v-for="heading in ['Goal', 'Why', 'Current State']" :key="heading">
              <div v-if="store.structured.sections[heading]" class="mb-3">
                <div class="text-uppercase fw-semibold mb-1" style="font-size: 0.7rem; color: var(--text-muted-custom); letter-spacing: 0.04em;">{{ heading }}</div>
                <div class="md-rendered" v-html="renderMd(store.structured.sections[heading])"></div>
              </div>
            </template>

            <div v-for="extra in store.structured.extra_sections" :key="extra.heading" class="mb-3">
              <div class="text-uppercase fw-semibold mb-1" style="font-size: 0.7rem; color: var(--text-muted-custom); letter-spacing: 0.04em;">{{ extra.heading }}</div>
              <div class="md-rendered" v-html="renderMd(extra.body)"></div>
            </div>

            <div class="mb-3">
              <div class="text-uppercase fw-semibold mb-1" style="font-size: 0.7rem; color: var(--text-muted-custom); letter-spacing: 0.04em;">Tasks</div>
              <div v-if="store.structured.tasks.length === 0" style="color: var(--text-muted-custom); font-size: 0.8rem;">No tasks.</div>
              <div v-for="task in store.structured.tasks" :key="task.line" class="d-flex align-items-center gap-2 mb-1">
                <i class="bi" :class="task.done ? 'bi-check-square-fill' : 'bi-square'" :style="task.done ? 'color: #75b798;' : 'color: var(--text-muted-custom);'"></i>
                <span :style="task.done ? 'text-decoration: line-through; opacity: 0.6;' : ''">{{ task.description }}</span>
                <span v-if="task.priority" class="badge" :style="`background: ${PRIORITY_STYLES[task.priority]?.bg}; color: ${PRIORITY_STYLES[task.priority]?.color}; font-size: 0.65rem;`">{{ task.priority }}</span>
                <span v-if="task.due" style="font-size: 0.72rem; color: var(--text-muted-custom);"><i class="bi bi-calendar-event me-1"></i>{{ task.due }}</span>
              </div>
            </div>

            <div v-if="store.structured.decisions.length" class="mb-3">
              <div class="text-uppercase fw-semibold mb-1" style="font-size: 0.7rem; color: var(--text-muted-custom); letter-spacing: 0.04em;">Decisions</div>
              <ul class="mb-0 ps-3">
                <li v-for="d in store.structured.decisions" :key="d" style="font-size: 0.83rem;">{{ d }}</li>
              </ul>
            </div>

            <div v-if="store.structured.open_questions.length" class="mb-3">
              <div class="text-uppercase fw-semibold mb-1" style="font-size: 0.7rem; color: var(--text-muted-custom); letter-spacing: 0.04em;">Open Questions</div>
              <ul class="mb-0 ps-3">
                <li v-for="q in store.structured.open_questions" :key="q" style="font-size: 0.83rem;">{{ q }}</li>
              </ul>
            </div>

            <div v-if="store.structured.sections['Notes']" class="mb-2">
              <div class="text-uppercase fw-semibold mb-1" style="font-size: 0.7rem; color: var(--text-muted-custom); letter-spacing: 0.04em;">Notes</div>
              <div class="md-rendered" v-html="renderMd(store.structured.sections['Notes'])"></div>
            </div>
          </div>

          <!-- EDIT mode -->
          <div v-else-if="store.mode === 'edit'" style="font-size: 0.85rem;">
            <!-- Status -->
            <div class="mb-3">
              <label class="form-label" style="font-size: 0.75rem; color: var(--text-muted-custom);">Status</label>
              <div class="d-flex align-items-center gap-2">
                <select
                  class="form-select form-select-sm dark-input"
                  style="max-width: 200px;"
                  :value="store.structured.frontmatter.status"
                  @change="onStatusChange"
                >
                  <option v-for="s in Object.keys(STATUS_STYLES)" :key="s" :value="s">{{ STATUS_STYLES[s].label }}</option>
                </select>
                <button
                  v-if="store.structured.frontmatter.status !== 'archived'"
                  class="btn btn-sm btn-outline-secondary"
                  style="font-size: 0.75rem;"
                  title="Move this project to its OU's Archive folder"
                  @click="archiveProject"
                ><i class="bi bi-archive me-1"></i>Archive</button>
              </div>
            </div>

            <!-- Freeform sections -->
            <div v-for="heading in ['Goal', 'Why', 'Current State', 'Notes']" :key="heading" class="mb-3">
              <label class="form-label d-flex align-items-center justify-content-between" style="font-size: 0.75rem; color: var(--text-muted-custom);">
                {{ heading }}
                <button
                  class="btn btn-xs btn-outline-primary py-0 px-2"
                  style="font-size: 0.68rem;"
                  :disabled="sectionDrafts[heading] === (store.structured.sections[heading] || '') || sectionSaving[heading]"
                  @click="saveSectionDraft(heading)"
                >Save</button>
              </label>
              <textarea
                v-model="sectionDrafts[heading]"
                class="form-control dark-input"
                style="font-size: 0.8rem; min-height: 70px;"
              ></textarea>
            </div>

            <!-- Tasks -->
            <div class="mb-3">
              <div class="fw-semibold mb-2" style="font-size: 0.8rem;">Tasks</div>
              <div v-for="task in store.structured.tasks" :key="task.line" class="d-flex align-items-center gap-2 mb-2">
                <input type="checkbox" class="form-check-input mt-0" :checked="task.done" @change="store.toggleTask(task)" />
                <template v-if="editingTask === task.line">
                  <input v-model="taskEditBuffer.description" class="form-control form-control-sm dark-input" style="font-size: 0.8rem;" />
                  <select v-model="taskEditBuffer.priority" class="form-select form-select-sm dark-input" style="max-width: 110px; font-size: 0.78rem;">
                    <option value="">No priority</option>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                  </select>
                  <input v-model="taskEditBuffer.due" type="date" class="form-control form-control-sm dark-input" style="max-width: 150px; font-size: 0.78rem;" />
                  <button class="btn btn-sm btn-primary" style="font-size: 0.74rem;" @click="saveEditTask(task)">Save</button>
                  <button class="btn btn-sm btn-outline-secondary" style="font-size: 0.74rem;" @click="editingTask = null">Cancel</button>
                </template>
                <template v-else>
                  <span class="flex-grow-1" :style="task.done ? 'text-decoration: line-through; opacity: 0.6;' : ''">{{ task.description }}</span>
                  <span v-if="task.priority" class="badge" :style="`background: ${PRIORITY_STYLES[task.priority]?.bg}; color: ${PRIORITY_STYLES[task.priority]?.color}; font-size: 0.65rem;`">{{ task.priority }}</span>
                  <span v-if="task.due" style="font-size: 0.72rem; color: var(--text-muted-custom);">{{ task.due }}</span>
                  <button class="btn btn-sm btn-outline-secondary py-0 px-2" style="font-size: 0.7rem;" @click="startEditTask(task)"><i class="bi bi-pencil"></i></button>
                  <button class="btn btn-sm btn-outline-danger py-0 px-2" style="font-size: 0.7rem;" @click="store.removeTask(task)"><i class="bi bi-trash"></i></button>
                </template>
              </div>

              <!-- Add task row -->
              <div class="d-flex align-items-center gap-2 mt-2">
                <input v-model="newTask.text" placeholder="New task…" class="form-control form-control-sm dark-input flex-grow-1" style="font-size: 0.8rem;" @keyup.enter="submitNewTask" />
                <select v-model="newTask.priority" class="form-select form-select-sm dark-input" style="max-width: 110px; font-size: 0.78rem;">
                  <option value="">No priority</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </select>
                <input v-model="newTask.due" type="date" class="form-control form-control-sm dark-input" style="max-width: 150px; font-size: 0.78rem;" />
                <button class="btn btn-sm btn-primary" style="font-size: 0.74rem;" @click="submitNewTask"><i class="bi bi-plus-lg"></i> Add</button>
              </div>
            </div>

            <!-- Decisions / Open Questions -->
            <div v-for="section in ['Decisions', 'Open Questions']" :key="section" class="mb-3">
              <div class="fw-semibold mb-2" style="font-size: 0.8rem;">{{ section }}</div>
              <div v-for="item in (section === 'Decisions' ? store.structured.decisions : store.structured.open_questions)" :key="item" class="d-flex align-items-center gap-2 mb-1">
                <span class="flex-grow-1" style="font-size: 0.82rem;">{{ item }}</span>
                <button class="btn btn-sm btn-outline-danger py-0 px-2" style="font-size: 0.7rem;" @click="store.removeListItem(section, item)"><i class="bi bi-trash"></i></button>
              </div>
              <div class="d-flex align-items-center gap-2 mt-1">
                <input v-model="newListItem[section]" :placeholder="`Add to ${section.toLowerCase()}…`" class="form-control form-control-sm dark-input flex-grow-1" style="font-size: 0.8rem;" @keyup.enter="submitListItem(section)" />
                <button class="btn btn-sm btn-outline-primary" style="font-size: 0.74rem;" @click="submitListItem(section)"><i class="bi bi-plus-lg"></i></button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
