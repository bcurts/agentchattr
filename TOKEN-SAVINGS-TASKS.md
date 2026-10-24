# Token-savings implementation tasks (branch feat/token-savings)

Source: TOKEN-SAVINGS-PLAN.md (agreed by Claude and Codex). Items 1, 2 and 8 are launch-flag/profile changes the supervisor applies and measures; the items below are code changes for this repo. Same rules as before: failing-first tests, run `.venv\Scripts\python.exe -m unittest discover -s tests`, cross-review, no commits.

## T1 — Wake prompt and standing instructions (plan items 3, 4, 5) — owner: Claude
- `wrapper.py` wake prompt (≈ line 896): make it prescriptive: "Call chat_read once (it returns only unread messages), do what the message asks, call chat_send once if a reply is needed, then END YOUR TURN. Do not call chat_who or chat_rules (rules are included below when they change). Never wait by sleeping or re-reading; the room wakes you when there is something new. Acknowledge with a bare ✅ (no @mention). @mention another agent only when they must act."
- `mcp_bridge._MCP_INSTRUCTIONS`: remove the unconditional "chat_join when you start a session", the duplicated identity rules (stated three times), and anything that tells agents to call chat_rules/chat_summary routinely. Join/rules/summary should be requested only on a genuinely new session: the wrapper already distinguishes a first trigger (it adds `_IDENTITY_HINT` once) — generalise that into an explicit first-trigger marker that survives restarts (a small state file next to the token file is fine) and have the first wake prompt say "new session: call chat_join and read the summary first".
- Tests: wake prompt content for first vs later triggers; instructions text has no duplicate identity block; a test that the server instructions no longer mention chat_join unconditionally.

## T2 — ✅ reactions (plan item 4 mechanism) — owner: Codex
- New MCP tool `chat_react(msg_id, emoji, sender)` in `mcp_bridge.py`: records a reaction on an existing message (store it in the message's metadata, e.g. `reactions: {"✅": ["codex"]}`), triggers NO routing/wake, and is NOT included in `chat_read` output (so readers pay nothing for it). Idempotent per (sender, emoji).
- `app.py`: broadcast the reaction so the web UI shows a small badge on the message; `static/chat.js` renders it. A human can also react from the UI (optional if cheap).
- Tests: react stores and dedups; chat_read output unchanged; no trigger written to any queue.

## T3 — Session reset at task boundaries (plan item 6) — owner: Claude
- A human slash command `/newtask [name]` in the chat (both websocket and HTTP paths like `/continue`): posts a system message, clears the loop-guard state, and asks each wrapper to restart its CLI session (the wrapper polls for a `restart` marker in its queue dir, or receives it via the existing heartbeat response), keeping identity (token takeover) and the chat as shared memory.
- The wrapper restarts the CLI process cleanly (deregister as reclaimable → relaunch → takeover). Pending queue items survive (F2 semantics).
- Tests: the command path emits the restart request; the wrapper restart path preserves identity and queue (reuse the F3 test fixtures).

## T4 — Concise outputs and trimmed text (plan item 7) — owner: Codex
- `chat_read`: cap `text` fields at a configurable length (default 2,000 chars) with a `[… N more chars; chat_read with since_id=<id> limit=1 for full text]` marker — or a `full=true` flag — so one giant message doesn't inflate every later read. Keep the default behaviour for small messages.
- `_MCP_INSTRUCTIONS`: coordinate with T1 (Claude owns the file edit; Codex reviews the wording for Codex-specific needs, e.g. the exec/code-mode style of tool calls).
- Tests: long message truncated with marker; short unchanged; full read works.

## Measurement (supervisor)
Before/after on the same tasks: first-call context, median context, calls per wake, output tokens, speed tier, effort. Today's baseline: Codex first call ≈ 17 k, median 61 k; Claude first call 47.5 k, median 77 k.
