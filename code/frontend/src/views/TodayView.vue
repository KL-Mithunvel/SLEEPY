<script setup>
import { ref, computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { useAuthStore } from '../stores/auth.js'
import { useTodayStore } from '../stores/today.js'
import { useAiStore } from '../stores/ai.js'
import { renderMd } from '../mdRender.js'

const auth = useAuthStore()
const today = useTodayStore()
const ai = useAiStore()

const now = new Date()
const greeting = now.getHours() < 12 ? 'Good morning' : now.getHours() < 17 ? 'Good afternoon' : 'Good evening'

const lastUserMsg = computed(() => {
  const u = [...ai.messages].reverse().find(m => m.role === 'user')
  return u || null
})
const lastAiMsg = computed(() => {
  const msgs = ai.messages
  if (!msgs.length) return null
  const last = msgs[msgs.length - 1]
  return last.role === 'assistant' ? last : null
})

function onAiKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendToAi()
  }
}

async function sendToAi() {
  if (!ai.inputText.trim() || ai.loading) return
  await ai.send(ai.inputText)
}

function diffLineClass(line) {
  if (line.startsWith('+++') || line.startsWith('---')) return 'diff-header'
  if (line.startsWith('+')) return 'diff-add'
  if (line.startsWith('-')) return 'diff-remove'
  if (line.startsWith('@@')) return 'diff-hunk'
  return 'diff-context'
}

// News helpers
function newsIcon(feedback) {
  if (feedback === '+1') return '👍'
  if (feedback === '-1') return '👎'
  return null
}

onMounted(() => {
  today.fetchToday()
  today.fetchNews()
})
</script>

<template>
  <div>
    <!-- Header -->
    <h5 class="mb-1 fw-semibold">{{ greeting }}, {{ auth.user?.name?.split(' ')[0] }}</h5>
    <p class="mb-4" style="color: var(--text-muted-custom); font-size: 0.85rem;">
      Here's what needs your attention today.
    </p>

    <!-- Global error -->
    <div v-if="today.error" class="alert alert-danger py-2 mb-3" style="font-size: 0.82rem;">
      <i class="bi bi-exclamation-triangle me-1"></i>{{ today.error }}
    </div>

    <!-- TOP ROW: Morning Briefing + News Feed side-by-side -->
    <div class="row g-3 mb-3">

      <!-- Morning Briefing -->
      <div class="col-12 col-xl-7">
        <div class="card p-3 d-flex flex-column" style="height: 480px;">
          <div class="d-flex align-items-center justify-content-between mb-2" style="flex-shrink: 0;">
            <div class="d-flex align-items-center gap-2">
              <i class="bi bi-stars text-accent"></i>
              <span class="fw-semibold" style="font-size: 0.85rem;">Morning Briefing</span>
              <span v-if="today.briefingAt" style="font-size: 0.72rem; color: var(--text-muted-custom);">
                {{ today.briefingAt }}
              </span>
            </div>
            <button
              class="btn btn-sm"
              :class="today.briefing ? 'btn-outline-secondary' : 'btn-outline-primary'"
              :disabled="today.loadingBriefing || today.loadingToday"
              style="font-size: 0.78rem;"
              @click="today.generateBriefing()"
            >
              <span v-if="today.loadingBriefing" class="spinner-border spinner-border-sm me-1" role="status"></span>
              <i v-else class="bi bi-lightning-charge me-1"></i>
              {{ today.briefing ? 'Regenerate' : 'Generate Briefing' }}
            </button>
          </div>

          <div v-if="today.loadingToday" class="py-2">
            <div class="placeholder-glow">
              <span class="placeholder col-10 mb-1 d-block" style="height: 0.8rem; border-radius: 4px;"></span>
              <span class="placeholder col-8 mb-1 d-block" style="height: 0.8rem; border-radius: 4px;"></span>
              <span class="placeholder col-6 d-block" style="height: 0.8rem; border-radius: 4px;"></span>
            </div>
          </div>

          <div
            v-else-if="today.briefing"
            class="md-rendered"
            style="font-size: 0.84rem; line-height: 1.6; color: var(--text-main, #e0e0e0); flex: 1; min-height: 0; overflow-y: auto;"
            v-html="renderMd(today.briefing)"
          ></div>

          <div v-else class="text-center py-3" style="color: var(--text-muted-custom); font-size: 0.84rem;">
            <i class="bi bi-moon-stars d-block mb-2" style="font-size: 1.8rem; opacity: 0.35;"></i>
            No briefing yet — click Generate to ask the AI what to focus on.
          </div>
        </div>
      </div>

      <!-- News Feed -->
      <div class="col-12 col-xl-5">
        <div class="card p-3 d-flex flex-column" style="height: 480px;">
          <!-- Header -->
          <div class="d-flex align-items-center justify-content-between mb-2" style="flex-shrink:0;">
            <div class="d-flex align-items-center gap-2">
              <i class="bi bi-newspaper text-accent"></i>
              <span class="fw-semibold" style="font-size: 0.85rem;">News</span>
              <span
                v-if="!today.loadingNews && today.newsItems.length"
                class="badge rounded-pill"
                style="background: var(--accent, #6ea8fe20); color: var(--accent, #6ea8fe); font-size: 0.7rem;"
              >{{ today.newsItems.length }}</span>
            </div>
            <div class="d-flex gap-1">
              <button
                class="btn btn-sm btn-outline-secondary"
                style="font-size: 0.72rem;"
                :disabled="today.loadingNews"
                title="Refresh news"
                @click="today.fetchNews()"
              >
                <i class="bi bi-arrow-clockwise"></i>
              </button>
              <button
                class="btn btn-sm btn-outline-primary"
                style="font-size: 0.72rem;"
                :disabled="today.newsWatchStatus === 'queued'"
                title="Run news watch now"
                @click="today.triggerNewsWatch()"
              >
                <i class="bi bi-broadcast me-1"></i>
                {{ today.newsWatchStatus === 'queued' ? 'Queued…' : 'Watch' }}
              </button>
            </div>
          </div>

          <!-- Error -->
          <div v-if="today.newsError" class="text-danger" style="font-size: 0.78rem; flex-shrink:0;">
            <i class="bi bi-exclamation-triangle me-1"></i>{{ today.newsError }}
          </div>

          <!-- Loading -->
          <div v-if="today.loadingNews" class="py-2 flex-grow-1">
            <div v-for="i in 4" :key="i" class="placeholder-glow mb-2">
              <span class="placeholder col-9 mb-1 d-block" style="height: 0.72rem; border-radius: 3px;"></span>
              <span class="placeholder col-6 d-block" style="height: 0.65rem; border-radius: 3px;"></span>
            </div>
          </div>

          <!-- News items list -->
          <div
            v-else-if="today.newsItems.length"
            class="news-list flex-grow-1"
          >
            <div
              v-for="(item, i) in today.newsItems"
              :key="i"
              class="news-item"
              :class="{
                'news-item--liked': item.feedback === '+1',
                'news-item--disliked': item.feedback === '-1',
                'news-item--clicked': item.clicked,
              }"
            >
              <!-- Title row -->
              <div class="d-flex align-items-start gap-1">
                <span class="news-emoji">📰</span>
                <a
                  :href="item.url || '#'"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="news-title"
                  :class="{ 'news-title--clicked': item.clicked }"
                  @click="today.markClicked(item.bullet)"
                >{{ item.title || item.bullet }}</a>
              </div>

              <!-- Meta row -->
              <div class="news-meta">
                <span v-if="item.domain">{{ item.domain }}</span>
                <span v-if="item.pub_date" class="ms-1" style="opacity:0.6;">{{ item.pub_date }}</span>
                <span v-if="item.topic" class="ms-2 news-topic">{{ item.topic }}</span>
                <span v-if="item.clicked" class="ms-auto" title="Opened">
                  <i class="bi bi-check-circle-fill" style="opacity: 0.5;"></i>
                </span>
                <span v-if="item.feedback" :class="item.clicked ? 'ms-1' : 'ms-auto'">{{ newsIcon(item.feedback) }}</span>
              </div>

              <!-- Reason (truncated) -->
              <div v-if="item.reason" class="news-reason">{{ item.reason }}</div>

              <!-- Feedback buttons -->
              <div class="news-actions">
                <button
                  class="btn-feedback"
                  :class="{ active: item.feedback === '+1' }"
                  title="Relevant"
                  @click="today.submitFeedback(item.bullet, item.feedback === '+1' ? null : '+1')"
                >👍</button>
                <button
                  class="btn-feedback"
                  :class="{ active: item.feedback === '-1' }"
                  title="Not relevant"
                  @click="today.submitFeedback(item.bullet, item.feedback === '-1' ? null : '-1')"
                >👎</button>
              </div>
            </div>
          </div>

          <!-- Empty state -->
          <div v-else class="text-center py-4 flex-grow-1 d-flex flex-column align-items-center justify-content-center"
               style="color: var(--text-muted-custom); font-size: 0.82rem;">
            <i class="bi bi-newspaper d-block mb-2" style="font-size: 1.8rem; opacity: 0.25;"></i>
            No news yet.<br>
            <span style="font-size: 0.76rem;">
              Add <code>news_topics:</code> to a project file,<br>then click Watch to run a search.
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- Today's Tasks -->
    <div class="card p-3 mb-3">
      <div class="d-flex align-items-center justify-content-between mb-2">
        <div class="d-flex align-items-center gap-2">
          <i class="bi bi-list-check text-accent"></i>
          <span class="fw-semibold" style="font-size: 0.85rem;">Active Tasks</span>
          <span
            v-if="!today.loadingToday && today.tasks.length"
            class="badge rounded-pill"
            style="background: var(--accent, #6ea8fe20); color: var(--accent, #6ea8fe); font-size: 0.7rem;"
          >{{ today.tasks.length }}</span>
        </div>
        <button
          class="btn btn-sm btn-outline-secondary"
          style="font-size: 0.78rem;"
          :disabled="today.loadingToday"
          @click="today.fetchToday()"
        >
          <i class="bi bi-arrow-clockwise me-1"></i>Refresh
        </button>
      </div>

      <div v-if="today.loadingToday">
        <div v-for="i in 3" :key="i" class="placeholder-glow mb-2 p-2 rounded" style="background: var(--bg-card, #1e1e2e);">
          <span class="placeholder col-4 mb-1 d-block" style="height: 0.7rem; border-radius: 3px;"></span>
          <span class="placeholder col-9 d-block" style="height: 0.75rem; border-radius: 3px;"></span>
        </div>
      </div>

      <div v-else-if="today.tasks.length">
        <div
          v-for="(task, i) in today.tasks"
          :key="i"
          class="d-flex align-items-start gap-2 mb-2 p-2 rounded"
          style="background: var(--bg-app, #13131f); border: 1px solid var(--border-color, rgba(255,255,255,0.07));"
        >
          <input
            type="checkbox"
            class="form-check-input mt-1 flex-shrink-0"
            style="cursor: pointer;"
            title="Mark done"
            @change="today.toggleTask(task)"
          />
          <div class="flex-grow-1" style="min-width: 0;">
            <div style="font-size: 0.82rem; color: var(--text-main, #e0e0e0);">{{ task.text }}</div>
            <div class="d-flex align-items-center gap-1 mt-1" style="font-size: 0.7rem; color: var(--text-muted-custom);">
              <i class="bi bi-file-earmark-text"></i>
              <span>{{ task.ou }}</span>
              <span v-if="task.due" class="ms-auto flex-shrink-0">
                <i class="bi bi-calendar-event me-1"></i>{{ task.due }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div v-else class="text-center py-3" style="color: var(--text-muted-custom); font-size: 0.84rem;">
        <i class="bi bi-inbox d-block mb-2" style="font-size: 1.8rem; opacity: 0.35;"></i>
        No tasks found in the corpus.<br>
        <span style="font-size: 0.76rem;">Run a corpus reindex to surface your active tasks.</span>
      </div>
    </div>

    <!-- AI Assistant mini-chat -->
    <div class="card p-3">
      <div class="d-flex align-items-center gap-2 mb-2">
        <i class="bi bi-stars text-accent"></i>
        <span class="fw-semibold" style="font-size: 0.85rem;">AI Assistant</span>
        <RouterLink
          to="/ai"
          style="font-size: 0.72rem; margin-left: auto; color: var(--accent, #6ea8fe); text-decoration: none;"
        >Full chat →</RouterLink>
      </div>

      <div v-if="lastAiMsg" class="mb-2">
        <div
          v-if="lastAiMsg.type === 'answer'"
          class="p-2 rounded md-rendered"
          style="background: var(--bg-app, #13131f); border: 1px solid var(--border-color, rgba(255,255,255,0.07)); font-size: 0.81rem; color: var(--text-main, #e0e0e0); max-height: 120px; overflow-y: auto;"
          v-html="renderMd(lastAiMsg.content)"
        ></div>

        <div
          v-else-if="lastAiMsg.type === 'edit'"
          class="rounded"
          style="background: var(--bg-app, #13131f); border: 1px solid var(--border-color, rgba(255,255,255,0.07)); overflow: hidden; font-size: 0.8rem;"
        >
          <div class="px-2 py-1" style="border-bottom: 1px solid var(--border-color); background: rgba(110,168,254,0.05);">
            <template v-if="lastAiMsg.settled && lastAiMsg.confirmed">
              <i class="bi bi-check-circle-fill text-success me-1"></i>
              Applied — <code style="font-size: 0.75rem;">{{ lastAiMsg.edit.rel_path }}</code>
            </template>
            <template v-else-if="lastAiMsg.settled">
              <i class="bi bi-x-circle me-1" style="color: #f87171;"></i> Discarded
            </template>
            <template v-else>
              <i class="bi bi-pencil-square me-1" style="color: var(--accent);"></i>
              Edit <code style="font-size: 0.75rem;">{{ lastAiMsg.edit.rel_path }}</code>
              — {{ lastAiMsg.edit.summary }}
            </template>
          </div>

          <div
            v-if="!lastAiMsg.settled"
            style="font-family: monospace; font-size: 0.72rem; max-height: 100px; overflow-y: auto; background: var(--bg-app);"
          >
            <div
              v-for="(line, li) in lastAiMsg.edit.diff.split('\n').slice(0, 30)"
              :key="li"
              :class="['diff-line-sm', diffLineClass(line)]"
            >{{ line }}</div>
          </div>

          <div v-if="!lastAiMsg.settled" class="d-flex gap-2 px-2 py-1" style="border-top: 1px solid var(--border-color);">
            <button class="btn btn-success btn-sm py-0" style="font-size: 0.74rem;" @click="ai.confirmEdit(ai.messages.length - 1)">
              <i class="bi bi-check-lg me-1"></i>Apply
            </button>
            <button class="btn btn-outline-secondary btn-sm py-0" style="font-size: 0.74rem;" @click="ai.discardEdit(ai.messages.length - 1)">
              <i class="bi bi-x-lg me-1"></i>Discard
            </button>
            <RouterLink to="/ai" style="font-size: 0.72rem; margin-left: auto; color: var(--accent); align-self: center; text-decoration: none;">
              View full diff →
            </RouterLink>
          </div>
        </div>

        <div
          v-else-if="lastAiMsg.type === 'error'"
          class="p-2 rounded"
          style="background: rgba(248,113,113,0.1); color: #f87171; font-size: 0.8rem; border: 1px solid rgba(248,113,113,0.2);"
        >
          <i class="bi bi-exclamation-triangle me-1"></i>{{ lastAiMsg.content }}
        </div>
      </div>

      <div class="input-group">
        <input
          v-model="ai.inputText"
          type="text"
          class="form-control form-control-sm"
          placeholder="Ask anything or give an instruction… (Enter to send)"
          :disabled="ai.loading"
          style="background: var(--bg-app); border-color: var(--border-color); color: var(--text-main);"
          @keydown="onAiKey"
        />
        <button
          class="btn btn-primary btn-sm"
          :disabled="ai.loading || !ai.inputText.trim()"
          @click="sendToAi"
        >
          <span v-if="ai.loading" class="spinner-border spinner-border-sm" role="status"></span>
          <i v-else class="bi bi-send"></i>
        </button>
      </div>
      <div style="font-size: 0.71rem; color: var(--text-muted-custom); margin-top: 0.35rem;">
        AI decides what to do — updates project files, answers questions, or captures to inbox.
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ---- Diff ---- */
.diff-line-sm {
  display: block;
  padding: 0 0.5rem;
  white-space: pre;
  line-height: 1.4;
}
.diff-add    { background: rgba(117,183,152,0.13); color: #75b798; }
.diff-remove { background: rgba(248,113,113,0.13); color: #f87171; }
.diff-hunk   { color: #6ea8fe; opacity: 0.75; }
.diff-header { color: var(--text-muted-custom); opacity: 0.5; }
.diff-context{ color: var(--text-muted-custom); }

/* ---- News panel ---- */
.news-list {
  overflow-y: auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
}

.news-item {
  padding: 0.45rem 0.6rem;
  border-radius: 7px;
  background: var(--bg-app, #13131f);
  border: 1px solid var(--border-color, rgba(255,255,255,0.07));
  font-size: 0.79rem;
}

.news-item--liked  { border-color: rgba(117,183,152,0.3); }
.news-item--disliked { opacity: 0.55; }
.news-item--clicked { opacity: 0.6; }

.news-title--clicked { color: var(--text-muted-custom); }

.news-emoji {
  flex-shrink: 0;
  font-size: 0.8rem;
  line-height: 1.4;
}

.news-title {
  color: var(--accent, #6ea8fe);
  text-decoration: none;
  line-height: 1.4;
  word-break: break-word;
}
.news-title:hover { text-decoration: underline; }

.news-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.15rem;
  margin-top: 0.2rem;
  font-size: 0.7rem;
  color: var(--text-muted-custom);
}

.news-topic {
  background: rgba(110,168,254,0.1);
  border: 1px solid rgba(110,168,254,0.2);
  border-radius: 10px;
  padding: 0 0.4rem;
  font-size: 0.66rem;
}

.news-reason {
  margin-top: 0.2rem;
  font-size: 0.7rem;
  color: var(--text-muted-custom);
  opacity: 0.75;
  line-height: 1.35;

  /* clamp to 2 lines */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.news-actions {
  display: flex;
  gap: 0.25rem;
  margin-top: 0.3rem;
}

.btn-feedback {
  background: none;
  border: 1px solid var(--border-color, rgba(255,255,255,0.07));
  border-radius: 5px;
  padding: 0 0.35rem;
  font-size: 0.72rem;
  line-height: 1.6;
  cursor: pointer;
  transition: background 0.15s;
}
.btn-feedback:hover { background: rgba(255,255,255,0.07); }
.btn-feedback.active { background: rgba(110,168,254,0.15); border-color: rgba(110,168,254,0.4); }

/* ---- Rendered markdown (briefing / AI replies) ---- */
.md-rendered :deep(h1),
.md-rendered :deep(h2),
.md-rendered :deep(h3) {
  font-size: 1em;
  font-weight: 600;
  margin: 0.9em 0 0.4em;
  color: var(--text-main, #e0e0e0);
}
.md-rendered :deep(h1:first-child),
.md-rendered :deep(h2:first-child),
.md-rendered :deep(h3:first-child) { margin-top: 0; }
.md-rendered :deep(p) { margin: 0 0 0.6em; }
.md-rendered :deep(p:last-child) { margin-bottom: 0; }
.md-rendered :deep(ul),
.md-rendered :deep(ol) { margin: 0 0 0.6em; padding-left: 1.3em; }
.md-rendered :deep(li) { margin-bottom: 0.2em; }
.md-rendered :deep(strong) { color: var(--text-main, #f0f0f0); }
.md-rendered :deep(hr) { border: none; border-top: 1px solid var(--border-color, rgba(255,255,255,0.1)); margin: 0.8em 0; }
.md-rendered :deep(a) { color: var(--accent, #6ea8fe); }
.md-rendered :deep(code) {
  background: rgba(255,255,255,0.08);
  padding: 0.1em 0.35em;
  border-radius: 3px;
  font-size: 0.9em;
}
</style>
