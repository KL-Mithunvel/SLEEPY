import { createRouter, createWebHistory } from 'vue-router'
import TodayView from '../views/TodayView.vue'

const routes = [
  { path: '/',         redirect: '/today' },
  { path: '/today',    component: TodayView,                                              meta: { title: 'Today',    perm: 'logs:read' } },
  { path: '/projects', component: () => import('../views/ProjectsView.vue'),              meta: { title: 'Projects', perm: 'projects:read' } },
  { path: '/logs',     component: () => import('../views/LogsView.vue'),                  meta: { title: 'Logs',     perm: 'logs:read' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = `${to.meta.title || 'SLEEPY'} — SLEEPY`
})

export default router
