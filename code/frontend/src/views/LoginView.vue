<script setup>
import { ref } from 'vue'
import { useAuthStore } from '../stores/auth.js'

const auth = useAuthStore()

const username = ref('')
const password = ref('')
const submitting = ref(false)

async function submit() {
  if (!username.value.trim() || !password.value || submitting.value) return
  submitting.value = true
  try {
    await auth.login(username.value.trim(), password.value)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="d-flex align-items-center justify-content-center vh-100 px-3">
    <div class="card" style="width: 100%; max-width: 360px; background: var(--surface-card); border-color: var(--border-subtle);">
      <div class="card-body p-4">
        <div class="text-center mb-4">
          <i class="bi bi-moon-stars-fill fs-2 text-accent"></i>
          <h5 class="mt-2 mb-0 fw-semibold">SLEEPY</h5>
          <p class="mb-0" style="font-size: 0.8rem; color: var(--text-muted-custom);">Sign in to continue</p>
        </div>

        <div v-if="auth.error" class="alert alert-danger py-2 mb-3" style="font-size: 0.82rem;">
          <i class="bi bi-exclamation-triangle me-1"></i>{{ auth.error }}
        </div>

        <form @submit.prevent="submit">
          <div class="mb-2">
            <label class="form-label" style="font-size: 0.82rem;">Username</label>
            <input v-model="username" type="text" class="form-control form-control-sm dark-input"
                   autocomplete="username" autofocus>
          </div>
          <div class="mb-3">
            <label class="form-label" style="font-size: 0.82rem;">Password</label>
            <input v-model="password" type="password" class="form-control form-control-sm dark-input"
                   autocomplete="current-password">
          </div>
          <button type="submit" class="btn btn-primary w-100"
                  :disabled="!username.trim() || !password || submitting">
            <span v-if="submitting" class="spinner-border spinner-border-sm me-1"></span>
            Sign in
          </button>
        </form>
      </div>
    </div>
  </div>
</template>
