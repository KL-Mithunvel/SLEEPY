<script setup>
import { computed } from 'vue'

const props = defineProps({
  node: { type: Object, required: true },   // { name, path, isFile, expanded?, children?, project? }
  selectedPath: { type: String, default: null },
  depth: { type: Number, default: 0 },
})
const emit = defineEmits(['select', 'toggle-folder'])

// Ancestor folders of the selected file get a subtle highlight so the open
// path reads clearly instead of the tree looking like a wall of identical rows.
const isAncestor = computed(() =>
  !props.node.isFile &&
  !!props.selectedPath &&
  props.selectedPath.startsWith(props.node.path + '/')
)
const isEmpty = computed(() => !props.node.isFile && props.node.children.length === 0)

// Total files nested anywhere under this folder (not just direct children) —
// so "Recur" with one file inside a further subfolder still shows a count.
function countFiles(node) {
  if (node.isFile) return 1
  return node.children.reduce((sum, c) => sum + countFiles(c), 0)
}
const fileCount = computed(() => !props.node.isFile ? countFiles(props.node) : 0)
</script>

<template>
  <div>
    <div
      class="tree-row"
      :class="{
        'tree-row--selected': node.isFile && node.path === selectedPath,
        'tree-row--ancestor': isAncestor,
        'tree-row--empty': isEmpty,
      }"
      :style="{ paddingLeft: (depth * 16 + 6) + 'px' }"
      @click="node.isFile ? emit('select', node.path) : (isEmpty ? null : emit('toggle-folder', node.path))"
    >
      <i
        v-if="!node.isFile"
        class="bi bi-chevron-right chevron"
        :class="{ 'chevron--open': node.expanded, 'chevron--hidden': isEmpty }"
      ></i>
      <i v-else class="chevron-spacer"></i>
      <i v-if="!node.isFile" class="bi" :class="node.expanded ? 'bi-folder2-open' : 'bi-folder2'"></i>
      <i v-else class="bi bi-file-earmark-text"></i>
      <span class="tree-label">{{ node.name }}</span>
      <span v-if="!node.isFile && !isEmpty" class="tree-count">{{ fileCount }}</span>
    </div>
    <div v-if="!node.isFile && node.expanded" class="tree-children">
      <FileTreeNode
        v-for="child in node.children"
        :key="child.path"
        :node="child"
        :selected-path="selectedPath"
        :depth="depth + 1"
        @select="(p) => emit('select', p)"
        @toggle-folder="(p) => emit('toggle-folder', p)"
      />
    </div>
  </div>
</template>

<style scoped>
.tree-row {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.22rem 0.5rem;
  font-size: 0.8rem;
  cursor: pointer;
  border-radius: 4px;
  color: var(--text-muted-custom);
  white-space: nowrap;
}
.tree-row:hover { background: rgba(255,255,255,0.05); }
.tree-row--selected { background: rgba(110,168,254,0.15); color: var(--text-main, #e0e0e0); }
.tree-row--ancestor { color: var(--text-main, #e0e0e0); font-weight: 500; }
.tree-row--empty { opacity: 0.45; cursor: default; }
.tree-row i { flex-shrink: 0; font-size: 0.78rem; }
.tree-row--ancestor > .bi-folder2, .tree-row--ancestor > .bi-folder2-open { color: var(--accent, #6ea8fe); }
.tree-label { overflow: hidden; text-overflow: ellipsis; }

/* Expand/collapse chevron — the actual "this can open" affordance, separate
   from the folder icon so it's unambiguous which folders have more inside. */
.chevron {
  font-size: 0.6rem !important;
  transition: transform 0.12s ease;
  color: var(--text-muted-custom);
}
.chevron--open { transform: rotate(90deg); }
.chevron--hidden { visibility: hidden; }
.chevron-spacer { width: 0.6rem; flex-shrink: 0; }

/* Nested-file count badge — answers "is it worth opening this" at a glance. */
.tree-count {
  margin-left: auto;
  padding: 0 0.35rem;
  font-size: 0.68rem;
  border-radius: 8px;
  background: rgba(255,255,255,0.06);
  color: var(--text-muted-custom);
  flex-shrink: 0;
}

/* Vertical guide line tracing each nesting level, so depth reads at a glance
   instead of relying purely on padding. */
.tree-children {
  margin-left: 16px;
  border-left: 1px solid rgba(255,255,255,0.08);
}
</style>
