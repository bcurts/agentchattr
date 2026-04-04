# Why Contribute These Changes Upstream

This repository is the active working tree for evaluating `agentchattr` as a
live multi-agent debate tool. The security and integrity issues found here are
not local customizations; they are core trust-boundary problems in upstream
behavior.

## Why upstream is the right place

### 1. The issues are architectural, not deployment-specific

Examples:

- regex-based SVG sanitization combined with DOM `innerHTML`
- public-route matching by prefix
- browser-visible bearer token with broad authority
- broken archive import path

These are not environment quirks. They are source-level issues.

### 2. Local-only tools still need strong trust boundaries

`agentchattr` is intentionally localhost-first, but that does not remove the
need for:

- authenticated write paths
- browser/agent auth separation
- safe rendering of agent-controlled content
- reliable import/export and recovery behavior

Upstream should be secure by default for its stated operating model.

### 3. Carrying a private fork would be high-friction

If these fixes stay local only, every upstream pull would require:

- manual rebasing of security changes
- repeated review of the same trust boundaries
- continued divergence in auth behavior and UI assumptions

That is the wrong long-term maintenance model.

### 4. The changes are reviewable in small slices

The hardening plan is intentionally organized into narrow PRs:

- auth fix
- SVG/hat shutdown
- browser auth redesign
- local action restriction
- archive repair

That gives upstream maintainers a realistic review path.

## Contribution posture

These PRs should be framed as:

- default-safe behavior improvements
- exploit-surface reduction
- integrity/recovery fixes

They should not be framed as a rewrite or a criticism of the product concept.

## Standards for contribution

Each upstream PR should include:

- a concrete problem statement
- a narrow fix
- regression coverage
- minimal unrelated cleanup
- a short rationale explaining the threat boundary being repaired

## Bottom line

If this repo is going to be used seriously as a live agent coordination tool,
these fixes should not live in a private fork. They address foundational trust
boundaries and belong upstream.

