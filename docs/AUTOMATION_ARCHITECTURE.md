# CodeAI J.A.R.V.I.S. Automation Architecture

Status: architecture foundation only. This branch deliberately does not enable remote computer control or arbitrary command execution.

## Existing application (verified from main)

- Flask application in `app.py`, started by Render as `gunicorn app:app`.
- OpenRouter chat and project generation in `ai_engine.py`.
- SQLite chat/message history in `database/codeai.db`.
- Project file helpers in `project_manager.py`.
- Browser UI in `templates/index.html` and `static/app.js`.
- Render service: `codeultimate08`, free plan, Oregon region, public URL `https://codeultimate08.onrender.com`.
- `render.yaml` is not present in the repository.
- The current service has no authentication layer in the inspected Flask routes. Do not expose new automation endpoints until authentication and authorization are implemented.

## Target topology

```text
Windows browser / Android browser (PWA)
             |
             | HTTPS + short-lived session
             v
       Flask API on Render
       |        |         |
       |        |         +-- OpenRouter (planning only)
       |        +------------ SQLite/Postgres task metadata
       +--------------------- policy + approval gate
             |
             | outbound HTTPS polling; device-scoped token
             v
       Windows companion agent (installed/run by the user)
       |       |             |
       |       |             +-- browser automation (Playwright, allowlisted)
       |       +---------------- local file operations (approved workspace)
       +------------------------ isolated code tests (sandbox only)
```

Android is the remote control surface, not a privileged execution host. The Windows agent initiates outbound connections; do not open inbound router ports or expose a desktop-control server to the internet. Render is ephemeral on the free plan, so it must not be treated as durable storage for user files or a Windows process host.

## Core services

1. **Identity and device pairing**
   - Add user accounts or a trusted identity provider before automation is enabled.
   - Pair each Windows agent with a one-time pairing code; exchange it for a revocable, device-scoped token.
   - Store only hashed tokens. Support token rotation and device revocation.
   - Require HTTPS, CSRF protection for cookie sessions, rate limits, and ownership checks on every task/chat/project route.
   - Never put OpenRouter keys or the Windows agent token in browser JavaScript, URLs, logs, or Git.

2. **Task planner**
   - Ask the LLM for a strict JSON plan, not executable shell commands.
   - Validate every action against a server-side tool registry and JSON schema.
   - Keep uploaded files and webpages untrusted; prompt injection must not expand tool permissions.
   - If output is invalid, return a planning error; never fall back to executing free-form text.

3. **Policy and approval gate**
   - Read-only actions can be eligible for immediate execution within the user's own workspace.
   - File creation/edits, browser form submissions, repository writes, publishing, sending messages, and other externally visible actions require a clear preview and explicit approval.
   - Deletion, account/security changes, purchases, payments, and administrative/system changes remain blocked or require a separate carefully designed authorization flow.
   - Provide cancel, timeout, audit history, and an emergency agent disconnect control.

4. **Task queue and worker**
   - Persist task state: `queued -> awaiting_approval -> approved -> running -> succeeded|failed|cancelled`.
   - Use Postgres for production task/device state. SQLite on the current Render free deployment is not reliable durable multi-instance storage.
   - Use Redis/Valkey plus a worker only when background throughput requires it; keep the first release synchronous/low-volume.
   - Every action must be idempotent or have a deduplication key. Add bounded retries and timeouts.

5. **Windows companion agent**
   - Python service that polls an authenticated Render endpoint over outbound HTTPS.
   - Start with a strict allowlist: list/read files in one configured workspace, create/edit files after approval, and run tests in an isolated temporary sandbox.
   - No arbitrary PowerShell/CMD execution, privilege escalation, persistence tricks, credential access, or unrestricted filesystem access.
   - Require local user confirmation for sensitive desktop/browser interactions. Show a visible connected/running indicator and provide a stop control.
   - Store device credentials using Windows Credential Manager/DPAPI, not a plaintext config file.

6. **Android**
   - Make the existing responsive web app installable as a PWA.
   - Android sends commands to the Flask API; it does not execute PC actions itself.
   - Display task plan, exact requested actions, approval controls, live status, result, and cancel.
   - Browser permissions (microphone, notifications) must be optional and user-initiated.

## Proposed API (all endpoints require authentication and ownership checks)

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/devices/pair/start` | Create short-lived pairing code |
| POST | `/api/devices/pair/complete` | Pair the Windows agent |
| GET | `/api/devices` | List the user's paired devices |
| DELETE | `/api/devices/<id>` | Revoke a device |
| POST | `/api/automation/tasks` | Create and validate a task plan |
| GET | `/api/automation/tasks` | List task history |
| GET | `/api/automation/tasks/<id>` | Inspect plan, approval and result |
| POST | `/api/automation/tasks/<id>/approve` | Approve the exact displayed plan |
| POST | `/api/automation/tasks/<id>/cancel` | Cancel a queued/running task |
| GET | `/api/agent/next` | Agent polls for one eligible task |
| POST | `/api/agent/tasks/<id>/result` | Agent submits structured result |

The agent endpoints authenticate with the paired device token and may only receive tasks assigned to that device. The approval endpoint must bind approval to a hash/version of the exact plan so a changed plan cannot inherit old approval.

## Suggested code changes

| File | Change |
|---|---|
| `auth.py` (new) | Login/session, CSRF, password hashing or OIDC integration, role/ownership decorators |
| `automation_schemas.py` (new) | Strict schemas for plans, tools, task state and results |
| `automation_policy.py` (new) | Risk classification, allowlist, approval requirements and blocked actions |
| `automation_engine.py` (new) | LLM plan generation and schema validation; no direct execution |
| `automation_store.py` (new) | Database operations for tasks, devices, approvals and audit events |
| `agent_api.py` (new) | Authenticated pairing, polling, result and revocation routes |
| `windows_agent/` (new) | Outbound polling client and narrowly scoped local tools |
| `app.py` | Register blueprints; apply auth/ownership to existing chat, upload and project routes |
| `static/app.js` | Task plan preview, approval, progress, cancel and device status |
| `templates/index.html` | Automation console and paired-device UI |
| `static/manifest.webmanifest`, service worker (new) | Installable Android PWA |
| `requirements.txt` | Add only selected, pinned auth/schema/database dependencies |
| `tests/` (new) | Unit, API authorization, policy, path traversal, replay and integration tests |

## Delivery phases

### Phase 0 — Secure the existing app (required first)
- Add authentication and ownership boundaries to chats, uploads and project workspaces.
- Replace the public default secret with a required environment secret.
- Add CSRF protection, rate limiting, secure cookies, and production error handling.
- Migrate durable data from Render ephemeral disk to managed Postgres; keep uploads/artifacts in durable object storage or explicitly local-only storage.
- Add tests proving one user cannot access another user's IDs/files.

### Phase 1 — Safe automation MVP
- Task planner returns typed JSON actions from a small allowlist.
- Add task history, previews, approval and cancellation.
- Initially support only safe project-workspace actions: list files, read a file, create/edit a file after approval, and package a ZIP.
- No shell execution and no desktop control yet.

### Phase 2 — Windows companion
- Pair one Windows device, authenticate outbound polling, and show connection state.
- Add workspace-scoped file tools and isolated tests.
- Add local confirmation for browser/desktop actions; start with a small allowlist of sites/actions.

### Phase 3 — Android PWA
- Installable PWA, secure login, task submission, approvals, progress, notifications (opt-in), and device revocation.

### Phase 4 — Integrations
- GitHub integration using least-privilege scoped permissions and explicit approval for commits/PRs.
- Browser automation for allowlisted workflows.
- Calendar/email/business integrations only after separate provider authorization and confirmation UX.

## Acceptance criteria

- No automation endpoint works anonymously.
- A user cannot read, approve, cancel or receive another user's task.
- Agent token can be revoked immediately and cannot access general chat/admin APIs.
- The agent only receives allowlisted structured actions; no free-form command execution.
- A changed plan invalidates prior approval.
- All externally visible or destructive actions are gated.
- Agent disconnects safely on expired token, invalid signature, policy failure or server error.
- Task status and audit events survive service restart.
- Mobile UI can submit, approve, cancel and inspect a task without exposing secrets.

## Deployment note

Do not deploy an automation worker onto the current Render web service. It is a hosted Linux web process, not the user's Windows PC. The companion agent must run on Windows and connect outbound. Before enabling production automation, configure managed persistence and authentication secrets in Render's environment settings. Never commit secret values.
