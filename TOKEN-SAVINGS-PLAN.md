# Cutting token use in the agentchattr room — agreed plan (Claude + Codex, 17 Oct 2026)

Two rounds: Claude's proposals (01), Codex's review (02), Claude's reply (03), Codex's sign-off (04). All in this folder.

## What we measured today

| | Codex (gpt-6-sol) | Claude Code (Opus 5.5) |
|---|---|---|
| Model calls (all sessions) | 666 | 738 |
| Context on a session's first call | ~17 k | **~47.5 k, every time** |
| Context at the median call | 61 k | 77 k |
| Longest session | 158 k after 97 calls | 204 k after 195 calls (11 wakes) |
| Cached share of input | 96 % | ~98 % |

**Finding 1 — the chat history is a minor cost.** The channel never exceeded ~25 k tokens if read whole; agents read only new messages anyway (today's F1 fix). What costs is (a) each agent's own session baseline on every call and (b) the session's own growth as it works.

**Finding 2 — the two sides differ.** Claude Code's baseline is large and fixed (system prompt, tool schemas, skills list, the SessionStart "superpowers" hook text, CLAUDE.md, MCP descriptions). Codex's baseline is small; its cost is growth across a long session.

**Finding 3 — the biggest plan saving is a setting, not code.** Fiona's global Codex config runs in Fast mode, which OpenAI documents as consuming included plan usage at 2.5× the Standard rate. Nothing in the room benefits from Fast.

**Caveat (Codex's point, agreed):** how cached tokens count against *included* ChatGPT plan usage isn't published; against credits they're 10× cheaper. Treat token counts as a proxy and check the usage dashboards before and after each change.

## Agreed ranked actions

| # | Action | Saves for | Rough saving | Effort |
|---|---|---|---|---|
| 1 | Run room Codex on **Standard** speed (`-c service_tier=default` in the wrapper launch, or a room profile). Verify the tier after launch. | Codex plan | ~60 % less included usage for identical turns; 0 raw tokens | Minutes |
| 2 | **Lean room profiles.** Claude Code: dedicated `CLAUDE_CONFIG_DIR` with no plugins/hooks, only the agentchattr MCP server, a short CLAUDE.md. Codex: a `--profile` with plugins off, `--disable code_mode` unless needed, and a trimmed `model_instructions_file`. Measure first-call context before/after. | Both; largest for Claude | Claude: 47.5 k → target 15–20 k per call (unproven until trialled); Codex: unmeasured | Medium |
| 3 | **End idle turns; never sleep/poll.** Put it in the wake prompt and in `_MCP_INSTRUCTIONS`. | Both | ~10–25 % of room tokens where polling happened (it did in test 9) | Low |
| 4 | **No acknowledgement-only mentions; batch actionable points.** A mention wakes the other agent for a full multi-call turn (`router.py` routes on any agent mention). Status without a mention is free. | Both | ~10–25 % of room tokens | Low (a room rule + one prompt line) |
| 5 | **One unread read per wake**, and reconcile `_MCP_INSTRUCTIONS` so `chat_join`/rules/summary happen only on a genuinely new session (needs an explicit first-trigger marker in the wrapper, tested across restarts). | Both | ~10–20 % | Low–medium |
| 6 | **Task-bounded sessions:** restart agent sessions at completed-task boundaries; treat a context-size threshold as a signal to finish/checkpoint, not an automatic mid-task reset. Compare total usage across the restart (rebuild cost). | Both | ~10–25 % of input in long sessions | Low–medium |
| 7 | **Concise tool outputs; trim duplicated instruction text** (`_MCP_INSTRUCTIONS` states the identity rules three times). | Both | Variable; largest in long sessions | Low–medium |
| 8 | **Effort and model by role:** Codex `medium` for building, `high` for review; cheaper models for bounded mechanical work. Judge by completed tasks and corrections, not tokens alone. | Both | Variable | Low–medium |

Savings overlap; do not add them. Dropped after discussion: `/clear` after every exchange (loses cache reuse), summary-on-every-wake (adds a call), merging tools into one (causes wrong calls), raising the hop limit (permits more wakes). DECISIONS.md stays optional for multi-day projects.

## How we'll know it worked
Re-run the measurement script after each change and compare: first-call context, last-call context, calls per session, wakes per session, output tokens, speed tier, effort — and tasks completed versus corrections needed. Check the Claude and ChatGPT usage dashboards alongside.

## Suggested order
Items 1, 3, 4 today (minutes, no risk). Item 2 as a measured trial on the next project. Items 5–8 as agentchattr changes on the fix branch, each with a before/after measurement.
