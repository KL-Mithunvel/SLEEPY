<script setup>
import { ref, onMounted } from 'vue'
import { useIntegrationsStore } from '../stores/integrations.js'

const store = useIntegrationsStore()

// Active send form
const activeForm = ref(null)  // 'email'

const emailTo      = ref('')
const emailSubject = ref('')
const emailBody    = ref('')

function openForm(name) {
  activeForm.value = name
  store.clearResult()
}

async function submitEmail() {
  if (!emailTo.value.trim() || !emailSubject.value.trim() || !emailBody.value.trim()) return
  await store.queueEmail(emailTo.value.trim(), emailSubject.value.trim(), emailBody.value.trim())
  if (store.sendResult?.ok) {
    emailTo.value = ''
    emailSubject.value = ''
    emailBody.value = ''
  }
}

onMounted(() => store.fetchStatus())
</script>

<template>
  <div>
    <h5 class="mb-1 fw-semibold">Integrations</h5>
    <p class="mb-4" style="color: var(--text-muted-custom); font-size: 0.85rem;">
      Send messages and sync external services.
    </p>

    <!-- Status error -->
    <div v-if="store.statusError" class="alert alert-danger py-2 mb-3" style="font-size: 0.82rem;">
      <i class="bi bi-exclamation-triangle me-1"></i>{{ store.statusError }}
    </div>

    <!-- Global send result -->
    <div v-if="store.sendResult" class="alert py-2 mb-3"
         :class="store.sendResult.ok ? 'alert-success' : 'alert-danger'"
         style="font-size: 0.82rem;">
      <i :class="store.sendResult.ok ? 'bi-check-circle' : 'bi-exclamation-triangle'" class="bi me-1"></i>
      {{ store.sendResult.ok ? store.sendResult.message : store.sendResult.error }}
    </div>

    <!-- Channel status cards -->
    <div class="row g-3 mb-4">
      <!-- Email -->
      <div class="col-md-6 col-xl-3">
        <div class="card h-100" style="background: var(--surface-card); border-color: var(--border-subtle);">
          <div class="card-body d-flex flex-column gap-2">
            <div class="d-flex align-items-center gap-2">
              <i class="bi bi-envelope-fill fs-4" style="color: #ef5350;"></i>
              <span class="fw-medium">Email</span>
              <span v-if="store.status" class="ms-auto badge"
                    :class="store.status.email ? 'bg-success' : 'bg-secondary'">
                {{ store.status.email ? 'Ready' : 'Not set' }}
              </span>
              <span v-else class="ms-auto badge bg-secondary">…</span>
            </div>
            <p class="mb-0" style="font-size: 0.78rem; color: var(--text-muted-custom);">
              Send via Office 365 / Microsoft Graph API.
            </p>
            <button class="btn btn-sm btn-outline-secondary mt-auto"
                    :disabled="!store.status?.email"
                    @click="openForm('email')">
              Compose email
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Send forms -->
    <div v-if="activeForm" class="card" style="background: var(--surface-card); border-color: var(--border-subtle);">
      <div class="card-body">

        <!-- Email form -->
        <template v-if="activeForm === 'email'">
          <h6 class="mb-3">Compose email</h6>
          <div class="mb-2">
            <label class="form-label" style="font-size: 0.82rem;">To</label>
            <input v-model="emailTo" type="email" class="form-control form-control-sm dark-input"
                   placeholder="recipient@example.com">
          </div>
          <div class="mb-2">
            <label class="form-label" style="font-size: 0.82rem;">Subject</label>
            <input v-model="emailSubject" type="text" class="form-control form-control-sm dark-input"
                   placeholder="Email subject">
          </div>
          <div class="mb-3">
            <label class="form-label" style="font-size: 0.82rem;">Body (plain text)</label>
            <textarea v-model="emailBody" class="form-control form-control-sm dark-input"
                      rows="5" placeholder="Email body..."></textarea>
          </div>
          <div class="d-flex gap-2">
            <button class="btn btn-sm btn-primary"
                    :disabled="!emailTo.trim() || !emailSubject.trim() || !emailBody.trim() || store.sending"
                    @click="submitEmail">
              <span v-if="store.sending" class="spinner-border spinner-border-sm me-1"></span>
              Queue email
            </button>
            <button class="btn btn-sm btn-outline-secondary" @click="activeForm = null">Cancel</button>
          </div>
        </template>

      </div>
    </div>
  </div>
</template>
