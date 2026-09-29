<script setup>
// Risk warnings for an AI-proposed change, computed by the backend
// (action_risk.py) from the change itself. The card's summary line is written
// by the AI; these are not, so they're labelled as SLEEPY's own check.
defineProps({
  warnings: { type: Array, default: () => [] },
  compact: { type: Boolean, default: false },
})
</script>

<template>
  <div v-if="warnings.length" :class="['edit-warnings', { 'edit-warnings--compact': compact }]" role="alert">
    <div class="edit-warnings-title">
      <i class="bi bi-exclamation-triangle-fill me-1"></i>Check before applying (SLEEPY's own check, not the AI's)
    </div>
    <ul>
      <li v-for="(w, i) in warnings" :key="i">{{ w }}</li>
    </ul>
  </div>
</template>

<style scoped>
.edit-warnings {
  padding: 0.5rem 0.85rem;
  font-size: 0.76rem;
  color: #fbbf24;
  background: rgba(251, 191, 36, 0.08);
  border-bottom: 1px solid rgba(251, 191, 36, 0.25);
}
.edit-warnings--compact {
  padding: 0.35rem 0.5rem;
  font-size: 0.72rem;
}
.edit-warnings-title {
  font-weight: 600;
  margin-bottom: 0.2rem;
}
.edit-warnings ul {
  margin: 0;
  padding-left: 1.1rem;
}
</style>
