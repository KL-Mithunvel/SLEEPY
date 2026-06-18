# PMA Frontend Documentation

## Technology Stack

- **Framework**: Vue 3 (Composition API + Options API)
- **Build tool**: Vite 7.x (dev server port 5173)
- **Routing**: vue-router 4.3
- **UI**: Bootstrap 5.3.3 + bootstrap-icons 1.11.3
- **Markdown rendering**: markdown-it 14.1
- **Auth**: keycloak-js 26.2.3
- **Tests**: vitest 3.2.0
- **Node target**: modern (ES modules)

## Directory Structure

```
code/frontend/
  package.json
  vite.config.js
  index.html
  src/
    main.js              # App entry point, Keycloak bootstrap, router setup
    App.vue              # Root component
    router/
      index.js           # vue-router routes definition
    views/
      TodayView.vue      # /today — daily log + AI chat
      QPlanView.vue      # /q-plan — quarterly/annual plans
      ProjectsView.vue   # /projects — project file browser/editor
      TeamView.vue       # /team — govern tracking + people directory
      FilesView.vue      # /files — full corpus file browser + git history
      SearchView.vue     # /search — literal text search
      SettingsView.vue   # /settings — user settings (MCP toggle, etc.)
    components/
      <shared components>
    composables/
      <Vue composables>
    assets/
      <static assets>
```

## `vite.config.js` — Build Configuration

```javascript
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:5000',   // proxies /api/* to Flask backend
      '/mcp': 'http://127.0.0.1:5000',   // proxies /mcp/* to Flask backend
    }
  }
})
```

## `src/main.js` — Application Entry Point

### Keycloak Bootstrap (runs BEFORE Vue mounts)
1. Creates Keycloak instance with realm URL + client ID from `/api/auth/config`
2. Calls `keycloak.init({ onLoad: 'login-required', pkceMethod: 'S256' })`
3. If authenticated: extracts token, mounts Vue app
4. Token auto-refresh: sets up interval to refresh token before expiry
5. All API calls include: `Authorization: Bearer <token>`

### Router Configuration
```javascript
const routes = [
  { path: '/', redirect: '/today' },
  { path: '/today', component: TodayView },
  { path: '/q-plan', component: QPlanView },
  { path: '/projects', component: ProjectsView },
  { path: '/team', component: TeamView },         // govern + people merged
  { path: '/files', component: FilesView },
  { path: '/search', component: SearchView },
  { path: '/settings', component: SettingsView },
  // Legacy redirects:
  { path: '/govern', redirect: '/team' },
  { path: '/people', redirect: '/team?tab=people' },
  { path: '/history', redirect: '/files?tab=history' },
]
```

## Views

### `TodayView.vue` — Daily View (Primary Interface)
The main daily work surface:
- **Left panel**: Today's daily log (`Daily/<date>.md`) rendered as editable markdown
- **Right panel**: AI chat interface (SSE streaming)
- AI chat connects to `POST /api/ai/chat` with `ou` set to current context
- SSE handling: processes `delta`, `tool_progress`, `error`, `done` event types
- `done` event triggers: pma-edit detection → applies to displayed file; actions list rendered
- Chat history loaded from `GET /api/ai/history` on mount
- History cleared via `DELETE /api/ai/history`
- File save: `PUT /api/corpus/file`
- move-line functionality: drag or button to move task to Plans

### `QPlanView.vue` — Quarterly/Annual Plans
- Lists plan periods (year, quarters, months) for current OU
- Loads from `GET /api/corpus/plans?ou=`
- Shows plan markdown rendered with markdown-it
- Edit in-place: `PUT /api/corpus/file`
- OU switcher (dropdown)

### `ProjectsView.vue` — Project Files Browser
- Tree view of `Projects/` directory: `GET /api/corpus/tree?ou=`
- Click file → load + render markdown: `GET /api/corpus/file?path=`
- Edit mode: textarea with save → `PUT /api/corpus/file`
- New file, rename, delete operations
- Section navigation: `GET /api/corpus/sections?path=`
- OU switcher

### `TeamView.vue` — Govern + People (Merged View)
Two tabs:
1. **Govern tab**: Team tracking for current month
   - `GET /api/corpus/govern?ou=&month=`
   - Shows recur_tasks, project_tasks, daily_tasks per team member
   - Check/uncheck tasks → `POST /api/corpus/line-edit`
2. **People tab**: People directory
   - `GET /api/corpus/people?ou=`
   - Renders People.md sections per person
   - Nicks: `GET /api/corpus/people/nicks?ou=`
- Legacy routes `/govern` and `/people` redirect here

### `FilesView.vue` — Full Corpus Browser + History
Two tabs:
1. **Files tab**: Full recursive tree
   - `GET /api/corpus/tree` (no OU filter)
   - Browse, read, edit any corpus file
2. **History tab**: Git log
   - `GET /api/corpus/git/log`
   - Click commit → `GET /api/corpus/git/show/<sha>` shows diff

### `SearchView.vue` — Text Search
- Search input → `GET /api/corpus/search?q=&ou=&limit=`
- Literal substring search (not semantic)
- Results show: file path, line number, matching text
- Click result → opens file in FilesView

### `SettingsView.vue` — User Settings
- Loads: `GET /api/corpus/settings`
- Saves: `PUT /api/corpus/settings`
- Settings include:
  - `mcp_enabled`: toggle MCP server access
- Shows index status: `GET /api/corpus/index-status`
- Manual reindex button: `POST /api/corpus/reindex`
- Queue stats: `GET /api/corpus/queue-stats`

## API Communication Pattern

All API calls follow this pattern:
```javascript
const response = await fetch('/api/corpus/file?path=' + path, {
  headers: {
    'Authorization': `Bearer ${keycloak.token}`,
    'Content-Type': 'application/json',
  }
})
```

For SSE streaming (chat):
```javascript
const eventSource = new EventSource(url)  // OR fetch with ReadableStream
// Process chunks manually for Authorization header support
const response = await fetch('/api/ai/chat', {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${keycloak.token}` },
  body: JSON.stringify({ messages, ou }),
})
const reader = response.body.getReader()
// Read and parse SSE events manually
```

## Markdown Rendering

Uses `markdown-it` for rendering:
```javascript
import MarkdownIt from 'markdown-it'
const md = new MarkdownIt({ html: false, linkify: true, typographer: true })
const rendered = md.render(markdownContent)
```

## Authentication Flow (Frontend)

1. On app load: fetch `/api/auth/config` to get `realm_url` and `client_id`
2. Initialize Keycloak: `new Keycloak({ url: realm_url, realm: 'Office', clientId: 'pma' })`
3. Call `keycloak.init({ onLoad: 'login-required', pkceMethod: 'S256' })`
4. If not authenticated → Keycloak redirects to login page
5. After login → redirected back with auth code
6. Keycloak exchanges code for tokens (PKCE S256)
7. `keycloak.token` is the access token (JWT)
8. Set up `setInterval` to call `keycloak.updateToken(60)` (refresh if <60s remaining)
9. Pass token as `Authorization: Bearer <token>` on all API requests

## Build for Production

```bash
cd code/frontend/
npm install
npm run build    # outputs to dist/
```

Production `dist/` is served by the `pma-frontend` Docker container (Nginx).

## `Dockerfile.frontend` (approximate structure)
```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf
EXPOSE 80
```

The Nginx config should:
- Serve static files from `/usr/share/nginx/html`
- Proxy `/api/` → backend container
- Proxy `/mcp/` → backend container
- SPA fallback: all unmatched routes → `index.html`

## Frontend Dependencies (package.json)

### Runtime Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| `vue` | ^3.4.0 | UI framework |
| `vue-router` | ^4.3.0 | Client-side routing |
| `bootstrap` | ^5.3.3 | CSS framework + components |
| `bootstrap-icons` | ^1.11.3 | Icon library |
| `markdown-it` | ^14.1.1 | Markdown → HTML renderer |
| `keycloak-js` | ^26.2.3 | Keycloak OIDC client |

### Dev Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| `@vitejs/plugin-vue` | ^6.0.0 | Vite Vue plugin |
| `vite` | ^7.0.0 | Build tool + dev server |
| `vitest` | ^3.2.0 | Unit test framework |

## Progressive Web App (PWA)
- PWA was in scope but deferred (see TODO.md)
- Service worker not implemented yet
- Manifest not configured yet

## Keycloak JS Configuration Details
- `pkceMethod: 'S256'` — PKCE with SHA-256 code challenge
- `onLoad: 'login-required'` — redirect to Keycloak if not authenticated
- Token storage: in-memory (keycloak-js manages this)
- Refresh threshold: 60 seconds before expiry
- Silent refresh not configured (uses redirect flow)
