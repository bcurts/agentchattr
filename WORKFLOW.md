# Autonomous Multi-Agent Workflow

## Roles
- **Claude** — Coordinator + implementation. Monitors agent health, unsticks frozen agents, implements tasks.
- **Codex** — Implementation + primary code reviewer.
- **Gemini** — Technical research, edge-case auditing, secondary reviewer.
- **User (PM)** — Owns goals, priorities, and acceptance criteria. Merges approved PRs.

## Task Lifecycle

### 1. Claiming a Task
- Pick the top `Pending` item from `TODO.md`.
- Update its status to `In Progress - [Agent Name]` in `TODO.md`.
- Announce in chat: *"Starting task: [task name]"*
- **WIP limit:** Max 1 active task per agent at a time.

### 2. Working on a Task
- Create a feature branch: `git checkout -b feature/[task-slug]`
- Post a **heartbeat** in chat every 10 minutes with a brief progress update.
- If blocked, **immediately** post in chat — don't wait for the timeout.

### 3. Completing a Task
- Write tests; ensure all tests pass and lint is clean locally.
- Push branch and open a PR.
- Announce in chat: *"PR open for [task]: [URL]. Requesting review from @[agent]."*
- Update `TODO.md` status to `Review - [Agent Name]`.

### 4. Code Review
- Reviewer reads the diff, runs `pytest` + `ruff`, and verifies CI is green.
- Post review comment on GitHub with explicit **APPROVE** or **BLOCK**.
- Confirm result in chat.

### 5. Merging
- PR merges only when: CI green + at least one non-author approval.
- After merge: update `TODO.md` status to `Done`, claim next task.

## Timeouts & Recovery
- **15 minutes** without a heartbeat or commit → task returns to `Pending`.
- Any agent can proactively unstick another (e.g., send Escape to clear frozen Gemini input).
- Claude (coordinator) checks agent health and escalates to the user if needed.

## Branch & Commit Conventions
- Branches: `feature/[slug]`, `fix/[slug]`, `test/[slug]`
- Commits: conventional format — `feat:`, `fix:`, `test:`, `docs:`, `chore:`
- Co-author line: `Co-Authored-By: [Agent] <noreply@anthropic.com>`
