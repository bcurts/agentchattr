/**
 * sessions.js -- Session orchestration UI module
 *
 * Extracted from chat.js (PR 2 of monolith breakup).
 * Depends on core.js (Hub) and store.js (Store) loaded first.
 *
 * Owns all session state, rendering, and interaction logic.
 * Subscribes to Hub for WS events and watches Store for channel changes.
 *
 * Reads from window: activeChannel, username, ws,
 *                    agentConfig, escapeHtml, switchChannel
 */

// ---------------------------------------------------------------------------
// Session state (moved from chat.js globals)
// ---------------------------------------------------------------------------

let activeSession = null;
let sessionTemplates = [];
let activeSessionsByChannel = {};
let sessionIndicatorTargetChannel = null;

// ---------------------------------------------------------------------------
// Message Renderers
// ---------------------------------------------------------------------------
// Register session message renderers so appendMessage in chat.js can
// delegate to us via window._messageRenderers[msg.type](el, msg).

if (!window._messageRenderers) window._messageRenderers = {};

function _renderSessionDraftResolvedCard(label, detail = "") {
  return `
        <div class="proposal-card session-proposal-card proposal-resolved">
            <div class="proposal-header">
                <span class="proposal-pill">Session Proposal</span>
            </div>
            <div class="proposal-title">${window.escapeHtml(label)}</div>
            ${detail ? `<div class="proposal-body session-proposal-desc">${window.escapeHtml(detail)}</div>` : ""}
        </div>`;
}

window._messageRenderers["session_start"] = function (el, msg) {
  el.classList.add("system-msg", "session-banner", "session-banner-start");
  const goal = msg.metadata?.goal
    ? ` -- ${window.escapeHtml(msg.metadata.goal)}`
    : "";
  el.innerHTML = `<span class="session-banner-icon">&#9654;</span> <strong>${window.escapeHtml(msg.text)}</strong>${goal}`;
};

window._messageRenderers["session_end"] = function (el, msg) {
  el.classList.add("system-msg", "session-banner", "session-banner-end");
  const outputId = msg.metadata?.output_message_id;
  const jumpLink = outputId
    ? ` <span class="session-output-link" onclick="scrollToSessionOutput(${outputId})">View output</span>`
    : "";
  el.innerHTML = `<span class="session-banner-icon">&#9632;</span> <strong>${window.escapeHtml(msg.text)}</strong>${jumpLink}`;
};

window._messageRenderers["session_phase"] = function (el, msg) {
  el.classList.add("system-msg", "session-banner", "session-banner-phase");
  el.innerHTML = `<span class="session-banner-icon">&#9656;</span> ${window.escapeHtml(msg.text)}`;
};

window._messageRenderers["session_draft"] = function (el, msg) {
  el.classList.add("system-msg", "session-draft-card");
  const meta = msg.metadata || {};
  const valid = meta.valid;
  const errors = meta.errors || [];
  const tmpl = meta.template || {};
  const draftId = meta.draft_id || "";
  const proposedBy = meta.proposed_by || "?";
  const revision = meta.revision || 1;
  const proposedByColor = window.getColor
    ? window.getColor(proposedBy)
    : "var(--text-dim)";

  // Check if superseded by a newer revision
  const isSuperseded = _isSupersededDraft(draftId, revision);

  // Always store draft identity for supersession lookups
  el.dataset.draftId = draftId;
  el.dataset.draftRevision = revision;

  if (isSuperseded) {
    el.classList.add("session-draft-superseded");
    el.innerHTML = _renderSessionDraftResolvedCard(
      "Superseded draft",
      `Replaced by revision ${revision}.`,
    );
  } else if (!valid) {
    el.classList.add("session-draft-invalid");
    const errorList = errors
      .map((e) => `<li>${window.escapeHtml(e)}</li>`)
      .join("");
    el.innerHTML = `
            <div class="proposal-card session-proposal-card session-proposal-invalid">
                <div class="proposal-header">
                    <span class="proposal-pill">Session Proposal</span>
                    <span class="proposal-author" style="color: ${proposedByColor}">${window.escapeHtml(proposedBy)}</span>
                </div>
                <div class="proposal-title">Invalid session draft</div>
                <div class="proposal-body session-proposal-desc">This draft needs changes before it can be run or saved.</div>
                <ul class="session-draft-errors">${errorList}</ul>
                <div class="proposal-actions">
                    <button class="proposal-request-changes" onclick="requestDraftChanges('${window.escapeHtml(draftId)}', '${window.escapeHtml(proposedBy)}', ${msg.id})">Request Changes</button>
                    <button class="proposal-dismiss" onclick="dismissDraft(${msg.id})">Dismiss</button>
                </div>
            </div>`;
  } else {
    const phases = tmpl.phases || [];
    const phasesDetailHtml = phases
      .map((p, i) => {
        const parts = (p.participants || [])
          .map(
            (r) =>
              `<span class="session-draft-phase-participant-pill">${window.escapeHtml(r)}</span>`,
          )
          .join("");
        const promptText = p.prompt ? window.escapeHtml(p.prompt) : "";
        return `<div class="session-draft-phase-detail">
                <span class="session-draft-phase-num">${i + 1}</span>
                <div class="session-draft-phase-copy">
                    <div class="session-draft-phase-top">
                        <span class="session-draft-phase-name">${window.escapeHtml(p.name)}</span>
                        ${parts ? `<span class="session-draft-phase-participants">${parts}</span>` : ""}
                    </div>
                    ${promptText ? `<div class="session-draft-phase-prompt">${promptText}</div>` : ""}
                </div>
            </div>`;
      })
      .join("");
    const metaLabel = revision > 1 ? `rev ${revision}` : "";
    el.dataset.draftTemplate = JSON.stringify(tmpl);
    el.dataset.draftMsgId = msg.id;
    el.innerHTML = `
            <div class="proposal-card session-proposal-card">
                <div class="proposal-header">
                    <span class="proposal-pill">Session Proposal</span>
                    <span class="proposal-author" style="color: ${proposedByColor}">${window.escapeHtml(proposedBy)}</span>
                    ${metaLabel ? `<span class="session-draft-meta">${metaLabel}</span>` : ""}
                </div>
                <div class="proposal-title">${window.escapeHtml(tmpl.name || "?")}</div>
                ${tmpl.description ? `<div class="proposal-body session-proposal-desc">${window.escapeHtml(tmpl.description)}</div>` : ""}
                <div class="session-draft-details">
                    ${phasesDetailHtml}
                </div>
                <div class="proposal-actions">
                    <button class="proposal-accept" onclick="runDraft(${msg.id})">Run</button>
                    <button class="proposal-request-changes session-draft-btn-save" onclick="saveDraft(${msg.id}, this)">Save Template</button>
                    <button class="proposal-request-changes" onclick="requestDraftChanges('${window.escapeHtml(draftId)}', '${window.escapeHtml(proposedBy)}', ${msg.id})">Request Changes</button>
                    <button class="proposal-dismiss" onclick="dismissDraft(${msg.id})">Dismiss</button>
                </div>
            </div>`;
    // Supersede older revisions of the same draft
    if (revision > 1) {
      setTimeout(() => _supersedePreviousDrafts(draftId, revision), 0);
    }
  }
};

// ---------------------------------------------------------------------------
// Session API functions
// ---------------------------------------------------------------------------

async function fetchSessionTemplates() {
  try {
    const res = await fetch("/api/sessions/templates", {});
    if (res.ok) sessionTemplates = await res.json();
  } catch (e) {
    console.warn("Failed to fetch session templates", e);
  }
}

async function fetchAllActiveSessions() {
  try {
    const res = await fetch("/api/sessions/active-all", {});
    if (res.ok) {
      const sessions = await res.json();
      activeSessionsByChannel = {};
      (sessions || []).forEach((session) => {
        const channel = session?.channel || "general";
        activeSessionsByChannel[channel] = session;
      });
      activeSession = activeSessionsByChannel[window.activeChannel] || null;
      updateSessionBar();
    }
  } catch (e) {
    console.warn("Failed to fetch active sessions", e);
  }
}

async function fetchActiveSession(channelName) {
  if (channelName === undefined) channelName = window.activeChannel;
  try {
    const res = await fetch(
      `/api/sessions/active?channel=${encodeURIComponent(channelName)}`,
      {},
    );
    if (res.ok) {
      const data = await res.json();
      if (data) activeSessionsByChannel[channelName] = data;
      else delete activeSessionsByChannel[channelName];
      if (channelName !== window.activeChannel) return;
      activeSession = data;
      updateSessionBar();
    }
  } catch (e) {
    console.warn("Failed to fetch active session", e);
  }
}

// ---------------------------------------------------------------------------
// WebSocket event handler
// ---------------------------------------------------------------------------

function handleSessionEvent(action, session) {
  if (!session) return;
  const channel = session.channel || "general";

  if (action === "create" || action === "update") {
    activeSessionsByChannel[channel] = session;
    if (channel === window.activeChannel) {
      activeSession = session;
    }
  } else if (action === "complete" || action === "interrupt") {
    delete activeSessionsByChannel[channel];
    if (channel === window.activeChannel) {
      activeSession = null;
    }
    // Highlight the output message
    if (
      channel === window.activeChannel &&
      action === "complete" &&
      session.output_message_id
    ) {
      highlightSessionOutput(session.output_message_id);
    }
  }
  updateSessionBar();
}

// ---------------------------------------------------------------------------
// Session bar
// ---------------------------------------------------------------------------

function updateSessionBar() {
  const bar = document.getElementById("session-bar");
  if (!bar) return;
  const templateEl = bar.querySelector(".session-template");
  const phaseEl = bar.querySelector(".session-phase");
  const waitingEl = bar.querySelector(".session-waiting");
  const endBtn = document.getElementById("session-end-btn");
  const jumpBtn = document.getElementById("session-jump-btn");

  if (!activeSession) {
    const otherSessions = Object.values(activeSessionsByChannel).filter(
      (session) =>
        session && (session.channel || "general") !== window.activeChannel,
    );

    if (!otherSessions.length) {
      clearEndSessionConfirm();
      sessionIndicatorTargetChannel = null;
      bar.classList.add("hidden");
      bar.classList.remove("session-elsewhere");
      return;
    }

    const target = otherSessions[0];
    const extraCount = otherSessions.length - 1;
    const targetChannel = target.channel || "general";
    const targetName = target.template_name || target.template_id || "Session";
    const channelListText = otherSessions
      .map((session) => `#${session.channel || "general"}`)
      .join(", ");

    sessionIndicatorTargetChannel = targetChannel;
    clearEndSessionConfirm();
    bar.classList.remove("hidden");
    bar.classList.add("session-elsewhere");
    templateEl.textContent =
      otherSessions.length === 1
        ? `Session active in #${targetChannel}`
        : `${otherSessions.length} sessions active elsewhere`;
    phaseEl.textContent =
      otherSessions.length === 1 ? targetName : channelListText;
    waitingEl.style.display = "none";
    if (jumpBtn) {
      jumpBtn.textContent =
        extraCount > 0
          ? `Go to #${targetChannel} (+${extraCount})`
          : `Go to #${targetChannel}`;
      jumpBtn.classList.remove("hidden");
      jumpBtn.style.display = "";
    }
    if (endBtn) endBtn.style.display = "none";
    return;
  }

  bar.classList.remove("hidden");
  bar.classList.remove("session-elsewhere");
  sessionIndicatorTargetChannel = null;
  const s = activeSession;
  const templateName = s.template_name || s.template_id || "?";
  const phaseName = s.phase_name || `Phase ${(s.current_phase || 0) + 1}`;
  const totalPhases = s.total_phases || "?";
  const phaseNum = (s.current_phase || 0) + 1;

  templateEl.textContent = templateName;
  phaseEl.textContent = `${phaseName} (${phaseNum}/${totalPhases})`;
  if (jumpBtn) {
    jumpBtn.classList.add("hidden");
    jumpBtn.style.display = "none";
  }
  if (endBtn) endBtn.style.display = "";

  const waitingAgent = s.current_agent || s.waiting_on;
  if (s.state === "waiting" && waitingAgent) {
    waitingEl.textContent = `Waiting for ${waitingAgent}`;
    waitingEl.style.display = "";
  } else if (s.state === "paused") {
    waitingEl.textContent = "Paused";
    waitingEl.style.display = "";
  } else {
    waitingEl.style.display = "none";
  }
}

function jumpToSessionChannel() {
  if (
    !sessionIndicatorTargetChannel ||
    sessionIndicatorTargetChannel === window.activeChannel
  )
    return;
  window.switchChannel(sessionIndicatorTargetChannel);
}

// ---------------------------------------------------------------------------
// Cast helpers
// ---------------------------------------------------------------------------

function _getAvailableAgents() {
  return Object.entries(window.agentConfig || {})
    .filter(([_, cfg]) => cfg.state === "active")
    .map(([name]) => name);
}

function _autoCast(roles, agents) {
  const cast = {};
  let pool = [...agents];
  for (const role of roles) {
    if (!pool.length) pool = [...agents];
    if (!pool.length) return null;
    cast[role] = pool.shift();
  }
  return cast;
}

function syncSessionCastRole(selectEl) {
  const role = selectEl?.dataset?.role;
  if (!role) return;
  const value = selectEl.value;
  document.querySelectorAll(".session-cast-select").forEach((sel) => {
    if (sel !== selectEl && sel.dataset.role === role) {
      sel.value = value;
    }
  });
}

function buildSessionCastEditor(tmpl, cast, assignees) {
  const phases = tmpl.phases || [];
  if (!phases.length) {
    return (tmpl.roles || [])
      .map((role) => {
        const assigned = cast ? cast[role] : "";
        const options = assignees
          .map(
            (a) =>
              `<option value="${window.escapeHtml(a)}" ${a === assigned ? "selected" : ""}>${window.escapeHtml(a)}</option>`,
          )
          .join("");
        return `<div class="session-cast-row">
                <span class="session-cast-role">${window.escapeHtml(role)}</span>
                <select class="session-cast-select" data-role="${window.escapeHtml(role)}" onchange="syncSessionCastRole(this)">${options}</select>
            </div>`;
      })
      .join("");
  }

  return phases
    .map((phase, idx) => {
      const participantRows = (phase.participants || [])
        .map((role) => {
          const assigned = cast ? cast[role] : "";
          const options = assignees
            .map(
              (a) =>
                `<option value="${window.escapeHtml(a)}" ${a === assigned ? "selected" : ""}>${window.escapeHtml(a)}</option>`,
            )
            .join("");
          return `<div class="session-cast-row">
                <span class="session-cast-role">${window.escapeHtml(role)}</span>
                <select class="session-cast-select" data-role="${window.escapeHtml(role)}" onchange="syncSessionCastRole(this)">${options}</select>
            </div>`;
        })
        .join("");

      return `<div class="session-cast-phase">
            <div class="session-cast-phase-head">
                <span class="session-cast-phase-num">${idx + 1}.</span>
                <span class="session-cast-phase-name">${window.escapeHtml(phase.name || `Phase ${idx + 1}`)}</span>
            </div>
            <div class="session-cast-phase-list">${participantRows}</div>
        </div>`;
    })
    .join("");
}

// ---------------------------------------------------------------------------
// Session launcher modal
// ---------------------------------------------------------------------------

function showSessionLauncher() {
  let existing = document.getElementById("session-launcher-modal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.id = "session-launcher-modal";
  modal.className = "session-launcher-overlay";
  modal.onclick = (e) => {
    if (e.target === modal) modal.remove();
  };

  let templateOptions = sessionTemplates
    .map(
      (t) =>
        `<div class="session-tmpl-card" onclick="showCastPreview('${window.escapeHtml(t.id)}')" title="${window.escapeHtml(t.description || "")}">
            ${t.is_custom ? `<span class="session-tmpl-delete-wrap"><button class="session-tmpl-delete" onclick="toggleDeleteSessionTemplateConfirm(this, '${window.escapeHtml(t.id)}', event)" title="Delete custom template">Delete</button></span>` : ""}
            <div class="session-tmpl-name">${window.escapeHtml(t.name)}</div>
            <div class="session-tmpl-desc">${window.escapeHtml(t.description || "")}</div>
            <div class="session-tmpl-roles">${(t.roles || []).map((r) => `<span class="session-role-pill">${window.escapeHtml(r)}</span>`).join(" ")}</div>
        </div>`,
    )
    .join("");

  // "Design a session" card -- lets user describe what they want and pick an agent to draft it
  const agents = _getAvailableAgents();
  const agentOptions = agents
    .map(
      (a) =>
        `<option value="${window.escapeHtml(a)}">${window.escapeHtml(a)}</option>`,
    )
    .join("");
  const designCard = `
        <div class="session-tmpl-card session-design-card">
            <div class="session-tmpl-name">+ Design a session</div>
            <div class="session-tmpl-desc">Ask an agent to draft a custom session template</div>
            <div class="session-design-row">
                <select id="session-design-agent" class="session-design-select">${agentOptions}</select>
                <input id="session-design-desc" type="text" class="session-design-input" placeholder="Describe the session you want..." />
                <button class="session-draft-btn run" onclick="sendDesignRequest()">Ask</button>
            </div>
        </div>`;

  modal.innerHTML = `
        <div class="session-launcher-dialog">
            <div class="session-launcher-header">
                <span>Start a Session</span>
                <button onclick="this.closest('.session-launcher-overlay').remove()">&times;</button>
            </div>
            <div class="session-launcher-goal">
                <input id="session-goal-input" type="text" placeholder="Goal (optional) -- what should this session achieve?" />
            </div>
            <div id="session-step-templates">
                <div class="session-launcher-templates">${templateOptions}${designCard}</div>
            </div>
            <div id="session-step-cast" class="hidden"></div>
        </div>
    `;
  document.body.appendChild(modal);
  document.getElementById("session-goal-input")?.focus();
}

async function sendDesignRequest() {
  const agent = document.getElementById("session-design-agent")?.value;
  const desc = document.getElementById("session-design-desc")?.value?.trim();
  if (!agent || !desc) return;
  const modal = document.getElementById("session-launcher-modal");
  if (modal) modal.remove();
  try {
    const res = await fetch("/api/sessions/request-draft", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        agent: agent,
        description: desc,
        channel: window.activeChannel,
        sender: window.username,
      }),
    });
    if (!res.ok)
      alert("Failed to send design request (HTTP " + res.status + ")");
  } catch (e) {
    alert("Error: " + e.message);
  }
}

function showCastPreview(templateId) {
  const tmpl = sessionTemplates.find((t) => t.id === templateId);
  if (!tmpl) return;

  const agents = _getAvailableAgents();
  const cast = _autoCast(tmpl.roles || [], agents);

  // All possible assignees: agents + "user" (self) + "none" (skip)
  const assignees = [...agents, window.username];

  const castStep = document.getElementById("session-step-cast");
  const tmplStep = document.getElementById("session-step-templates");
  if (!castStep || !tmplStep) return;

  tmplStep.classList.add("hidden");
  castStep.classList.remove("hidden");

  const roleRows = buildSessionCastEditor(tmpl, cast, assignees);

  castStep.innerHTML = `
        <div class="session-cast-header">
            <button class="session-back-btn" onclick="sessionCastBack()">&larr;</button>
            <span>${window.escapeHtml(tmpl.name)} -- Cast</span>
        </div>
        <div class="session-cast-list">${roleRows}</div>
        <button class="session-start-btn" onclick="launchSessionWithCast('${window.escapeHtml(templateId)}')">Start Session</button>
    `;
}

function sessionCastBack() {
  const castStep = document.getElementById("session-step-cast");
  const tmplStep = document.getElementById("session-step-templates");
  if (castStep) castStep.classList.add("hidden");
  if (tmplStep) tmplStep.classList.remove("hidden");
}

async function launchSessionWithCast(templateId) {
  const goalInput = document.getElementById("session-goal-input");
  const goal = goalInput ? goalInput.value.trim() : "";

  // Read cast from dropdowns
  const cast = {};
  document
    .querySelectorAll("#session-step-cast .session-cast-select")
    .forEach((sel) => {
      cast[sel.dataset.role] = sel.value;
    });

  const modal = document.getElementById("session-launcher-modal");
  if (modal) modal.remove();

  try {
    const res = await fetch("/api/sessions/start", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        template_id: templateId,
        channel: window.activeChannel,
        cast: cast,
        goal: goal,
        started_by: window.username,
      }),
    });
    if (!res.ok) {
      const data = await res.json();
      alert(data.error || "Failed to start session");
    }
  } catch (e) {
    alert("Error starting session: " + e.message);
  }
}

// ---------------------------------------------------------------------------
// End session
// ---------------------------------------------------------------------------

function clearEndSessionConfirm() {
  const btn = document.getElementById("session-end-btn");
  const confirm = document.getElementById("session-end-confirm");
  if (confirm) confirm.remove();
  if (btn) {
    btn.textContent = "End Session";
    btn.classList.remove("confirming");
  }
}

function toggleEndSessionConfirm(event) {
  event.stopPropagation();
  const btn = event.currentTarget;
  const controls = document.getElementById("session-end-controls");
  const existing = document.getElementById("session-end-confirm");
  if (existing) {
    clearEndSessionConfirm();
    return;
  }

  btn.textContent = "End Session?";
  btn.classList.add("confirming");

  const confirmWrap = document.createElement("span");
  confirmWrap.id = "session-end-confirm";
  confirmWrap.className = "session-inline-confirm";
  confirmWrap.innerHTML = `
        <button class="session-inline-confirm-yes ch-confirm-yes" title="Confirm end session">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3.5 3.5 6.5-7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <button class="session-inline-confirm-no ch-confirm-no" title="Cancel">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
        </button>
    `;
  (controls || btn.parentElement || btn).appendChild(confirmWrap);

  confirmWrap.querySelector(".session-inline-confirm-yes").onclick = async (
    e,
  ) => {
    e.stopPropagation();
    await endActiveSession();
  };
  confirmWrap.querySelector(".session-inline-confirm-no").onclick = (e) => {
    e.stopPropagation();
    clearEndSessionConfirm();
  };
}

async function endActiveSession() {
  if (!activeSession) return;

  try {
    const res = await fetch(`/api/sessions/${activeSession.id}/end`, {
      method: "POST",
    });
    if (!res.ok) {
      const data = await res.json();
      alert(data.error || "Failed to end session");
    }
  } catch (e) {
    alert("Error ending session: " + e.message);
  } finally {
    clearEndSessionConfirm();
  }
}

// ---------------------------------------------------------------------------
// Session Drafts
// ---------------------------------------------------------------------------

function _isSupersededDraft(draftId, revision) {
  if (!draftId) return false;
  const allDrafts = document.querySelectorAll(".session-draft-card");
  for (const card of allDrafts) {
    if (
      card.dataset.draftId === draftId &&
      parseInt(card.dataset.draftRevision || "0") > revision
    ) {
      return true;
    }
  }
  return false;
}

function _supersedePreviousDrafts(draftId, currentRevision) {
  if (!draftId) return;
  const allDrafts = document.querySelectorAll(".session-draft-card");
  for (const card of allDrafts) {
    if (
      card.dataset.draftId === draftId &&
      parseInt(card.dataset.draftRevision || "0") < currentRevision
    ) {
      if (!card.classList.contains("session-draft-superseded")) {
        card.classList.add("session-draft-superseded");
        card.innerHTML = _renderSessionDraftResolvedCard(
          `Superseded (rev ${card.dataset.draftRevision})`,
          "A newer revision is now the active draft.",
        );
      }
    }
  }
}

function runDraft(msgId) {
  const el = document.querySelector(`.message[data-id="${msgId}"]`);
  if (!el || !el.dataset.draftTemplate) return;
  const tmpl = JSON.parse(el.dataset.draftTemplate);

  // Open the cast preview modal with draft context
  showDraftCastPreview(tmpl, msgId);
}

function showDraftCastPreview(tmpl, draftMsgId) {
  const agents = _getAvailableAgents();
  const cast = _autoCast(tmpl.roles || [], agents);
  const assignees = [...agents, window.username];

  let existing = document.getElementById("session-launcher-modal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.id = "session-launcher-modal";
  modal.className = "session-launcher-overlay";
  modal.onclick = (e) => {
    if (e.target === modal) modal.remove();
  };

  const roleRows = buildSessionCastEditor(tmpl, cast, assignees);

  modal.innerHTML = `
        <div class="session-launcher-dialog">
            <div class="session-launcher-header">
                <span>${window.escapeHtml(tmpl.name || "?")} -- Cast</span>
                <button onclick="this.closest('.session-launcher-overlay').remove()">&times;</button>
            </div>
            <div class="session-launcher-goal">
                <input id="session-goal-input" type="text" placeholder="Goal (optional)" />
            </div>
            <div id="session-step-cast">
                <div class="session-cast-list">${roleRows}</div>
                <button class="session-start-btn" onclick="launchDraftSession(${draftMsgId})">Start Session</button>
            </div>
        </div>
    `;
  document.body.appendChild(modal);
}

async function launchDraftSession(draftMsgId) {
  const goalInput = document.getElementById("session-goal-input");
  const goal = goalInput ? goalInput.value.trim() : "";

  const cast = {};
  document
    .querySelectorAll("#session-step-cast .session-cast-select")
    .forEach((sel) => {
      cast[sel.dataset.role] = sel.value;
    });

  const modal = document.getElementById("session-launcher-modal");
  if (modal) modal.remove();

  try {
    const res = await fetch("/api/sessions/start", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        draft_message_id: draftMsgId,
        channel: window.activeChannel,
        cast: cast,
        goal: goal,
        started_by: window.username,
      }),
    });
    if (!res.ok) {
      const data = await res.json();
      alert(data.error || "Failed to start session from draft");
    }
  } catch (e) {
    alert("Error: " + e.message);
  }
}

async function saveDraft(msgId, btn) {
  try {
    const res = await fetch("/api/sessions/save-draft", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message_id: msgId }),
    });
    if (res.ok) {
      if (btn) {
        btn.textContent = "Saved";
        btn.disabled = true;
        btn.classList.add("saved");
      }
      fetchSessionTemplates();
    } else {
      const data = await res.json();
      alert(data.error || "Failed to save template");
    }
  } catch (e) {
    alert("Error: " + e.message);
  }
}

function clearSessionTemplateDeleteConfirms() {
  document
    .querySelectorAll(".session-tmpl-delete-confirm")
    .forEach((el) => el.remove());
  document
    .querySelectorAll(".session-tmpl-delete.confirming")
    .forEach((btn) => {
      btn.classList.remove("confirming");
      btn.textContent = "Delete";
    });
}

function toggleDeleteSessionTemplateConfirm(btn, templateId, event) {
  if (event) event.stopPropagation();
  const wrap = btn.closest(".session-tmpl-delete-wrap");
  const existing = wrap?.querySelector(".session-tmpl-delete-confirm");
  if (existing) {
    clearSessionTemplateDeleteConfirms();
    return;
  }

  clearSessionTemplateDeleteConfirms();
  btn.classList.add("confirming");
  btn.textContent = "Delete?";

  const confirmWrap = document.createElement("span");
  confirmWrap.className = "session-tmpl-delete-confirm";
  confirmWrap.innerHTML = `
        <button class="session-inline-confirm-yes ch-confirm-yes" title="Confirm delete">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3.5 3.5 6.5-7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <button class="session-inline-confirm-no ch-confirm-no" title="Cancel">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
        </button>
    `;
  wrap.appendChild(confirmWrap);

  confirmWrap.querySelector(".session-inline-confirm-yes").onclick = async (
    e,
  ) => {
    e.stopPropagation();
    await deleteSessionTemplate(templateId);
  };
  confirmWrap.querySelector(".session-inline-confirm-no").onclick = (e) => {
    e.stopPropagation();
    clearSessionTemplateDeleteConfirms();
  };
}

async function deleteSessionTemplate(templateId) {
  try {
    const res = await fetch(
      `/api/sessions/templates/${encodeURIComponent(templateId)}`,
      {
        method: "DELETE",
      },
    );
    if (res.ok) {
      await fetchSessionTemplates();
      showSessionLauncher();
    } else {
      const data = await res.json();
      alert(data.error || "Failed to delete template");
    }
  } catch (e) {
    alert("Error: " + e.message);
  } finally {
    clearSessionTemplateDeleteConfirms();
  }
}

function requestDraftChanges(draftId, proposedBy, msgId) {
  const el = document.querySelector(`.message[data-id="${msgId}"]`);
  if (!el) return;
  const actions =
    el.querySelector(".proposal-actions") ||
    el.querySelector(".session-draft-actions");
  if (!actions) return;

  // Only allow one inline editor per draft card.
  const existingInputs = el.querySelectorAll(".draft-changes-input");
  if (existingInputs.length) {
    existingInputs.forEach((row, idx) => {
      if (idx > 0) row.remove();
    });
    existingInputs[0].querySelector("textarea")?.focus();
    return;
  }

  const inputRow = document.createElement("div");
  inputRow.className = "draft-changes-input";
  inputRow.innerHTML = `
        <textarea class="draft-changes-textarea" rows="2" placeholder="What changes do you want?"></textarea>
        <div class="draft-changes-btns">
            <button class="session-draft-btn run" onclick="submitDraftChanges('${window.escapeHtml(draftId)}', '${window.escapeHtml(proposedBy)}', ${msgId})">Send</button>
            <button class="session-draft-btn dismiss" onclick="dismissDraftChanges(this)">Cancel</button>
        </div>
    `;
  actions.after(inputRow);
  const ta = inputRow.querySelector("textarea");
  ta.focus();
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitDraftChanges(draftId, proposedBy, msgId);
    }
    if (e.key === "Escape") inputRow.remove();
  });
}

function submitDraftChanges(draftId, proposedBy, msgId) {
  const el = document.querySelector(`.message[data-id="${msgId}"]`);
  if (!el) return;
  const inputRow = el.querySelector(".draft-changes-input");
  const ta = inputRow?.querySelector("textarea");
  const feedback = ta?.value?.trim();
  if (!feedback) return;

  const tmplJson = el.dataset.draftTemplate || "";
  const text = `@${proposedBy} Please revise session draft [${draftId}]: ${feedback}\n\nCurrent draft:\n\`\`\`session\n${tmplJson}\n\`\`\``;
  if (window.ws && window.ws.readyState === WebSocket.OPEN) {
    window.ws.send(
      JSON.stringify({
        type: "message",
        text: text,
        sender: window.username,
        channel: window.activeChannel,
      }),
    );
  } else {
    alert("Connection lost. Reconnect and try again.");
    return;
  }
  if (inputRow) inputRow.remove();
}

function dismissDraft(msgId) {
  fetch(`/api/messages/${msgId}/demote`, {
    method: "POST",
  })
    .then((res) => {
      if (!res.ok)
        alert("Failed to dismiss session proposal (HTTP " + res.status + ")");
    })
    .catch((e) => {
      console.error("Failed to demote session draft:", e);
      alert("Failed to dismiss session proposal");
    });
}

function dismissDraftChanges(btn) {
  const row = btn.closest(".draft-changes-input");
  if (row) row.remove();
}

function highlightSessionOutput(messageId) {
  const el = document.querySelector(`.message[data-id="${messageId}"]`);
  if (el) el.classList.add("session-output");
}

function scrollToSessionOutput(messageId) {
  const el = document.querySelector(`.message[data-id="${messageId}"]`);
  if (el) {
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("session-output");
    el.classList.add("session-output-flash");
    setTimeout(() => el.classList.remove("session-output-flash"), 2000);
  }
}

// ---------------------------------------------------------------------------
// Hub subscription -- handle session WebSocket events
// ---------------------------------------------------------------------------

Hub.on("session", function (event) {
  handleSessionEvent(event.action, event.data);
});

Hub.on("channel_renamed", function (event) {
  // Migrate session cache key so Store watcher resolves the correct session
  if (activeSessionsByChannel[event.old_name]) {
    activeSessionsByChannel[event.new_name] =
      activeSessionsByChannel[event.old_name];
    delete activeSessionsByChannel[event.old_name];
  }
});

// ---------------------------------------------------------------------------
// Store integration -- react to channel changes
// ---------------------------------------------------------------------------

Store.watch("activeChannel", function (newChannel) {
  activeSession = activeSessionsByChannel[newChannel] || null;
  updateSessionBar();
  fetchActiveSession(newChannel);
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function _sessionsInit() {
  fetchSessionTemplates();
  fetchAllActiveSessions();
  activeSession = null;
  updateSessionBar();
  fetchActiveSession();
}

// ---------------------------------------------------------------------------
// Window exports (for inline onclick handlers in generated HTML)
// ---------------------------------------------------------------------------

window.showSessionLauncher = showSessionLauncher;
window.runDraft = runDraft;
window.saveDraft = saveDraft;
window.dismissDraft = dismissDraft;
window.dismissDraftChanges = dismissDraftChanges;
window.requestDraftChanges = requestDraftChanges;
window.submitDraftChanges = submitDraftChanges;
window.toggleEndSessionConfirm = toggleEndSessionConfirm;
window.jumpToSessionChannel = jumpToSessionChannel;
window.showCastPreview = showCastPreview;
window.sessionCastBack = sessionCastBack;
window.launchSessionWithCast = launchSessionWithCast;
window.launchDraftSession = launchDraftSession;
window.sendDesignRequest = sendDesignRequest;
window.syncSessionCastRole = syncSessionCastRole;

window.toggleDeleteSessionTemplateConfirm = toggleDeleteSessionTemplateConfirm;
window.scrollToSessionOutput = scrollToSessionOutput;

window.Sessions = { init: _sessionsInit };
