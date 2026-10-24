# Trial findings to fix (agentchattr @ d775776, native Windows, 17 Oct 2026)

Reproduced in a scripted trial with Claude Code 2.1.302 and Codex CLI 0.163.0. Full report and evidence:
`C:\Users\mayhe\Documents\agent-trial-report-2026-10-17\`.

## F1 — Mentions sent while an agent is busy are silently lost (reproduced 3/3 for Claude)
- Mechanism: `mcp_bridge.chat_send` ends with `_update_cursor(sender, [msg], channel)`, moving the agent's read cursor to its **own** just-sent message id. Any messages that arrived between the agent's last `chat_read` and that send are skipped forever; the next triggered `chat_read` returns nothing after the cursor.
- Secondary leak: `chat_read` applies `msgs[-limit:]` (default 20) and then sets the cursor to the last returned id, so a backlog larger than `limit` drops the oldest unread.
- Observed: Claude read up to #103, ran a 45 s command, #104 and #105 (addressed to it) arrived, Claude sent #106, cursor → 106, then reported "nothing new addressed to me".

## F2 — Messages sent while an agent is down are lost when it restarts
- `agents.trigger` correctly appends to `data/<name>_queue.jsonl` even when the agent is offline, but `wrapper.py` wipes that file on startup (`queue_file.write_text("")` right after identity assignment).
- Compounded by F3: after an unclean stop the relaunched agent gets a new name and never looks at the old queue file.

## F3 — Identity churn after an unclean stop
- A force-closed wrapper leaves a live registry entry until the crash timeout; a quick relaunch gets `name-2`. The dead instance can show as online for 60+ s. Fresh Claude sessions then refuse to post under an uncertain name.
- `registry.register()` only reclaims via `chat_claim`; normal startup does not reclaim a stale same-base instance. The wrapper has no console-close handler to deregister.

## F4 — Claude's chat tools die when the server restarts
- Each agent reaches the server through its own local `mcp_proxy`. After a server restart the upstream MCP session is gone; Claude Code marks the MCP server failed (ECONNREFUSED) and never retries. Codex's client recovers on its own.
- Fix direction: make the proxy the stable endpoint — re-initialise its upstream session on failure and keep serving the agent's existing session; fallback: wrapper detects a server epoch change and injects `/mcp` reconnect.

## F5 — Dashboard shows an agent as idle while it waits on an approval prompt
- `wrapper_windows.get_activity_checker` infers busy/idle from screen-cell churn; a static permission prompt looks idle after 5 quiet seconds. The chat shows nothing.
- Fix direction: detect known prompt text on screen and report a "waiting for approval" state (status pill + system message).

## F6 — `/continue` lifts the loop-guard pause but does not re-route the blocked message
- `router.continue_routing()` resets state; nothing re-delivers the message whose routing was blocked, so the chain only resumes on a new human mention.

## Constraints for the fix
- Keep changes small and covered by tests in `tests/` (`.venv\Scripts\python.exe -m unittest discover -s tests`, the repo venv; the bare interpreter lacks fastapi/mcp). Baseline: OK, 4 skipped.
- Don't change the MCP tool surface or config keys without a reason; existing users rely on them.
- Reviewer must actually run the suite and say which command it ran; claims are checked against session logs.

## F7 — A wake prompt injected while the agent is mid-turn can be lost (found 17 Oct, 12:01)
- Observed once: a trigger arrived while Claude Code was mid-turn; the console then showed "Interrupted · What should Claude do instead?" with an empty composer, and the injected prompt was gone.
- **Cause not established.** The first note here blamed a leading ESC in `wrapper_windows.inject`; Codex checked and the injector sends no ESC (it writes the text and VK_RETURN; the Unix path's only ESC bytes are bracketed-paste framing). The lone-ESC-on-focus behaviour mentioned in `enable_vt_mode`'s docstring is a candidate, unverified.
- **Mitigation implemented (wrapper.py):** the queue watcher no longer injects while the activity monitor reports the agent active or waiting for approval; the group stays in `.processing` and is retried each poll, acknowledged only after a successful inject (F2 semantics). An active state older than 180 s (measured from the `.processing` file's mtime) forces the inject so a misread can't starve delivery; an approval wait is never forced. Tests in `tests/test_queue_delivery.py`.
