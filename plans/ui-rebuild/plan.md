# UI Rebuild Plan for AgentChattr

**Version:** 2.3 (Final)
**Date:** 2026-03-14
**Status:** ✅ Codex reviewed → Claude updated → Duong decisions applied → Duong serving architecture refined → **Ready for Phase 0**

### Changelog
| Ver | Author | Summary |
|-----|--------|---------|
| 1.0 | Claude | Initial draft |
| 2.0 | Claude | Incorporated Codex review: added Phase 0, module mapping, backend contract, replaced deprecated libs, realistic rollout strategy, MVP/post-MVP split |
| 2.1 | Claude | Fixed rollout vs "zero backend changes" contradiction per Codex final note. Clarified: API contract frozen, but ~10-line serving-layer tweak needed for `?ui=legacy` fallback |
| 2.2 | Claude | Applied Duong's decisions: full TypeScript, Unit+E2E testing, ship-per-phase timeline, CI after MVP |
| 2.3 | Claude | Applied Duong's serving architecture refinement: new UI on port 8888 (dev), separate folder (web/dist/), easy comparison with legacy |

---

## Executive Summary

Rebuild the agentchattr chat UI from vanilla JS (12.5K LOC across 7 files) to React + Vite + Zustand. Mobile-first, dark theme.

**Serving Architecture (per Duong's feedback):**
- New React UI runs on **port 8888** (Vite dev server during development)
- Builds to **`web/dist/`** folder (separate from legacy `/static/`)
- Legacy UI stays at existing port/location for easy side-by-side comparison
- Post-MVP rollout: decide final merge into `/static/` or keep parallel

**Backend Contract:** API contract (55 REST endpoints, 30+ WS events, session-token auth) stays frozen — no payload/event changes. Zero backend changes required for core feature parity.

---

## Current State Analysis

### Frontend Architecture
- **Single Page App:** Vanilla JS, no framework
- **CSS:** Monolithic style.css (72KB), hardcoded dark theme only
- **Build:** None (direct browser script loading)
- **Structure:**
  - `static/index.html` (19KB) - single HTML file
  - `static/chat.js` (126KB) - all chat logic
  - `static/jobs.js` (85KB) - job management
  - `static/sessions.js` (39KB) - session orchestration
  - `static/rules-panel.js` (23KB) - rules UI
  - `static/channels.js` (13KB) - channel management
  - `static/style.css` (72KB), `jobs.css` (27KB), `sessions.css` (15KB)
  - No component reuse, tight coupling

### Key Features to Preserve
1. Real-time WebSocket messaging
2. Agent status pills with activity indicators
3. @mention autocomplete with agent search
4. Job/task management with threading
5. Rules engine UI
6. Session orchestration (code review, debate, etc.)
7. Pinned messages with todo/done states
8. Channel management and switching
9. Settings panel (font, contrast, notifications)
10. Voice typing via Web Speech API
11. Message scheduling
12. Drag-and-drop file uploads
13. Markdown rendering (via marked.js)

---

## Tech Stack Decision

### Chosen Stack
1. **React 18+** - Component model, better state management
2. **Vite** - Modern bundler (10-100x faster than Webpack)
3. **Zustand** - Minimal, simple state management (no Redux boilerplate)
4. **TailwindCSS** - Utility-first CSS, smaller bundle, faster iterations
5. **shadcn/ui** - Pre-built accessible components (dialog, dropdown, etc.)
6. **TypeScript** (confirmed by Duong) - Strict types from day 1
7. **Vitest** + **Playwright** - Unit tests + E2E (confirmed by Duong)

### Rationale
- **React** → Enterprise-grade ecosystem, huge community, easier hiring
- **Vite** → 10-100x faster dev server, better HMR than Create React App
- **Zustand** → Simpler than Redux/Context, avoids prop drilling, <1KB bundle
- **TailwindCSS** → Smaller final CSS, mobile-first utilities, dark mode baked in
- **shadcn** → Copy-paste components, full control, avoids heavy component libs
- **TypeScript** → Critical for large codebases; reduces runtime errors by ~15-20%

### Not Chosen (Why)
- ❌ **Vue** - Not chosen; React ecosystem larger
- ❌ **Next.js** - Not needed; static SPA, no SSR required
- ❌ **Redux** - Too verbose; Zustand scales better
- ❌ **styled-components** - TailwindCSS faster; smaller bundle
- ❌ **Material-UI** - Heavy (50KB+); shadcn more lightweight

---

## Architecture Design

### Directory Structure
```
src/
├── components/              # Reusable components
│   ├── Chat/
│   │   ├── ChatMessage.tsx
│   │   ├── MessageList.tsx
│   │   ├── InputArea.tsx
│   │   └── ChatContainer.tsx
│   ├── Jobs/
│   │   ├── JobsList.tsx
│   │   ├── JobDetail.tsx
│   │   └── JobsPanel.tsx
│   ├── Channels/
│   │   ├── ChannelTabs.tsx
│   │   └── ChannelList.tsx
│   ├── Agents/
│   │   ├── AgentStatus.tsx
│   │   ├── AgentPill.tsx
│   │   └── MentionAutocomplete.tsx
│   ├── Settings/
│   │   ├── SettingsPanel.tsx
│   │   └── SoundSettings.tsx
│   ├── Common/
│   │   ├── Header.tsx
│   │   ├── Footer.tsx
│   │   ├── Modal.tsx
│   │   └── Spinner.tsx
│   └── Sessions/
│       ├── SessionBar.tsx
│       └── SessionLauncher.tsx
├── stores/                  # Zustand stores
│   ├── chatStore.ts         # Messages, current channel
│   ├── jobsStore.ts         # Jobs and threading
│   ├── agentStore.ts        # Agent presence, status
│   ├── settingsStore.ts     # User prefs, notifications
│   ├── uiStore.ts           # Panel visibility, modals
│   └── sessionStore.ts      # Active session state
├── hooks/                   # Custom React hooks
│   ├── useWebSocket.ts      # WS connection management
│   ├── useMentionAutocomplete.ts
│   ├── useLocalStorage.ts
│   └── useNotification.ts
├── services/                # API/utility functions
│   ├── api.ts              # REST calls
│   ├── markdown.ts         # Markdown parsing
│   └── scheduling.ts       # Schedule logic
├── styles/
│   ├── globals.css         # TailwindCSS imports
│   ├── dark.css            # Dark theme (if needed beyond Tailwind)
│   └── animations.css      # Smooth transitions
├── types/                   # TypeScript types
│   ├── chat.ts
│   ├── job.ts
│   ├── agent.ts
│   ├── rules.ts
│   └── index.ts
├── utils/                   # Helpers
│   ├── formatting.ts       # Date, time, formatting
│   ├── validators.ts       # Input validation
│   └── storage.ts          # LocalStorage wrappers
├── App.tsx                  # Root component
├── main.tsx                 # Entry point
└── vite-env.d.ts           # Vite type definitions

public/
├── logo.png
├── favicon.ico
└── sounds/                  # Audio files

tests/
├── components/
├── stores/
├── hooks/
└── utils/

.env.example                # Environment template
tailwind.config.ts          # Tailwind customization
tsconfig.json               # TypeScript config
vite.config.ts              # Vite bundler config
vitest.config.ts            # Test runner config
package.json
```

### Component Breakdown

#### 1. Chat Module (`components/Chat/`)
- **ChatMessage** → Single message with actions (delete, reply, pin)
  - Props: `message`, `onReply`, `onPin`, `onDelete`
  - Features: Markdown render, copy button, image inline view
- **MessageList** → Scrollable message history
  - Features: Virtual scrolling (for performance), date dividers, typing indicator
- **InputArea** → Message composition
  - Features: @mention autocomplete, emoji support, Shift+Enter for newline
- **ChatContainer** → Orchestrates chat UI + WS connection

#### 2. Jobs Module (`components/Jobs/`)
- **JobsList** → Board view (To Do → Active → Closed)
  - Kanban-style, drag-to-reorder within status groups
- **JobDetail** → Threaded conversation view
  - Features: Reply threading, in-thread replies, status transitions
- **JobsPanel** → Sidebar containing JobsList or JobDetail

#### 3. Agents Module (`components/Agents/`)
- **AgentStatus** → Status pills (online, working, offline)
  - Real-time activity indicator (spinning border)
  - Click to rename (for multi-instance)
- **MentionAutocomplete** → Dropdown with agent/user search
  - Keyboard nav (arrow keys), Enter to insert

#### 4. Settings & Panels
- **SettingsPanel** → Collapsible settings (font, contrast, notifications)
  - Per-agent notification sounds
  - Persists to localStorage
- **RulesPanel** → Rules management (active, drafts, archive)
- **PinsPanel** → Pinned messages with todo/done filter

#### 5. Sessions Module
- **SessionBar** → Progress indicator (current phase, next agent)
- **SessionLauncher** → Modal to start/design sessions

### State Management (Zustand Stores)

#### `chatStore.ts`
```typescript
interface ChatState {
  messages: Message[];
  currentChannel: string;
  channels: Channel[];
  selectedReplyTarget: Message | null;

  // Actions
  addMessage: (msg: Message) => void;
  setChannel: (id: string) => void;
  deleteMessage: (id: string) => void;
  pinMessage: (id: string) => void;
  // ...
}
```

#### `agentStore.ts`
```typescript
interface AgentState {
  agents: Agent[];
  agentStatus: Record<string, 'online' | 'working' | 'offline'>;

  // Actions
  registerAgent: (agent: Agent) => void;
  setAgentStatus: (id: string, status: string) => void;
  // ...
}
```

#### `jobsStore.ts`
```typescript
interface JobsState {
  jobs: Job[];
  activeJob: Job | null;

  // Actions
  addJob: (job: Job) => void;
  updateJobStatus: (id: string, status: string) => void;
  // ...
}
```

### WebSocket Integration
- Centralized `useWebSocket` hook
- Auto-reconnect with exponential backoff
- Message buffering during disconnection
- Heartbeat to detect stale connections

### Styling Strategy

#### Dark Theme (Tailwind + shadcn)
- Tailwind's `dark:` prefix for dark mode utilities
- shadcn components use CSS variables (easy to customize)
- Config in `tailwind.config.ts`:
  ```typescript
  darkMode: 'class', // Toggle via document.documentElement.classList.add('dark')
  ```

#### Mobile-First Approach
- Start with mobile breakpoints, scale up
- TailwindCSS breakpoints: `sm:`, `md:`, `lg:`, `xl:`
- Example responsive layout:
  ```jsx
  <div className="flex flex-col md:flex-row gap-2">
    <main className="w-full md:flex-1" />
    <aside className="w-full md:w-64 md:border-l" />
  </div>
  ```

---

## Module Mapping (Old → New)

> Added per Codex feedback #2: explicit mapping prevents missed features.

| Old File (vanilla) | LOC | React Target | Priority |
|---------------------|-----|-------------|----------|
| `chat.js` | 3,057 | `Chat/*`, `Agents/*`, `hooks/useWebSocket` | MVP |
| `jobs.js` | 2,122 | `Jobs/*`, `stores/jobsStore` | MVP |
| `sessions.js` | 935 | `Sessions/*`, `stores/sessionStore` | MVP |
| `rules-panel.js` | 588 | `Rules/*`, `stores/rulesStore` | MVP |
| `channels.js` | 351 | `Channels/*` (merged into chatStore) | MVP |
| `store.js` | 54 | Replaced by Zustand stores | MVP |
| `core.js` | 45 | `services/api.ts` (token init) | MVP |
| `style.css` | 3,383 | TailwindCSS utilities | MVP |
| `jobs.css` | 1,285 | TailwindCSS (co-located) | MVP |
| `sessions.css` | 698 | TailwindCSS (co-located) | MVP |

### Features: MVP vs Post-MVP

> Added per Codex feedback #7: clear scope boundary.

**MVP Parity (must-ship):**
- Chat send/receive + markdown + images
- Agent status pills + activity indicators
- @mention autocomplete + toggles
- Channels (switch, create, unread badges)
- Jobs (list, detail thread, status transitions)
- Rules panel (active/drafts/archive, drag)
- Pins (todo/done cycle)
- Settings (font, contrast, history, loop guard, sounds)
- Message scheduling
- Voice typing
- Sessions (bar, launcher, templates)
- Hats, role labels, multi-instance naming
- Image upload (paste/drop)
- Slash commands autocomplete

**Post-MVP (after parity stable):**
- Virtual scrolling (react-window)
- Deep code splitting / lazy loading
- 50%+ test coverage
- Full docs (COMPONENTS.md, DEPLOYMENT.md)
- Lighthouse optimization pass
- E2E tests (Playwright)

---

## Backend Contract (Frozen)

> Added per Codex feedback #3 & #8: document what stays unchanged.

### Auth
- `X-Session-Token` header on all non-public requests
- Token injected into index.html by FastAPI at `GET /`
- Same-origin: no CORS needed (React build output served from `static/`)

### Data Flow Architecture
```
┌─────────────────────────────────────────────────┐
│  React App                                       │
│                                                   │
│  ┌─────────────┐   ┌──────────────────────────┐  │
│  │ Bootstrap    │   │ WebSocket Event Ingestion │  │
│  │ (REST GET)   │   │ (real-time updates)       │  │
│  │              │   │                            │  │
│  │ /api/history │   │ message, delete, agent_*,  │  │
│  │ /api/jobs    │   │ job_*, rule_*, session_*,  │  │
│  │ /api/rules/* │   │ todo_*, schedule_*,        │  │
│  │ /api/agents  │   │ typing, rename, settings   │  │
│  └──────┬───────┘   └──────────┬─────────────────┘  │
│         │                      │                    │
│         ▼                      ▼                    │
│  ┌─────────────────────────────────────────────┐  │
│  │         Zustand Stores (single source)       │  │
│  └─────────────────────────────────────────────┘  │
│         │                                          │
│         ▼                                          │
│  ┌─────────────────────────────────────────────┐  │
│  │  Mutations (REST POST/PUT/DELETE)            │  │
│  │  → optimistic update store                   │  │
│  │  → fire REST call                            │  │
│  │  → rollback on error                         │  │
│  └─────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

**Key WS events to handle (30+):** message, delete, agent_joined, agent_left, agent_status, agent_renamed, hat_update, job_created, job_updated, job_deleted, job_reordered, rule_proposed, rule_activated, rule_updated, rules_refreshed, session_started, session_phase, session_ended, todo_update, schedule_created, schedule_deleted, schedule_toggled, settings, typing, identity_claim, channel_created, channel_renamed, channel_deleted, etc.

**Key REST endpoints for bootstrap:**
- `GET /api/history?channel=X` — initial messages
- `GET /api/agents` — online agents
- `GET /api/rules/list` — active rules
- `GET /api/jobs` — all jobs
- `GET /api/sessions/active` — current session
- `GET /api/schedules` — active schedules

**API contract frozen — zero changes to endpoints, payloads, or WS events.** The only backend modification is a small serving-layer tweak (~10 lines in `app.py`) to support `?ui=legacy` fallback during rollout. This routes `GET /` to either `static/` (new) or `static-legacy/` (old) based on a query param. Once rollout completes and legacy is removed, this tweak is also removed.

---

## Workspace & Build Strategy

> Added per Codex feedback #1: resolve integration architecture upfront.
> Updated per Duong feedback (2026-03-14): port 8888 for dev, separate `web/dist/` folder.

**Chosen approach:** `web/` workspace inside repo, Vite builds to `web/dist/` (separate folder for easy comparison).

```
agentchattr/
├── web/                    # ← NEW: React frontend workspace
│   ├── src/
│   ├── public/
│   ├── package.json
│   ├── vite.config.ts      # outDir: './dist'
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   └── dist/               # ← Vite build output (separate folder)
│       ├── index.html      # built by Vite
│       ├── assets/         # hashed JS/CSS chunks
│       ├── favicon.ico
│       ├── logo.png
│       └── sounds/
├── static/                 # ← LEGACY: Old files stay here unchanged
│   ├── index.html (old)
│   ├── chat.js
│   ├── jobs.js
│   └── ...
├── app.py                  # unchanged — serves static/ as before
└── ...
```

**Why this approach:**
- Separate folder (`web/dist/` vs `static/`) makes it easy to compare old vs. new
- Dev server: `vite dev` on **port 8888** (standalone, no port collision)
- Build output in `web/dist/` (clear separation, easy to version/deploy)
- FastAPI serves old UI from `static/` unchanged
- Post-MVP decision: merge `web/dist/` into `static/` or keep parallel serving

**Development Flow:**
```bash
# Terminal 1: Run Python backend (existing, unchanged)
python -m uvicorn app:app --reload --port 5000

# Terminal 2: Run Vite dev server on port 8888
cd web && npm run dev   # Vite dev server at localhost:8888
```

**Vite config sketch:**
```typescript
export default defineConfig({
  root: '.',
  build: { outDir: './dist', emptyOutDir: true },
  server: {
    port: 8888,
    proxy: {
      '/api': 'http://localhost:5000',
      '/ws': { target: 'ws://localhost:5000', ws: true },
      '/uploads': 'http://localhost:5000',
    }
  }
})
```

**Production/Rollout (Phase 9+):** Decision point:
- Option A: Copy `web/dist/*` into `static/`, FastAPI serves same-origin (after testing at port 8888)
- Option B: Keep `web/dist/` separate, add a route in `app.py` to serve from `web/dist/` at a path like `/ui-new/`
- TBD after MVP is stable

---

## Implementation Plan (Phases)

### Phase 0: Audit & Baseline (3 days)

> Added per Codex feedback #6: measure before committing KPIs.

**Goal:** Establish measurable baseline, finalize contract mapping.

**Tasks:**
1. Run Lighthouse on current UI → record scores
2. Measure current load time (DevTools Network)
3. Profile chat render time with 100/500/1000 messages
4. Audit all 55 REST endpoints used by frontend (mark which ones)
5. Audit all 30+ WS events consumed by frontend
6. Document token injection flow in `GET /`
7. Create `web/` directory scaffold (empty)

**Deliverables:**
- `plans/ui-rebuild/baseline.md` — measured performance numbers
- `plans/ui-rebuild/api-contract.md` — endpoint/event checklist

**Acceptance:**
- ✅ Baseline numbers recorded (not estimated)
- ✅ Every REST + WS interaction catalogued

---

### Phase 1: Setup & Foundation (Week 1)
**Goal:** Project scaffolding, base structure, zero features

**Tasks:**
1. Create `web/` workspace inside repo:
   ```bash
   cd agentchattr
   mkdir web && cd web
   npm create vite@latest . -- --template react-ts
   npm install
   ```
2. Install dependencies:
   - `zustand`, `marked`, `lucide-react`
   - TailwindCSS + shadcn/ui setup
   - `@dnd-kit/core` + `@dnd-kit/sortable` (replaces deprecated react-beautiful-dnd)
3. Configure Vite:
   - `outDir: './dist'` (build to `web/dist/`, not `../static/`)
   - `port: 8888` for dev server
   - proxy to backend on `:5000` (or existing port)
4. Configure Tailwind (dark mode: `class`, mobile-first breakpoints)
5. Set up folder structure per Architecture Design section
6. Create TypeScript types (`src/types/`) from backend contract
7. Keep legacy `static/` folder untouched (for comparison)
8. Test:
   - `cd web && npm run dev` starts dev server on port 8888
   - Can see React app at `localhost:8888`
   - API calls proxy to backend correctly

**Deliverables:**
- `web/` workspace with empty component tree
- Vite dev server on **port 8888**, proxying to backend
- TypeScript compiling, Tailwind rendering
- Legacy `static/` folder intact for comparison

**Acceptance:**
- ✅ `cd web && npm run dev` starts dev server
- ✅ `localhost:8888` shows React app
- ✅ API calls proxy to backend correctly
- ✅ `npm run build` outputs to `web/dist/`
- ✅ Can compare old UI (`localhost:5000`) and new UI (`localhost:8888`) side-by-side

---

### Phase 2: Core Chat UI (Weeks 2-3)
**Goal:** Message rendering, real-time updates, input

**Tasks:**
1. Wire `chatStore` to backend WebSocket
   - Load historical messages
   - Listen for new messages in real-time
   - Handle disconnection gracefully
2. Build `ChatMessage` component
   - Render markdown with marked.js
   - Copy button on hover
   - Inline image preview
   - @mention pills (colored, clickable)
3. Build `MessageList` component
   - Scroll to latest on new message
   - Date dividers between days
   - Typing indicator at bottom
4. Build `InputArea` component
   - Basic message send
   - @mention autocomplete (dropdown)
   - Shift+Enter for newline, Enter to send
5. Connect to backend `/api/messages` endpoints
6. Styling (dark theme, mobile responsive)

**Deliverables:**
- Messages display in real-time
- Can send messages
- @mention autocomplete works
- Mobile-responsive layout

**Acceptance:**
- ✅ Receive + display messages
- ✅ Send message via input
- ✅ @mention dropdown on `@` key
- ✅ No layout shift on mobile

---

### Phase 3: Agent Status & Presence (Week 3)
**Goal:** Online indicators, activity animations

**Tasks:**
1. Build `AgentStatus` component
   - Status pills (online/working/offline)
   - Activity spinner (CSS animation)
   - Color per agent (from backend config)
2. Wire `agentStore` to `/api/agents` (heartbeat)
3. Build `MentionAutocomplete`
   - Search agents by name
   - Keyboard nav (arrow keys, enter)
   - Pre-selection toggles below input
4. Styling (color-coded pills, smooth transitions)

**Deliverables:**
- Agent pills render with current status
- Activity spinner animates
- Agent list searchable via @mention

**Acceptance:**
- ✅ Status pills show online/offline
- ✅ Activity indicator spins while agent working
- ✅ @mention search filters agents

---

### Phase 4: Channels (Week 4)
**Goal:** Multi-channel support, switching

**Tasks:**
1. Build `ChannelTabs` component
   - Tab bar with channel names
   - Active indicator
   - Unread badge
   - Add button for new channel
2. Build channel creation dialog (shadcn Dialog)
3. Wire to backend `/api/channels` endpoints
4. Update `MessageList` to filter by channel
5. Persist active channel to localStorage
6. Update WS subscription on channel change

**Deliverables:**
- Channel tabs render correctly
- Can switch between channels
- Messages filtered per channel
- New channel creation works

**Acceptance:**
- ✅ Switch channel, messages reload
- ✅ Unread badges appear
- ✅ Create new channel via dialog

---

### Phase 5: Jobs & Threading (Weeks 5-6)
**Goal:** Job board + threaded conversations

**Tasks:**
1. Build `JobsList` component
   - Kanban board (To Do / Active / Done columns)
   - Drag-to-reorder within column (`@dnd-kit/core` + `@dnd-kit/sortable`)
   - Click to open job detail
2. Build `JobDetail` component
   - Threaded conversation view
   - Reply UI (reply-to indicator)
   - Status transitions (button bar)
3. Wire `jobsStore` to backend `/api/jobs` endpoints
4. Implement reply threading logic
5. Build job creation modal
6. Styling (kanban cards, thread bubbles)

**Deliverables:**
- Job board displays all jobs
- Can drag jobs between columns
- Open job opens threaded view
- Can reply within thread

**Acceptance:**
- ✅ Kanban board updates in real-time
- ✅ Open job → threaded view loads
- ✅ Reply message appears in thread
- ✅ Drag job → status updates

---

### Phase 6: Settings & Panels (Week 6)
**Goal:** User prefs, notifications, rules UI

**Tasks:**
1. Build `SettingsPanel` component
   - Font selection (mono/sans)
   - Contrast mode (normal/high)
   - History limit slider
   - Notification sounds (per-agent dropdowns)
   - Loop guard setting
2. Build per-agent sound selector
   - Dropdown with 7 sound options + "None"
   - Preview button to test sound
3. Build `RulesPanel` component
   - Active rules list
   - Drafts section
   - Archive section
   - Drag between sections
   - Edit/delete per rule
4. Build `PinsPanel` component
   - Open (todo) pins
   - Done (completed) pins
   - Filter toggle

**Deliverables:**
- Settings persist to localStorage
- Rules panel fully functional
- Pins panel shows open/done

**Acceptance:**
- ✅ Change font → UI updates
- ✅ Settings reload on refresh
- ✅ Sound plays when message arrives

---

### Phase 7: Sessions & Advanced (Week 7)
**Goal:** Session orchestration, session launcher

**Tasks:**
1. Build `SessionBar` component
   - Current session progress
   - Phase indicator
   - Agent awaiting response
   - End session button
2. Build `SessionLauncher` component
   - Modal with session templates
   - Agent role assignment
   - Start button
3. Wire `sessionStore` to backend `/api/sessions` endpoints
4. Implement phase advancement logic
5. Update WS to broadcast session state

**Deliverables:**
- Session launcher modal works
- Start session → bar appears
- Phase advancement shows in bar

**Acceptance:**
- ✅ Select template → agent picker
- ✅ Start session → visible in bar
- ✅ Phase transitions trigger next agent

---

### Phase 8: Polish & Optimization (Week 8)
**Goal:** Performance, accessibility, dark theme finalize

**Tasks:**
1. Virtual scrolling for `MessageList` (react-window)
   - Handles 10k+ messages without lag
2. Code-split components (lazy load panels)
3. Audit a11y (keyboard nav, color contrast, ARIA labels)
4. Dark theme polish
   - Verify all colors meet WCAG AA
   - Tailwind dark mode fully applied
5. Mobile-first validation (test on devices)
   - Touch interactions smooth
   - No horizontal scroll
   - Text readable at all sizes
6. Bundle analysis + tree-shaking
   - Target <300KB main JS (gzip)
7. Add unit tests (Vitest)
   - 50%+ coverage on critical paths (chat, stores)

**Deliverables:**
- Performance audit passing
- a11y audit passing (axe-core)
- Mobile responsiveness verified
- Unit tests in place
- Bundle <300KB gzipped

**Acceptance:**
- ✅ Lighthouse score >90
- ✅ No axe violations
- ✅ Works on iPhone 12 and iPad
- ✅ Tests pass locally

---

### Phase 9: Integration & Migration (Week 9)
**Goal:** Integrate built UI, rollout strategy

**Tasks:**
1. Run `npm run build` in `web/` → output to `web/dist/`
   - Built files ready for integration
2. Verify session-token injection in built `web/dist/index.html`
   - Backend will inject token at runtime; ensure Vite build preserves placeholder for injection
3. Decision point for final deployment (TBD based on testing at port 8888):
   - **Option A:** Copy `web/dist/*` into `static/`, FastAPI serves from `static/` (same-origin)
   - **Option B:** Keep separate, serve from `web/dist/` at a new route in `app.py`
   - Document decision and rationale
4. Full integration testing:
   - All 55 REST endpoints called correctly
   - All 30+ WS events handled
   - Session-token auth working
   - Backward compatibility: wrappers/agents unaffected
5. Verify existing agent wrappers still function (no contract break)
6. Internal dogfooding:
   - Developers test new UI from `web/dist/` (or new route if Option B)
   - Verify feature parity with legacy UI in `static/`
   - Collect feedback, fix bugs

**Deliverables:**
- New UI built and integrated (ready for production path)
- Decision documented: merge into `static/` or keep parallel
- All integration tests passing
- Internal dogfooding underway

**Acceptance:**
- ✅ Chat send/receive works
- ✅ Jobs create/update works
- ✅ Agents appear in real-time
- ✅ All wrapper scripts still function
- ✅ No console errors
- ✅ Feature parity verified (checked against legacy UI)

---

### Phase 10: Documentation & Handoff (Week 10)
**Goal:** Developer docs, deployment guide

**Tasks:**
1. Write component API docs (props, usage)
2. Write store usage guide (Zustand patterns)
3. Deployment guide (build, serve, Docker)
4. Contribution guidelines (adding features)
5. Known issues + future TODOs

**Deliverables:**
- `docs/UI_ARCHITECTURE.md`
- `docs/COMPONENTS.md`
- `docs/DEPLOYMENT.md`
- `CONTRIBUTING.md`

---

## Migration Strategy

> Updated per Codex feedback #5: realistic rollout, no A/B infrastructure needed.
> Updated per Duong feedback (2026-03-14): separate folders + port 8888 for easier side-by-side comparison.

### Development & Testing
- Legacy UI: served at original location/port (e.g., `localhost:5000`)
- New UI: dev server on **port 8888** (easy to compare both simultaneously)
- Developers can toggle between them while building

### Vertical Slice Migration Order
```
Phase 0: Audit + Baseline
Phase 1: App Shell (header, footer, layout) on port 8888
Phase 2: Chat (messages, input, markdown)
Phase 3: Agents (status pills, mentions)
Phase 4: Channels (tabs, switching, unread)
Phase 5: Jobs (board, threads)
Phase 6: Settings + Rules + Pins
Phase 7: Sessions
Phase 8: Polish
Phase 9: Integration + decision on final deployment
Phase 10: Docs
```

### Rollout Plan (Post-MVP, Phase 9+)
1. **Build to `web/dist/`** — tested thoroughly at `localhost:8888`
2. **Internal dogfooding:** Developers use built version, verify feature parity
3. **Decide final deployment:**
   - Option A: Merge `web/dist/*` into `static/`, deprecate legacy files
   - Option B: Keep in `web/dist/`, serve at separate route (e.g., `/ui-new/`)
4. **Gradual rollout** (after decision):
   - Default to new, legacy as fallback (if Option A)
   - Or maintain both in parallel (if Option B)
5. **Legacy removal:** After stable (2+ weeks), remove old files

### Rollback Plan
- `static/` folder preserved unchanged during development
- `web/dist/` is build output — git tracks source (`web/src/`)
- Easy revert: use previous commit or redeploy from git history

---

## Performance Targets

> Per Codex feedback #6: "Current" column values are estimates.
> **Phase 0 will measure actual baselines** — targets will be adjusted accordingly.

| Metric | Target | Current (est.) | Measured in Phase 0? |
|--------|--------|----------------|---------------------|
| Initial Load | <2s | ~3-4s | ✅ Yes |
| Message Render (100 msgs) | <100ms | ~200-300ms | ✅ Yes |
| Total JS size (gzip) | <50KB | ~126KB raw | ✅ Yes |
| Time to Interactive | <2s | ~2.5s | ✅ Yes |
| Lighthouse Score | >90 | ~75 | ✅ Yes |

---

## Risk Analysis & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|-----------|
| Session-token auth breaks in new UI | Medium | **Critical** | Phase 0 audits token injection; test early in Phase 2 |
| Backend contract drift during rebuild | Medium | High | Freeze contract; any backend changes tracked separately |
| Wrapper/agent backward compat break | Low | **Critical** | Integration test in Phase 9; wrappers don't touch UI |
| WebSocket state sync issues | Medium | High | Separate bootstrap (REST) from real-time (WS); optimistic + rollback |
| React learning curve for team | Medium | Medium | Pair programming, docs, training |
| Styling breakage on old browsers | Low | Medium | Tailwind browser support list, fallbacks |
| Performance regression (large channels) | Low | High | Defer virtual scrolling to post-MVP; test with real data |
| 10-week estimate too optimistic | Medium | Medium | MVP/post-MVP split; post-MVP deferred if needed |
| Deprecated lib adoption | Low | Low | `@dnd-kit` replaces `react-beautiful-dnd` from day 1 |

---

## Dependency Tree

```
Phase 0 (Audit + Baseline) ─ 3 days
└─ Phase 1 (Foundation + web/ workspace) ─ Week 1
   ├─ Phase 2 (Core Chat) ─ Weeks 2-3
   │  ├─ Phase 3 (Agents) ─ Week 3
   │  ├─ Phase 4 (Channels) ─ Week 4
   │  └─ Phase 5 (Jobs) ─ Weeks 5-6
   ├─ Phase 6 (Settings + Rules + Pins) ─ Week 6
   ├─ Phase 7 (Sessions) ─ Week 7
   ├─ Phase 8 (Polish — MVP scope only) ─ Week 8
   ├─ Phase 9 (Integration + Feature-flag rollout) ─ Week 9
   └─ Phase 10 (Docs — minimal) ─ Week 10
```

**Critical Path:** 0 → 1 → 2 → 9
**Timeline model:** Ship per phase (Duong's decision) — no hard deadline, each vertical slice ships when ready
**Estimate:** ~10 weeks for MVP parity (soft target, not deadline)
**Post-MVP (virtual scroll, deep code splitting, full docs, CI/lint):** +2-3 weeks after MVP stable

---

## Success Criteria

### Technical
- ✅ All existing features working
- ✅ No console errors
- ✅ Performance targets met
- ✅ Mobile responsive (tested on 3+ devices)
- ✅ Dark theme fully applied
- ✅ <300KB JS gzipped

### UX
- ✅ Faster initial load (<2s)
- ✅ Smoother real-time updates
- ✅ Better mobile experience
- ✅ Easier to use on small screens

### Developer Experience
- ✅ Component-based architecture
- ✅ Easy to add new features
- ✅ Clear documentation
- ✅ Fast dev loop (Vite HMR)

### Business
- ✅ No user-facing downtime
- ✅ Smooth migration path
- ✅ Future-proof (React ecosystem)
- ✅ Reduced maintenance burden

---

## Open Questions / Decisions Needed

### Resolved (via Codex Review)
- ~~Integration strategy~~ → `web/` workspace, Vite builds to `static/` ✅
- ~~Drag-and-drop lib~~ → `@dnd-kit` (not deprecated `react-beautiful-dnd`) ✅
- ~~Rollout strategy~~ → Feature-flag (`?ui=new/legacy`), no A/B infra ✅
- ~~Performance baselines~~ → Phase 0 will measure actuals ✅
- ~~MVP scope~~ → Defined MVP vs post-MVP split ✅
- ~~Backend contract~~ → API contract frozen; ~10-line serving-layer tweak for rollout ✅

### Resolved (Duong's Decisions — 2026-03-14)
1. **TypeScript** → **(A) Full TypeScript from day 1** — strict types, better DX ✅
2. **Testing** → **(C) Unit + E2E (Playwright)** — full safety net ✅
3. **Timeline** → **(C) Ship per phase** — no hard deadline, deliver each vertical slice when ready ✅
4. **CI/Lint** → **(C) After MVP** — focus on shipping, add ESLint/Prettier/GH Actions post-MVP ✅

### No remaining open questions.

---

## Resources & References

- [Vite Docs](https://vitejs.dev/)
- [React 18 Docs](https://react.dev/)
- [Zustand GitHub](https://github.com/pmndrs/zustand)
- [TailwindCSS Docs](https://tailwindcss.com/)
- [shadcn/ui Components](https://ui.shadcn.com/)
- [Web Performance Checklist](https://www.speedcurve.com/blog/web-performance-checklist/)
- [Mobile-First CSS](https://www.lukew.com/ff/entry.asp?1117)

---

## Sign-Off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Product Owner | Duong | [awaiting] | [ ] |
| Tech Lead | | [awaiting] | [ ] |
| Lead Dev | Claude | [draft] | [pending] |

---

**Notes for Review:**
- This plan prioritizes mobile-first, dark theme, and developer experience
- Vite + React + Zustand chosen for speed, simplicity, and ecosystem
- Incremental rollout recommended to minimize risk
- Estimated 10 weeks for full rebuild + integration
- Ready for Codex review and counterargument phase
