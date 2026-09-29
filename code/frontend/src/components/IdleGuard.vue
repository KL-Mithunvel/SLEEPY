<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth.js'

// "Are you still there?" — signs out after `idleTimeoutMinutes` with no
// activity, asking one minute before. Real activity also renews the
// short-lived server token, so an abandoned session is dead server-side too,
// not just hidden by the UI.

const auth = useAuthStore()

const WARN_LEAD_MS = 60 * 1000
const ACTIVITY_EVENTS = ['pointerdown', 'keydown', 'wheel', 'touchstart', 'scroll']

const idleMs = computed(() => auth.idleTimeoutMinutes * 60 * 1000)
const warning = ref(false)
const secondsLeft = ref(0)

let lastActivity = Date.now()
let ticker = null

function onActivity() {
  // While the prompt is up only the button counts — a stray mouse nudge
  // must not silently dismiss it.
  if (warning.value) return
  lastActivity = Date.now()
  auth.refreshIfStale()
}

function stay() {
  lastActivity = Date.now()
  warning.value = false
  auth.refreshToken()
}

function tick() {
  // Wall-clock comparison, not a countdown timer: browsers throttle timers in
  // background tabs and a sleeping laptop pauses them entirely.
  const idle = Date.now() - lastActivity
  if (idle >= idleMs.value) {
    warning.value = false
    auth.logout('idle')
  } else if (idle >= idleMs.value - WARN_LEAD_MS) {
    warning.value = true
    secondsLeft.value = Math.ceil((idleMs.value - idle) / 1000)
  }
}

onMounted(() => {
  lastActivity = Date.now()
  ACTIVITY_EVENTS.forEach((e) => window.addEventListener(e, onActivity, { passive: true, capture: true }))
  ticker = setInterval(tick, 1000)
})

onBeforeUnmount(() => {
  ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, onActivity, { capture: true }))
  clearInterval(ticker)
})
</script>

<template>
  <div v-if="warning" class="idle-backdrop" role="alertdialog" aria-modal="true" aria-labelledby="idle-title">
    <div class="card idle-card">
      <div class="card-body p-4 text-center">
        <i class="bi bi-moon-stars-fill fs-2 text-accent"></i>
        <h5 id="idle-title" class="mt-2 mb-1 fw-semibold">Are you still there?</h5>
        <p class="mb-3" style="font-size: 0.85rem; color: var(--text-muted-custom);">
          Signing you out in {{ secondsLeft }}s for your security.
        </p>
        <button class="btn btn-primary w-100" autofocus @click="stay">Yes, I'm here</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.idle-backdrop {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
  background: rgba(0, 0, 0, 0.65);
}
.idle-card {
  width: 100%;
  max-width: 340px;
  background: var(--surface-card);
  border-color: var(--border-subtle);
}
</style>
