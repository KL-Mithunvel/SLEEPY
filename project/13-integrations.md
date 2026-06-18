# PMA External Integrations

## Overview

PMA supports several optional external integrations. All are configured via `secrets_app.py` or environment variables. If credentials are not configured, the integration is silently disabled.

## Jira Cloud Integration

### Purpose
Sync tasks between the MD corpus and Jira Cloud issues. Tasks with `JIRA:<KEY>` tokens are tracked.

### Configuration
```python
# secrets_app.py
JIRA_URL = "https://company.atlassian.net"
JIRA_EMAIL = "user@company.com"
JIRA_TOKEN = "your-jira-api-token"       # from https://id.atlassian.com/manage/api-tokens
JIRA_PROJECT_KEY = "PROJ"                 # default project key
```

### Task Syntax in MD Corpus
```markdown
- [ ] Implement login page JIRA:PROJ-123
- [x] Fix bug in auth flow JIRA:PROJ-124
```

### How It Works
1. `jira_sync_job` runs every 900s (15 minutes) if Jira configured
2. Scans corpus for tasks with `JIRA:<KEY>` tokens
3. Fetches issue status from Jira Cloud REST API v3:
   ```
   GET https://company.atlassian.net/rest/api/3/issue/PROJ-123
   Authorization: Basic base64(email:token)
   ```
4. If Jira issue is `Done`/`Closed` and corpus task is `- [ ]`: updates to `- [x]`
5. Changes committed to git as `batch:` commit

### Creating Jira Issues
The `jira_create` task handler creates issues:
```python
# Enqueued by chat AI tool:
task_queue.enqueue("jira_create", {
    "project_key": "PROJ",
    "summary": "Task title",
    "description": "Task description",
    "issue_type": "Task",
})
```

## Office 365 / Email Integration

### Purpose
Send emails directly from chat (AI tool) or task queue.

### Configuration
```python
# secrets_app.py
O365_CLIENT_ID = "azure-app-client-id"
O365_CLIENT_SECRET = "azure-app-client-secret"
O365_TENANT_ID = "azure-tenant-id"
```

Azure app registration requirements:
- API permissions: `Mail.Send` (Microsoft Graph)
- Application permissions (not delegated) for sending as service account
- OR delegated permissions with user consent

### How It Works
1. MSAL client credentials flow:
   ```python
   import msal
   app = msal.ConfidentialClientApplication(
       O365_CLIENT_ID,
       client_credential=O365_CLIENT_SECRET,
       authority=f"https://login.microsoftonline.com/{O365_TENANT_ID}"
   )
   token = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
   ```
2. Send email via Graph API:
   ```
   POST https://graph.microsoft.com/v1.0/me/sendMail
   Authorization: Bearer <token>
   ```

### AI Tool Usage
```python
# Chat tool: send_email
tool_input = {
    "to": "recipient@example.com",
    "subject": "Meeting Notes",
    "body": "Here are the notes from today's meeting..."
}
# Handler enqueues to task_queue
task_queue.enqueue("email", tool_input)
```

## Telegram Integration

### Purpose
Send notifications directly to a Telegram chat/group.

### Configuration
```python
# secrets_app.py
TELEGRAM_BOT_TOKEN = "123456789:AABBCCDDEEFFaabbccddeeff1234567890"
TELEGRAM_CHAT_ID = "-100123456789"    # negative for groups, positive for DMs
```

Getting credentials:
1. Create bot via @BotFather → get `TELEGRAM_BOT_TOKEN`
2. Add bot to target chat/group
3. Get `TELEGRAM_CHAT_ID` from `https://api.telegram.org/bot<TOKEN>/getUpdates`

### How It Works
Task handler sends via Bot API:
```python
import urllib.request, json

url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
data = json.dumps({
    "chat_id": TELEGRAM_CHAT_ID,
    "text": message,
    "parse_mode": "Markdown",
}).encode()
urllib.request.urlopen(url, data)
```

### AI Tool Usage
```python
# Chat tool: send_telegram
tool_input = {"message": "Meeting in 30 minutes"}
task_queue.enqueue("telegram", tool_input)
```

## Anthropic News Watch (Message Batches API)

### Purpose
Nightly news monitoring for projects using Anthropic's async batch processing.

### How It Works
1. **Submission** (nightly):
   ```python
   from anthropic import Anthropic
   client = Anthropic(api_key=ANTHROPIC_API_KEY)
   
   batch = client.messages.batches.create(
       requests=[
           {
               "custom_id": f"news-{ou}-{date}",
               "params": {
                   "model": DEFAULT_MODEL,
                   "max_tokens": 1024,
                   "messages": [{
                       "role": "user",
                       "content": f"Find recent news about: {project_keywords}"
                   }]
               }
           }
           for ou, project_keywords in projects.items()
       ]
   )
   # batch.id stored for polling
   ```

2. **Polling** (every 300s):
   ```python
   batch = client.messages.batches.retrieve(batch_id)
   if batch.processing_status == "ended":
       for result in client.messages.batches.results(batch_id):
           # Process result.message.content
           # Write news summary to project file
   ```

### API Endpoints
- `POST /api/corpus/news-watch` — trigger on-demand news fetch
- `GET /api/corpus/news-watch/status/<job_id>` — poll status
- `GET /api/corpus/news-feedback` — list feedback
- `POST /api/corpus/news-feedback` — submit feedback (👍/👎)

### Disabling
Set `NEWS_WATCH_CRON_DISABLED=True` to disable nightly submission.

## Keycloak (Authentication SSO)

### Purpose
Single Sign-On for PMA using Keycloak as OIDC provider.

### Keycloak Setup Requirements
1. **Realm**: any name (e.g. `MyRealm`) — must match realm in `KEYCLOAK_REALM_URL`
2. **Client**: `pma` (must match `KEYCLOAK_CLIENT_ID`)
   - Client protocol: `openid-connect`
   - Access type: `public` (PKCE, no client secret)
   - Valid redirect URIs: `https://pma.example.com/*`
   - Web origins: `https://pma.example.com`
   - Standard flow: enabled
   - PKCE challenge method: `S256`
3. **Users**: Keycloak users whose username maps to `DATA_ROOT/<username>/`
4. **Optional**: Client roles for RBAC

### Configuration
```python
KEYCLOAK_REALM_URL = "https://sso.example.com/realms/MyRealm"
KEYCLOAK_HOST_IP = "192.168.1.xxx"   # for Docker container direct access
```

### Internal URL Rewrite
Docker containers cannot access Keycloak via the public URL (Caddy). The backend rewrites:
```
https://sso.example.com/realms/MyRealm → http://<KEYCLOAK_HOST_IP>:8080/realms/MyRealm
```
via `auth_utils._make_internal_url()`. This is why `KEYCLOAK_HOST_IP` + `extra_hosts` in docker-compose are needed.

## Caddy (Reverse Proxy)

Not in docker-compose (runs separately on host). Example config:
```
pma.example.com {
    reverse_proxy /api/* pma-backend:5000
    reverse_proxy /mcp/* pma-backend:5000
    reverse_proxy /.well-known/* pma-backend:5000
    reverse_proxy /authorize pma-backend:5000
    reverse_proxy /token pma-backend:5000
    reverse_proxy /* pma-frontend:80
    tls admin@example.com
}
```

## Git Remote (MD Corpus Backup)

Not yet automated (see TODO.md). Manual setup:
```bash
cd DATA_ROOT/<user>/md
git remote add origin git@github.com:user/md-corpus.git
git push -u origin main
```

Future: `worker.py` could add a `git_push_job` to push MD corpus to remote automatically.

## Integration Status Summary

| Integration | Status | Config Required |
|-------------|--------|----------------|
| Anthropic Claude API | Required | `ANTHROPIC_API_KEY` |
| Keycloak | Required (or DEV_AUTH_BYPASS) | `KEYCLOAK_REALM_URL` |
| Jira Cloud | Optional | `JIRA_URL`, `JIRA_EMAIL`, `JIRA_TOKEN` |
| Office 365 Email | Optional | `O365_CLIENT_ID`, `O365_CLIENT_SECRET`, `O365_TENANT_ID` |
| Telegram | Optional | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| News Watch | Optional (auto-disabled if no config) | `ANTHROPIC_API_KEY` (same as Claude) |
| MCP Server | Optional (user opt-in) | `MCP_API_KEY` + user setting |
| Git Remote Push | Not yet implemented | N/A |
| Calendar | Not yet implemented | N/A |
| Weather Station | Not yet implemented | N/A |
