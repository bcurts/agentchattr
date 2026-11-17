# Expert room example

A setup for people who keep many repos and many long-lived Claude Code sessions: one **expert** agent per repo (or area), a **lead** that plans cross-repo work, a read-only **reviewer**, all talking in agentchattr. Each expert is the real `claude` CLI in tmux, so your hooks, plugins, MCP servers and slash commands keep working (`@backend //compact` from the chat).

What it adds on top of plain agentchattr:
- **Personas** per expert (`experts/*.md` + shared `_protocol.md`), passed with `args = ["--append-system-prompt-file", ...]`.
- **Expert directory**: `expertise = "..."` per agent; `chat_who` lists it, so agents route questions themselves.
- **Feature files** (`features/FEAT-<id>.md`, owned by the lead): plan, branches, PRs, decisions and next step per cross-repo feature. A crashed or `/clear`ed session picks up from the file, not from a huge transcript.
- **Guardrails** (`guard.json`, passed with `--settings`): deny rules for force-push, cluster mutations, PR merge, `rm -rf`. Claude Code enforces deny rules even with `--dangerously-skip-permissions`.
- **`ac` launcher**: starts the server and each expert in detached tmux sessions (no terminal windows), `ac attach <agent>` to drop into one.

## Setup

1. Copy this folder somewhere (your "hub"), edit `experts/*.md`, add one persona per expert.
2. Add the experts to `config.local.toml` (or `config.toml`):

```toml
[agents.lead]
command = "claude"
cwd = "/path/to/hub"
label = "Lead"
expertise = "Plans cross-repo features, routes work to experts, owns features/*.md. No code."
args = ["--name", "ac-lead", "--append-system-prompt-file", "/path/to/agentchattr/data/prompts/lead.md",
        "--settings", "/path/to/hub/guard.json"]

[agents.backend]
command = "claude"
cwd = "/path/to/api-repo"
label = "Backend"
expertise = "api-repo: REST API, DB schema, auth. Contract owner for anything the frontend calls."
args = ["--name", "ac-backend", "--append-system-prompt-file", "/path/to/agentchattr/data/prompts/backend.md",
        "--settings", "/path/to/hub/guard.json"]

[routing]
default = "none"
max_agent_hops = 8   # lead -> expert -> expert -> lead needs more than 4
```

`ac` builds `data/prompts/<agent>.md` from `_protocol.md` + `experts/<agent>.md` on every start.

3. Run:

```sh
HUB=/path/to/hub /path/to/agentchattr/examples/expert-room/ac up        # every agent with an expertise
ac up lead backend    # or a subset
ac attach backend     # real CLI; Ctrl+B D to detach
ac ls / ac down
```

The first start of an agent in a folder Claude Code hasn't seen asks "trust this folder": `ac attach <agent>` and accept once.

## Tips
- Interns = the CLI's own subagents inside each expert; they stay out of the chat.
- Channels: `#general` (you + lead), one per active cross-repo feature, `#reviews`, `#incidents`. Single-repo tasks: use a job.
- `ac restart` resumes each expert's last Claude Code session (found by its `--name`), so restarts keep context; `FRESH=1 ac restart x` starts clean.
- Idle experts cost nothing; each wake-up is a model turn. Start only the experts you need today.
- If you add `--dangerously-skip-permissions`, remember any agent can wake any other: keep the deny list tight and give ops-style agents read-only tools (`--disallowedTools Edit Write`).
