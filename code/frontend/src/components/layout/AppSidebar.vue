<script setup>
import { RouterLink, useLink } from 'vue-router'
import { useAuthStore } from '../../stores/auth.js'

const auth = useAuthStore()

const navItems = [
  { to: '/today',    label: 'Today',     icon: 'bi-calendar-check', perm: 'logs:read'    },
  { to: '/projects', label: 'Projects',  icon: 'bi-folder2-open',   perm: 'projects:read' },
  { to: '/logs',     label: 'Logs',      icon: 'bi-journal-text',   perm: 'logs:read'    },
  { to: '/ai',       label: 'Assistant', icon: 'bi-stars',          perm: 'ai:suggest'   },
]

function canSee(perm) {
  if (!auth.user) return false
  return auth.user.permissions.includes(perm)
}
</script>

<template>
  <aside class="app-sidebar">
    <div class="sidebar-brand">
      <div class="brand-name">SLEEPY</div>
      <div class="brand-sub">Even when sleeping,<br>work is tracked.</div>
    </div>

    <nav class="sidebar-nav">
      <ul class="nav flex-column">
        <li v-for="item in navItems" :key="item.to" class="nav-item">
          <RouterLink
            v-if="canSee(item.perm)"
            :to="item.to"
            class="nav-link"
            active-class="active"
          >
            <i :class="item.icon"></i>
            <span>{{ item.label }}</span>
          </RouterLink>
        </li>
      </ul>
    </nav>

    <div class="sidebar-footer px-3 py-3" style="border-top: 1px solid var(--border-subtle);">
      <div class="d-flex align-items-center gap-2">
        <i class="bi bi-person-circle" style="font-size: 1.1rem; color: var(--text-muted-custom);"></i>
        <div style="font-size: 0.75rem; color: var(--text-muted-custom); line-height: 1.3; overflow: hidden;">
          <div class="text-truncate fw-medium" style="color: #ced4da;">{{ auth.user?.name }}</div>
          <div class="text-truncate">{{ auth.user?.email }}</div>
        </div>
      </div>
    </div>
  </aside>
</template>
