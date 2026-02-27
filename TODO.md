# TODO

See [WORKFLOW.md](WORKFLOW.md) for the full process.

## Task Template
```
### [Task Title]
- **Owner:** Pending / In Progress - [Agent] / Review - [Agent] / Done
- **Scope:** What exactly is being changed
- **Acceptance criteria:** What "done" looks like
- **Test plan:** How to verify it works
- **Branch:** feature/[slug]
```

---

## Backlog

### Merge PR #1 — fix/gemini-input-stacking
- **Owner:** Done
- **Scope:** Merge the existing PR with Escape fix, configurable cooldowns, tests, and CI
- **Acceptance criteria:** CI green, branch merged to main
- **Test plan:** CI runs automatically on merge

---

### Session resume on restart
- **Owner:** Review - codex (implemented by gemini-cli, PR open on feature/session-resume)
- **Scope:** `wrapper.py` — use each agent's `resume_flag` from config to resume the previous session on restart instead of starting cold
- **Acceptance criteria:** Restarting `start_gemini.sh` resumes the last Gemini session; same for Claude and Codex
- **Test plan:** Unit test that `run_agent` constructs the correct resume command; manual smoke test
- **Branch:** feature/session-resume

---

### Stale queue file cleanup on startup
- **Owner:** Review - gemini-cli
- **Scope:** `wrapper.py` — flush (truncate) the agent's queue `.jsonl` file at startup to prevent leftover entries from a previous crashed session firing unexpectedly
- **Acceptance criteria:** Starting a wrapper with a non-empty queue file does not trigger spurious injections
- **Test plan:** Unit test with a pre-populated queue file; verify it is cleared before the watcher starts
- **Branch:** feature/queue-flush-on-start

---

### Windows wrapper parity (Escape-before-inject)
- **Owner:** Review - codex (implemented by gemini-cli, PR #4 open)
- **Scope:** `wrapper_windows.py` — add the same Escape keystroke before injection that was added to `wrapper_unix.py` in PR #1
- **Acceptance criteria:** Windows inject function sends Escape → text → Enter, matching unix behavior; existing windows tests (if any) pass
- **Test plan:** Unit test mirroring `test_inject_sends_escape_before_text` for the windows inject path
- **Branch:** fix/windows-wrapper-parity

---

### "Working" indicator in chat UI
- **Owner:** In Progress - codex
- **Scope:** Show a `…` or "active" status next to an agent's name in the chat UI while they are processing a task
- **Acceptance criteria:** Agent status pill shows activity when processing; returns to normal when idle
- **Test plan:** Manual UI test; unit test for backend status endpoint if applicable
- **Branch:** feature/working-indicator

---

### ngrok secret token auth
- **Owner:** Pending - codex (after PR reviews)
- **Scope:** `app.py` — read `ACCESS_TOKEN` env var; reject requests missing `?token=<value>` when set; no change to local-only usage
- **Acceptance criteria:** Setting `ACCESS_TOKEN=xyz ngrok http 8300` and opening `https://xxx.ngrok.io?token=xyz` works; without token param returns 403
- **Test plan:** Unit test middleware; manual test via ngrok URL
- **Branch:** feature/ngrok-auth

---

### ngrok setup docs
- **Owner:** Review - codex (implemented by gemini-cli, PR #6 open)
- **Scope:** `README.md` — add ngrok installation, startup command, and `ACCESS_TOKEN` usage instructions
- **Acceptance criteria:** A new user can follow README to get mobile access in < 5 min
- **Test plan:** Walkthrough review
- **Branch:** docs/ngrok-setup

---

### Kanban task sidebar
- **Owner:** Pending - codex (after ngrok auth)
- **Scope:** Backend `/api/tasks` endpoint parses `TODO.md` → returns tasks as JSON; frontend collapsible sidebar in `static/` shows kanban columns (Pending / In Progress / Review / Done) with auto-refresh every 30s
- **Acceptance criteria:** Sidebar visible in chat UI; cards update when TODO.md changes; works on mobile viewport
- **Test plan:** Unit test TODO.md parser; manual UI test
- **Branch:** feature/kanban-sidebar

---

### @user mentions filter
- **Owner:** Review - codex (implemented by gemini-cli, PR #5 open)
- **Scope:** `static/` — add a filter button/tab to the chat UI showing only messages that @mention the user, with an unread badge count
- **Acceptance criteria:** Clicking the filter shows only @user messages; badge shows count of unseen @mentions; clicking a mention scrolls to it
- **Test plan:** Manual UI test; works on mobile viewport
- **Branch:** feature/user-mentions-filter

---

### `/status` command — agent health check
- **Owner:** Review - codex (implemented by gemini-cli, PR #7 open)
- **Scope:** Add a `/status` slash command (or equivalent API endpoint) that reports each agent's online status, last-seen time, and queue depth
- **Acceptance criteria:** Typing `/status` in the chat UI returns a health summary for all configured agents
- **Test plan:** Unit test the status logic; integration test via the API endpoint
- **Branch:** feature/status-command

---

### `/history` command — cross-session message history
- **Owner:** In Progress - gemini-cli
- **Scope:** Add a `/history [n]` command that returns the last N messages from the persistent store, including messages from previous sessions
- **Acceptance criteria:** `/history 20` returns the 20 most recent messages regardless of when the server started
- **Test plan:** Unit test store retrieval; manual test across a server restart
- **Branch:** feature/history-command

---

### Automated stale tmux session cleanup
- **Owner:** Pending
- **Scope:** Add logic to detect and kill tmux sessions for agents that have been offline for > N minutes (configurable)
- **Acceptance criteria:** Dead sessions are cleaned up automatically; active sessions are untouched
- **Test plan:** Unit test session detection logic with mocked tmux output
- **Branch:** feature/tmux-session-cleanup

---

### Projects — multi-context workspaces
- **Owner:** Pending (needs design discussion first)
- **Scope:** Add a "Projects" concept so agents and the user can switch between separate working contexts (e.g., "agentchattr", "Valuerank coding", "Valuerank vignette analysis"). Each project has its own chat history, task list, and agent assignments.
- **Acceptance criteria:** User can create/switch projects from the UI; chat history and tasks are scoped per project; agents know which project context they are in
- **Test plan:** Manual UI test across at least 2 projects; verify history isolation; unit test project switching logic
- **Branch:** feature/projects
- **Note:** Needs architecture design before implementation — data model, config format, agent awareness
