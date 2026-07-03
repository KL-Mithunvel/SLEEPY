<script setup>
import { computed } from 'vue'

const props = defineProps({
  node: { type: Object, required: true },   // { name, path, isFile, expanded?, children?, project? }
  selectedPath: { type: String, default: null },
  depth: { type: Number, default: 0 },
})
const emit = defineEmits(['select'])

// Ancestor folders of the selected file get a subtle highlight so the open
// path reads clearly instead of the tree looking like a wall of identical rows.
const isAncestor = computed(() =>
  !props.node.isFile &&
  !!props.selectedPath &&
  props.selectedPath.startsWith(props.node.path + '/')
)
const isEmpty = computed(() => !props.node.isFile && props.node.children.length === 0)
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
      :style="{ paddingLeft: (depth * 14 + 8) + 'px' }"
      @click="node.isFile ? emit('select', node.path) : (node.expanded = !node.expanded)"
    >
      <i v-if="!node.isFile" class="bi" :class="node.expanded ? 'bi-folder2-open' : 'bi-folder2'"></i>
      <i v-else class="bi bi-file-earmark-text"></i>
      <span class="tree-label">{{ node.name }}</span>
    </div>
    <div v-if="!node.isFile && node.expanded">
      <FileTreeNode
        v-for="child in node.children"
        :key="child.path"
        :node="child"
        :selected-path="selectedPath"
        :depth="depth + 1"
        @select="(p) => emit('select', p)"
      />
    </div>
  </div>
</template>

<style scoped>
.tree-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.2rem 0.5rem;
  font-size: 0.8rem;
  cursor: pointer;
  border-radius: 4px;
  color: var(--text-muted-custom);
  white-space: nowrap;
}
.tree-row:hover { background: rgba(255,255,255,0.05); }
.tree-row--selected { background: rgba(110,168,254,0.15); color: var(--text-main, #e0e0e0); }
.tree-row--ancestor { color: var(--text-main, #e0e0e0); font-weight: 500; }
.tree-row--empty { opacity: 0.45; }
.tree-row i { flex-shrink: 0; font-size: 0.78rem; }
.tree-row--ancestor > i { color: var(--accent, #6ea8fe); }
.tree-label { overflow: hidden; text-overflow: ellipsis; }
</style>
