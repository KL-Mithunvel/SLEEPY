<script setup>
import { useAuthStore } from '../stores/auth.js'
const auth = useAuthStore()

const now = new Date()
const greeting = now.getHours() < 12 ? 'Good morning' : now.getHours() < 17 ? 'Good afternoon' : 'Good evening'
</script>

<template>
  <div>
    <h5 class="mb-1 fw-semibold">{{ greeting }}, {{ auth.user?.name?.split(' ')[0] }}</h5>
    <p class="mb-4" style="color: var(--text-muted-custom); font-size: 0.85rem;">
      Here's what needs your attention today.
    </p>

    <!-- Top tasks placeholder -->
    <div class="row g-3 mb-4">
      <div class="col-12">
        <div class="card p-3">
          <div class="d-flex align-items-center gap-2 mb-3">
            <i class="bi bi-stars text-accent"></i>
            <span class="fw-semibold" style="font-size: 0.85rem;">Today's Focus</span>
          </div>
          <div class="text-center py-4" style="color: var(--text-muted-custom); font-size: 0.85rem;">
            <i class="bi bi-moon-stars d-block mb-2" style="font-size: 2rem; opacity: 0.4;"></i>
            Morning briefing not generated yet.<br>
            <button class="btn btn-sm btn-outline-primary mt-3" disabled>
              <i class="bi bi-lightning-charge me-1"></i>Generate Briefing
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Quick capture -->
    <div class="card p-3">
      <div class="d-flex align-items-center gap-2 mb-2">
        <i class="bi bi-plus-circle text-accent"></i>
        <span class="fw-semibold" style="font-size: 0.85rem;">Quick Capture</span>
      </div>
      <div class="input-group">
        <input
          type="text"
          class="form-control form-control-sm"
          placeholder="Capture a thought or task… (goes to inbox.md)"
          disabled
        />
        <button class="btn btn-primary btn-sm" disabled>
          <i class="bi bi-send"></i>
        </button>
      </div>
      <div style="font-size: 0.72rem; color: var(--text-muted-custom); margin-top: 0.4rem;">
        Coming in Phase 4 — AI layer required.
      </div>
    </div>
  </div>
</template>
