<script setup>
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { useAuthStore } from './stores/auth.js'
import AppSidebar from './components/layout/AppSidebar.vue'
import AppTopbar from './components/layout/AppTopbar.vue'
import LoginView from './views/LoginView.vue'

const auth = useAuthStore()
onMounted(() => auth.init())
</script>

<template>
  <!-- Full-screen spinner while auth initialises -->
  <div v-if="auth.loading" class="d-flex align-items-center justify-content-center vh-100">
    <div class="spinner-border text-accent" role="status">
      <span class="visually-hidden">Loading…</span>
    </div>
  </div>

  <LoginView v-else-if="!auth.authenticated" />

  <div v-else class="app-layout">
    <AppSidebar />
    <div class="app-main">
      <AppTopbar />
      <main class="app-content">
        <RouterView />
      </main>
    </div>
  </div>
</template>
