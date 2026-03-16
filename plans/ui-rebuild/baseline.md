# Phase 0: Audit & Baseline Report (CORRECTED)

**Date:** 2026-03-14
**Status:** ✅ COMPLETE - All checklist items completed, contracts synchronized with actual backend

---

## 1. Frontend Files Audit

### Current UI File Sizes
| File | Size | Type | Purpose |
|------|------|------|---------|
| `static/index.html` | 19 KB | HTML | Main entry point |
| `static/chat.js` | 123 KB | JS | Chat messages, input, rendering |
| `static/jobs.js` | 83 KB | JS | Jobs board, threading |
| `static/sessions.js` | 38 KB | JS | Session management |
| `static/rules-panel.js` | 23 KB | JS | Rules UI (active/draft/archive) |
| `static/channels.js` | 13 KB | JS | Channel management |
| `static/core.js` | 1.2 KB | JS | Session token initialization |
| `static/store.js` | 1.4 KB | JS | Basic state store |
| `static/style.css` | 71 KB | CSS | Core styles (dark theme) |
| `static/jobs.css` | 27 KB | CSS | Jobs styling |
| `static/sessions.css` | 15 KB | CSS | Sessions styling |
| **TOTAL** | **414 KB** | — | Uncompressed |

### Estimated Gzipped Size
- Current uncompressed: ~414 KB
- Estimated gzipped: ~120-140 KB (typical 30% ratio)
- **Target for new UI:** <50 KB JS gzipped

---

## 2. Backend API Audit

### REST Endpoints Count
- **Total endpoints:** 46 (not 44 as initially counted)
- **Categories:**
  - Messages: `/api/messages`, `/api/send`, `/api/upload`
  - Jobs: `/api/jobs`, `/api/jobs/{id}/messages`, `/api/jobs/{id}` (PATCH), `/api/jobs/reorder`
  - Settings: `/api/settings`, `/api/status`
  - Schedules: `/api/schedules`, `/api/schedules/{id}/toggle`
  - Agents: `/api/register`, `/api/deregister`, `/api/label`, `/api/heartbeat`, `/api/roles`
  - Hats: `/api/hat/{agent_name}`
  - Rules: `/api/rules`, `/api/rules/active`, `/api/rules/agent_sync`
  - Sessions: `/api/sessions/*`
  - Utils: `/api/trigger-agent`, `/api/platform`, `/api/version_check`

### Key Corrections Made
- ✅ Added missing endpoints: `PATCH /api/schedules/{id}/toggle`, `PATCH /api/jobs/{id}`
- ✅ Corrected response shapes (most return unwrapped data, not `{data}` wrapper)
- ✅ Job IDs are **numbers**, not strings
- ✅ Message IDs are **numbers**, not strings
- ✅ Job statuses are `'open'`, `'done'`, `'archived'` (not `'todo'`, `'active'`, `'done'`)
- ✅ Rule statuses are `'active'`, `'pending'`, `'archived'` (not `'draft'`, `'active'`, `'archived'`)

### WebSocket Contract (Corrected)
- **Event Structure:** `{type, action?, data}`
- **Snapshot Events (on connect):** `settings`, `agents`, `base_colors`, `todos`, `rules`, `hats`, `jobs`, `schedules`, `pending_instance`, `status`
- **Dynamic Broadcasts:** `message`, `job`, `rule`, `schedule`, `session`, `agent`, `instance`
- **Total events:** 18+ distinct types with sub-actions

---

## 3. Phase 0 Completion Checklist

- [x] Run audit on current frontend files
- [x] Count and document all REST endpoints (46 total)
- [x] List all WebSocket events (18+ types)
- [x] Document token injection flow
- [x] Create `web/` directory scaffold
- [x] Set up Vite + React 18 + TypeScript
- [x] Install core dependencies (Zustand, @dnd-kit, etc.)
- [x] Configure Vite (port 8888, proxy, build to `./dist/`)
- [x] Create directory structure in `web/src/`
- [x] Define TypeScript types (synchronized with actual backend)
- [x] Create Zustand stores (chat, agents, jobs)
- [x] Verify build pipeline (`npm run build` passes)
- [x] Create comprehensive API contract doc (`api-contract.md`)
- [x] **[CORRECTED]** Sync API contract with actual backend code
- [x] **[CORRECTED]** Update TypeScript types to match actual backend
- [x] **[CORRECTED]** Update Zustand stores to use correct types

---

## 4. Deliverables Summary

### Files Created/Updated
1. ✅ `plans/ui-rebuild/baseline.md` — This file (audit report)
2. ✅ `plans/ui-rebuild/api-contract.md` — 46 REST endpoints + WebSocket events (corrected)
3. ✅ `web/` workspace — Full React + Vite + TypeScript scaffold
4. ✅ `web/src/types/index.ts` — Core types (corrected per backend)
5. ✅ `web/src/stores/chatStore.ts` — Chat state management (corrected)
6. ✅ `web/src/stores/agentStore.ts` — Agent presence
7. ✅ `web/src/stores/jobsStore.ts` — Jobs board (corrected)

### Build Status
```
✓ TypeScript compilation passes
✓ Vite build successful
✓ Bundle: 60.66 KB gzipped (React scaffold only)
✓ Port 8888 configured and tested
✓ Proxy to backend (:8300) configured
```

---

## 5. Key Corrections Made (vs. Initial Phase 0)

| Issue | Initial | Corrected |
|-------|---------|-----------|
| REST endpoints | 44 | 46 |
| Job IDs | `string` | `number` ✓ |
| Message IDs | `string` | `number` ✓ |
| Job statuses | `'todo'\|'active'\|'done'` | `'open'\|'done'\|'archived'` ✓ |
| Rule statuses | `'draft'\|'active'\|'archived'` | `'active'\|'pending'\|'archived'` ✓ |
| WS events | Named events (`job_created`, etc.) | Structured events (`{type, action, data}`) ✓ |
| Response format | Wrapped (`{data, success}`) | Unwrapped + `ok` field ✓ |

---

## 6. Next Steps

✅ **Phase 0 ready for @codex final review**

- API contract synchronized with actual backend code
- TypeScript types corrected and matching backend reality
- Build pipeline verified
- Ready to proceed to **Phase 1: Core Chat UI**

---

**Status:** ✅ COMPLETE (after corrections)
**Blockers:** None
**Ready for Phase 1:** Yes


