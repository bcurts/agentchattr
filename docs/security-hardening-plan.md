# Security Hardening Plan

This document turns the current destructive review into an upstream-friendly
patch plan for `agentchattr`.

## Scope

The review found four priority areas:

1. Browser auth blast radius is too high.
2. SVG hat handling is not safe enough.
3. At least one write endpoint is unintentionally public.
4. Import/export reliability is not strong enough to trust for recovery.

The goal is to fix the highest-risk trust-boundary issues first, with minimal
architectural churn.

## Threat Model

`agentchattr` is local-first, but it still has meaningful attack surfaces:

- A browser session can trigger agents and mutate room state.
- Agent tools can persist content into the UI.
- API agents can forward room context to external endpoints.
- Localhost-only services are still reachable by other local processes.

The hardening target is:

- one compromised agent should not become browser compromise
- one compromised browser tab should not become unrestricted local control
- one broken archive import should not corrupt or block recovery paths

## Findings Summary

### 1. Stored XSS path through hats

Current flow:

- agent calls `chat_set_hat`
- server applies regex-based SVG cleanup
- frontend injects resulting SVG with `innerHTML`
- browser page also carries the session token

This is the highest-risk chain in the codebase.

### 2. Public write route by prefix mistake

The auth middleware treats `/api/roles` as public by prefix, which also exposes
`POST /api/roles/{agent}`.

### 3. Browser token has too much authority

The browser session token is injected into page JavaScript and used for a wide
set of write operations. If browser-side script execution is gained, room
control is effectively gained.

### 4. Archive path is not yet trustworthy

The current upstream tests already show a broken import path. Security work
should not rely on archive/recovery paths until that is fixed.

## Patch Order

## Phase 1: Close obvious auth gaps

Files:

- `app.py`

Changes:

- Replace prefix-based public route matching with exact route allowlisting.
- Keep `GET /api/roles` public only if that is actually intended.
- Require session auth for `POST /api/roles/{agent_name}`.
- Audit every write route under `/api/*` for accidental public exposure.

Success criteria:

- all write routes require either browser session auth or agent bearer auth
- middleware tests cover exact public routes and exact protected routes

## Phase 2: Remove dangerous SVG path

Files:

- `app.py`
- `mcp_bridge.py`
- `static/chat.js`

Recommended minimal fix:

- disable custom SVG hats entirely for now
- remove `.svg` from general uploads
- keep raster uploads only (`png`, `jpg`, `jpeg`, `gif`, `webp`, `bmp`)

Why:

- this is the smallest high-confidence fix
- regex sanitization is not a safe basis for scriptable SVG
- disabling the feature is safer than introducing a partial sanitizer and
  pretending the problem is solved

Possible later reintroduction:

- only via a dedicated safe rendering model
- likely rasterized or isolated-origin delivery

Success criteria:

- agents cannot persist executable markup into the main UI
- browser token is no longer exposed to agent-controlled DOM content

## Phase 3: Reduce browser-token blast radius

Files:

- `run.py`
- `app.py`
- `static/*.js`

Changes:

- move browser auth from a JS-visible token to an `HttpOnly` cookie
- add CSRF protection for browser write routes
- keep strict `Origin` validation for mutating requests
- move WebSocket auth to cookie-backed auth instead of query token
- stop printing the browser session token during normal startup

Notes:

- agent bearer tokens remain separate and should not be replaced by cookies
- browser auth and agent auth should remain clearly distinct

Success criteria:

- browser auth secret is not readable by frontend JavaScript
- a successful DOM XSS no longer automatically grants room-control bearer auth

## Phase 4: Restrict local action endpoints

Files:

- `app.py`

Changes:

- gate `/api/open-path` behind config or disable by default
- optionally restrict allowed paths to:
  - repo root
  - configured data dir
  - configured uploads dir

Success criteria:

- browser compromise does not automatically yield arbitrary desktop launcher use

## Phase 5: Repair archive trustworthiness

Files:

- `archive.py`
- `store.py`
- `tests/test_archive_feature.py`

Changes:

- fix current broken import path
- add tests for:
  - malformed zip
  - duplicate UIDs
  - oversized import
  - reply-link reconstruction
  - attachment handling

Success criteria:

- `pytest -q` is green
- archive import/export can be trusted as an operational recovery path

## Phase 6: Frontend hardening follow-up

Files:

- `static/chat.js`
- `static/jobs.js`
- `static/sessions.js`
- `static/rules-panel.js`
- `static/index.html`

Changes:

- reduce `innerHTML` usage where practical
- remove inline event handlers over time
- prepare the frontend for a stricter CSP

This phase is explicitly later because it is larger and less surgical.

## PR Slices

Recommended upstream sequence:

1. `fix: protect role mutation endpoints`
2. `security: disable unsafe svg hats and svg uploads`
3. `security: move browser auth to httponly cookie`
4. `security: restrict open-path endpoint`
5. `fix: repair archive import path and add coverage`
6. `hardening: reduce inline html/script surface`

Each PR should:

- include one clear security or integrity objective
- include focused tests
- avoid bundling unrelated cleanup

## Explicit Non-Goals

Not part of the first hardening series:

- redesigning the whole MCP model
- replacing tmux wrappers
- introducing a new permissions system for every tool
- large UI rewrites

The first series should reduce risk without changing the product shape.

