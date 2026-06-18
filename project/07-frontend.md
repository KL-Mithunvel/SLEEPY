# PMA Frontend Documentation

## Technology Stack

- **Framework**: Vue 3 (Composition API + Options API mixed)
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
    App.vue              # Root component with sidebar + router-view
    api.js               # Unified API client (all backend calls go through here)
    router/
      index.js           # vue-router routes definition
    views/
      TodayView.vue      # /today — daily log + AI chat (primary interface)
      QPlanView.vue      # /q-plan — quarterly/annual plans
      ProjectsView.vue   # /projects — project file browser/editor
      FilesBrowserView.vue # Sub-view: corpus file browser
      TeamView.vue       # /team — govern tracking + people directory (merged)
      GovernView.vue     # Sub-view: govern tracking
      PeopleView.vue     # Sub-view: people directory
      FilesView.vue      # /files — full corpus file browser + git history
      HistoryView.vue    # Sub-view: git history
      SearchView.vue     # /search — literal text search
      SettingsView.vue   # /settings — user settings
    components/
      AppSidebar.vue     # Left navigation sidebar
      ChatPanel.vue      # AI chat panel (SSE streaming)
      DiaryPanel.vue     # Daily log display + editing
      FileTree.vue       # Recursive file tree display
      InboxPanel.vue     # Inbox display + item management
      MoveTool.vue       # Move task to Plans tool
      OuSelector.vue     # OU dropdown selector
      ProjectForm.vue    # Create/edit project form
      ProjectList.vue    # Project listing with flags/status
      TaskBlocks.vue     # Task rendering with checkbox interaction
    composables/
      useProjectFile.js  # Project file load/save/edit logic
      useTaskContent.js  # Task line parsing and rendering
    stores/
      auth.js            # Authentication state (token, username, roles)
      corpus.js          # Corpus-wide state (OUs, files)
      ou.js              # Active OU state
      plans.js           # Plans list state
    utils/
      taskValidation.js  # Task line format validation
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

## `src/api.js` — Unified API Client

All backend communication goes through `api.js`. This module provides typed wrappers for all HTTP verbs and handles token refresh before each request.

```javascript
// api.js
import { keycloak } from './main.js'  // shared Keycloak instance

async function ensureFreshToken(minValiditySec = 30) {
  // Refresh token if it expires in less than minValiditySec seconds
  try {
    await keycloak.updateToken(minValiditySec)
  } catch (e) {
    keycloak.login()  // redirect to login if refresh fails
    throw e
  }
  return keycloak.token
}

async function apiGet(path, params = {}) { ... }
async function apiPost(path, body) { ... }
async function apiPut(path, body) { ... }
async function apiDelete(path) { ... }

export { apiGet, apiPost, apiPut, apiDelete }
```

**Key behaviors:**
- Calls `ensureFreshToken(30)` before every request — ensures token is valid for at least 30 more seconds
- If `updateToken` fails (refresh token expired), calls `keycloak.login()` to redirect to login page
- All requests include `Authorization: Bearer <token>` header
- All POST/PUT requests include `Content-Type: application/json`
- Throws on non-2xx responses (consumers handle errors)

## `src/main.js` — Application Entry Point

### Keycloak Bootstrap (runs BEFORE Vue mounts)
1. Fetch `/api/auth/config` to get `realm_url`, `client_id`, `dev_bypass` flag
2. Create Keycloak instance: `new Keycloak({ url, realm, clientId })`
3. Call `keycloak.init({ onLoad: 'login-required', pkceMethod: 'S256' })`
4. If authenticated: extract token, create Vue app, mount to `#app`
5. Export `keycloak` instance for use by `api.js`

### Router Configuration
```javascript
const routes = [
  { path: '/', redirect: '/today' },
  { path: '/today', component: TodayView },
  { path: '/q-plan', component: QPlanView },
  { path: '/projects', component: ProjectsView },
  { path: '/team', component: TeamView },
  { path: '/files', component: FilesView },
  { path: '/search', component: SearchView },
  { path: '/settings', component: SettingsView },
  // Legacy redirects (backwards compatibility):
  { path: '/govern', redirect: '/team' },
  { path: '/people', redirect: '/team?tab=people' },
  { path: '/history', redirect: '/files?tab=history' },
]
```

## Views

### `TodayView.vue` — Daily View (Primary Interface)
The main daily work surface. Renders in two panels:

**Left panel — Diary:**
- Today's daily log (`Daily/<date>.md`) rendered as editable markdown
- Uses `DiaryPanel.vue` component
- Checkbox state toggled via `POST /api/corpus/line-edit`
- File save: `PUT /api/corpus/file`
- Move-task-to-Plans: `MoveTool.vue` component

**Right panel — AI Chat:**
- Uses `ChatPanel.vue` component
- Connects to `POST /api/ai/chat` with `ou` set to current OU
- SSE streaming: reads response body as stream
- Processes event types: `delta` (text chunk), `tool_progress` (tool call update), `error`, `done`
- `done` event: pma-edit blocks detected → applied to displayed files; actions list rendered
- Chat history: `GET /api/ai/history` on mount; `DELETE /api/ai/history` to clear

**State:**
- Active OU from `ou.js` store
- Today's date constructed client-side

### `QPlanView.vue` — Quarterly/Annual Plans
- Lists plan periods (year, quarters, months) for current OU
- Loads from `GET /api/corpus/plans?ou=<ou>`
- Renders plan markdown with markdown-it
- Edit in-place: textarea with save → `PUT /api/corpus/file`
- OU switcher: `OuSelector.vue`

### `ProjectsView.vue` — Project Files Browser
- Tree view of `Projects/` directory: `GET /api/corpus/tree?ou=<ou>`
- Uses `ProjectList.vue` for listing (shows flags: star/important/urgent icons)
- Uses `FileTree.vue` for directory tree
- Click project → load + render markdown: `GET /api/corpus/file?path=<path>`
- Edit mode: textarea with save → `PUT /api/corpus/file`
- New project: `ProjectForm.vue` modal (validates frontmatter fields)
- Section navigation: `GET /api/corpus/sections?path=<path>`
- OU switcher

**ProjectForm.vue fields:**
- Title (required)
- Key (`GROUP-CODE` format, validated by `taskValidation.js`)
- Status (active/paused/complete/archived)
- Priority (P1/P2/P3)
- Owner (@nick)
- Flag (star/important/urgent)
- news_topics (multi-line)

### `TeamView.vue` — Govern + People (Merged View)
Two tabs, internally uses `GovernView.vue` and `PeopleView.vue`:

**Govern tab (`GovernView.vue`):**
- Team tracking for current month
- `GET /api/corpus/govern?ou=<ou>&month=<YYYY-MM>`
- Shows tasks per team member: recur_tasks, project_tasks, daily_tasks
- Check/uncheck tasks → `POST /api/corpus/line-edit`

**People tab (`PeopleView.vue`):**
- People directory from `<OU>/People/` files
- `GET /api/corpus/people?ou=<ou>`
- Renders People.md sections per person
- Nicks list: `GET /api/corpus/people/nicks?ou=<ou>`

### `FilesView.vue` — Full Corpus Browser + History
Two tabs, internally uses `FilesBrowserView.vue` and `HistoryView.vue`:

**Files tab (`FilesBrowserView.vue`):**
- Full recursive tree: `GET /api/corpus/tree` (no OU filter)
- Uses `FileTree.vue` component
- Browse, read, edit any corpus file
- `GET /api/corpus/file?path=`, `PUT /api/corpus/file`

**History tab (`HistoryView.vue`):**
- Git commit log: `GET /api/corpus/git/log`
- Click commit → `GET /api/corpus/git/show/<sha>` shows unified diff
- Commits labelled by prefix (`AI:`, `archive:`, `housekeeping:`, etc.)

### `SearchView.vue` — Text Search
- Search input → `GET /api/corpus/search?q=<query>&ou=<ou>&limit=<n>`
- Literal substring search (not semantic; backed by `md_grep.py`)
- Results: file path, line number, matching text (highlighted)
- Click result → opens file in FilesView

### `SettingsView.vue` — User Settings
Sections:
- **General settings**: `GET /api/corpus/settings` / `PUT /api/corpus/settings`
  - `mcp_enabled`: toggle MCP server access
- **Index status**: `GET /api/corpus/index-status` (ChromaDB health + last sync time)
- **Manual reindex**: `POST /api/corpus/reindex`
- **Task queue stats**: `GET /api/corpus/queue-stats`
- **News watch feedback**: list and rate news items (👍/👎)
- **Chat history**: clear button → `DELETE /api/ai/history`

---

## Components

### `AppSidebar.vue`
Left navigation sidebar:
- Links to all views with icons (bootstrap-icons)
- Active OU display + `OuSelector.vue` dropdown
- User info (username from `auth.js` store)
- Collapse/expand on mobile

### `ChatPanel.vue`
AI chat panel with SSE streaming:
- Message input textarea (Shift+Enter for newline, Enter to send)
- Renders AI response as streaming markdown
- Tool call progress: shows tool name + arguments while executing
- pma-edit detection: parses `\`\`\`pma-edit` blocks in AI response and calls `PUT /api/corpus/file`
- Error display inline (red banner)
- Sends to: `POST /api/ai/chat`

### `DiaryPanel.vue`
Daily log display and editing:
- Renders daily markdown as formatted HTML (TaskBlocks for task lines)
- Inline edit: click to edit, textarea appears
- Uses `TaskBlocks.vue` for checkboxes
- Drag-to-move tasks: activates `MoveTool.vue`

### `FileTree.vue`
Recursive file tree component:
- Takes a tree object (from `GET /api/corpus/tree`)
- Renders folders (collapsible) and files
- Emits `select` event when file clicked
- Shows file modification time

### `InboxPanel.vue`
Inbox items from `inbox.md`:
- Groups by section: Log, News, Housekeeping
- Checkbox state: `POST /api/corpus/line-edit`
- News items show feedback buttons (👍/👎): `POST /api/corpus/news-feedback`

### `MoveTool.vue`
Move a task from the daily view to a plan file:
- Modal dialog: select target plan period (year/quarter/month)
- `GET /api/corpus/plans?ou=` to populate options
- On confirm: `POST /api/corpus/move-task`

### `OuSelector.vue`
OU dropdown:
- Loads OU list from `corpus.js` store
- Emits `change` event when selection changes
- Updates `ou.js` store active OU

### `ProjectForm.vue`
Create/edit project modal:
- Validates `key:` format via `taskValidation.js`
- Status/priority/flag dropdowns
- Submits: `PUT /api/corpus/file` with generated markdown content

### `ProjectList.vue`
Project listing in a grid/table:
- Shows key, title, status, priority, owner
- Flag icons: amber star / red exclamation / orange hourglass
- Click → load project in editor
- Filter by status (active/all)

### `TaskBlocks.vue`
Task line rendering:
- Parses markdown task lines: `- [ ]`, `- [x]`, `- [>]`, `- [-]`
- Renders checkbox, label, annotations (`due:`, `@nick`, `JIRA:`, progress `---`)
- Progress annotation (`---` separator): renders remainder in muted grey
- Checkbox click: `POST /api/corpus/line-edit`

---

## Stores

### `auth.js`
```javascript
// Pinia store (or reactive state)
state: {
  token: null,         // JWT access token string
  username: null,      // Keycloak preferred_username
  roles: [],           // Keycloak realm roles
  isAuthenticated: false,
}
```

### `corpus.js`
```javascript
state: {
  ous: [],             // list of OU names from GET /api/corpus/ous
  files: {},           // cached file contents by path
}
```

### `ou.js`
```javascript
state: {
  activeOu: null,      // currently selected OU name
}
// Persisted to localStorage for session continuity
```

### `plans.js`
```javascript
state: {
  plans: [],           // list of plan period objects {period, path, has_tasks}
  loading: false,
}
```

---

## Composables

### `useProjectFile.js`
Encapsulates project file load/save/section-nav logic:
```javascript
const { file, loading, save, sections, loadSection } = useProjectFile(path)
// save(content): PUT /api/corpus/file
// sections: GET /api/corpus/sections?path=
// loadSection(sectionName): reads specific section content
```

### `useTaskContent.js`
Task line parsing utilities:
```javascript
const { parseTasks, renderTask, updateTaskState } = useTaskContent()
// parseTasks(markdownContent): extracts task lines with metadata
// renderTask(task): formats for display (handles --- annotation)
// updateTaskState(path, lineNum, newState): POST /api/corpus/line-edit
```

---

## Utils

### `taskValidation.js`
Validation functions for task and project field formats:
```javascript
export function validateProjectKey(key) {
  // Must match [A-Z0-9]+-[A-Z0-9]+ (GROUP-CODE format)
  return /^[A-Z0-9]+-[A-Z0-9]+$/.test(key)
}

export function validateDueDate(due) {
  // Accepts: Mmm-DD, YYYY-MM-DD, Mmm, Q1-Q4, YYYY
  ...
}

export function validateNick(nick) {
  // Must start with uppercase letter
  return /^[A-Z][\w-]*$/.test(nick)
}
```

---

## SSE Streaming (Chat)

Since `EventSource` doesn't support custom headers, the frontend uses `fetch` with `ReadableStream`:

```javascript
const response = await fetch('/api/ai/chat', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ messages, ou, project_path }),
})

const reader = response.body.getReader()
const decoder = new TextDecoder()

while (true) {
  const { done, value } = await reader.read()
  if (done) break
  const text = decoder.decode(value)
  // Parse SSE lines: "data: {...}\n\n"
  for (const line of text.split('\n')) {
    if (!line.startsWith('data: ')) continue
    const event = JSON.parse(line.slice(6))
    handleEvent(event)  // delta | tool_progress | error | done
  }
}
```

**SSE event types:**
| Type | Payload | Action |
|------|---------|--------|
| `delta` | `{text: "..."}` | Append to AI response display |
| `tool_progress` | `{tool: "...", args: {...}, status: "calling/done"}` | Show/update tool call indicator |
| `error` | `{message: "..."}` | Display error banner |
| `done` | `{actions: [...], edits: [...]}` | Finalize response; apply pma-edit blocks; render action list |

---

## Authentication Flow (Frontend)

1. App loads → fetch `/api/auth/config` (public endpoint) to get `realm_url`, `client_id`, `dev_bypass`
2. Initialize Keycloak: `new Keycloak({ url: realm_url, realm: 'Office', clientId: client_id })`
3. Call `keycloak.init({ onLoad: 'login-required', pkceMethod: 'S256' })`
4. Not authenticated → Keycloak redirects to SSO login page
5. After login → redirected back; Keycloak exchanges code for tokens (PKCE S256)
6. `keycloak.token` = access token (JWT RS256)
7. `api.js` calls `ensureFreshToken(30)` before every request — refreshes if <30s remaining
8. If refresh fails → `keycloak.login()` redirect

**DEV_AUTH_BYPASS mode:**
- When backend has `DEV_AUTH_BYPASS=1`, `/api/auth/config` returns `dev_bypass: true`
- Frontend skips Keycloak initialization
- Sends `X-Dev-User: <username>` header instead of `Authorization: Bearer`

---

## Markdown Rendering

Uses `markdown-it` for rendering corpus content:
```javascript
import MarkdownIt from 'markdown-it'
const md = new MarkdownIt({ html: false, linkify: true, typographer: true })
const rendered = md.render(markdownContent)
```

Task lines are rendered by `TaskBlocks.vue` rather than markdown-it (to support interactive checkboxes).

Progress annotations (`---`) are rendered with custom post-processing:
```javascript
// After rendering, find .task-label elements and split on ' --- '
// Part before --- : normal weight
// Part after --- : muted-grey, smaller font
```

---

## Build for Production

```bash
cd code/frontend/
npm install
npm run build    # outputs to dist/
```

Production `dist/` is served by the `pma-frontend` Docker container (Nginx).

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

The Nginx config:
- Serves static files from `/usr/share/nginx/html`
- Proxies `/api/` → backend container
- Proxies `/mcp/` → backend container
- SPA fallback: all unmatched routes → `index.html`

---

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

---

## Progressive Web App (PWA)
- PWA was in scope but deferred
- Service worker not implemented
- Manifest not configured
