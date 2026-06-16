<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const pageTitle = computed(() => route.meta.title || 'SLEEPY')

const now = new Date()
const dateStr = now.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })
</script>

<template>
  <header class="app-topbar">
    <span class="topbar-page-title">{{ pageTitle }}</span>
    <span class="ms-auto topbar-user" style="font-size: 0.78rem; color: var(--text-muted-custom);">
      {{ dateStr }}
    </span>
  </header>
</template>
