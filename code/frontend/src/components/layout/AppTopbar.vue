<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../../stores/auth.js'

const route = useRoute()
const auth = useAuthStore()
const pageTitle = computed(() => route.meta.title || 'SLEEPY')

const now = new Date()
const dateStr = now.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })
</script>

<template>
  <header class="app-topbar">
    <span class="topbar-page-title">{{ pageTitle }}</span>
    <span class="ms-auto topbar-user d-flex align-items-center gap-3" style="font-size: 0.78rem; color: var(--text-muted-custom);">
      {{ dateStr }}
      <span v-if="auth.user" class="d-flex align-items-center gap-2">
        <span>{{ auth.user.name }}</span>
        <button class="btn btn-sm btn-outline-secondary py-0 px-2" title="Sign out" @click="auth.logout()">
          <i class="bi bi-box-arrow-right"></i>
        </button>
      </span>
    </span>
  </header>
</template>
