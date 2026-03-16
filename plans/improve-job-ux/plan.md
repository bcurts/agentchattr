# Plan: Improve Job UI/UX (Unifying with Thread UX)

## Goal
Unify the UI/UX surface of "Jobs" with the familiar flow of "Threads" (conversations), making Jobs feel like a natural extension of the chat experience rather than a separate, heavy module.

**Key Principle:** "Every job is a special thread, but not every thread is a job."

## Current State Analysis
- **Jobs:** Managed via `JobsPanel` (Kanban view) and `JobThread` (Side panel). Jobs have metadata (status, assignee) and their own message persistence (`jobs.py`).
- **Threads (Chat):** Handled via `reply_to` on messages. Currently displayed as compact quoted snippets (`ReplyContext.tsx`) within the main message list. No dedicated "Thread Panel" exists for regular chat threads in the React UI (`web/`).
- **Legacy UI:** The old `static/chat.js` has some "convert to job" logic, but it's missing in the new `web/` (React) UI.

## Proposed Changes

### Phase 1: Message/Thread Promotion to Job
- **Action:** Add a "Promote to Job" action button to `MessageItem` (visible on hover).
- **Backend Plumbing:**
  - Update `CreateJobPayload` in `web/src/services/api.ts` to include `anchor_msg_id`.
- **Frontend Plumbing:**
  - Modify `CreateJobForm.tsx` to handle pre-filled data (title, body, anchor_msg_id, channel).
  - Use the correct channel context from the original message when promoting.
- **Visual Feedback:**
  - Implement a visual indicator (e.g., a "Job" badge/link) on the original message.
  - This requires cross-referencing `jobsStore` in `MessageItem` to find any job with a matching `anchor_msg_id`.

### Phase 2: Unify Job Thread UX
- **Redesign `JobThread`:**
  - Create a `JobMessageItem.tsx` (lightweight presentational component or adapter). This avoids tight coupling with the `Message` type while ensuring UI parity.
  - **Markdown Support:** Integrate `renderMarkdown()` into `JobMessageItem` to ensure messages are rendered correctly (currently plain text).
  - **Consistent Input:** Update the reply input in `JobThread` to match the main `MessageInput` styling and behavior.
- **Navigation:**
  - Clicking a Job from the `JobsPanel` or from a "Job badge" on a message should open the `JobThread` in the side-panel.

### Phase 3: Metadata Management
- Keep `status` and `assignee` management inside the `JobThread` header or a dedicated sub-header (`UpdateJobControls`).
- Ensure these controls are compact and don't distract from the conversation.

## Technical Strategy
- **Frontend (`web/`):**
  - **Stores:** No major store changes; use existing `chatStore` and `jobsStore`.
  - **Components:** Create `JobMessageItem.tsx`, update `MessageItem.tsx`, `JobThread.tsx`, and `CreateJobForm.tsx`.
- **Backend (`app.py` / `jobs.py`):**
  - **Verification:** Confirm `POST /api/jobs` and `jobs.create()` already handle `anchor_msg_id` (verified: they do). Phase 1 is primarily frontend plumbing.

## Out of Scope
- **Data Model Merge:** We will NOT merge the chat message model and the job message model. They remain separate entities in the backend (`store.py` vs `jobs.py`).
- **Global Thread Panel:** Creating a general-purpose "Thread Panel" for regular chat replies is out of scope for this specific task.

## Verification Plan
1. **Manual Testing:**
   - Hover over a message, click "Promote to Job".
   - Verify `CreateJobForm` opens with correct pre-filled title/body/channel.
   - After creation, verify the original message shows a link/badge to the new job.
   - Open the job and verify `JobMessageItem` renders markdown correctly.
   - Reply in the job and verify consistent styling.
2. **Automated Testing:**
   - **E2E (Playwright):**
     - Case 1: Message promotion flow (hover -> click -> fill -> submit -> check badge).
     - Case 2: Job navigation (click badge -> open thread -> check content).
     - Case 3: Job messaging (send reply in JobThread -> verify broadcast).
