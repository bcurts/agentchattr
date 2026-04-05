// rules-panel.js -- Rules sidebar panel: render, CRUD, drag/drop, badges
// Extracted from chat.js PR 5.  Reads shared state via window.* bridges.

"use strict";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const RULE_MAX_CHARS = 160;
const RULE_REASON_MAX_CHARS = 240;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function autoGrowTextarea(el) {
  el.style.height = "auto";
  el.style.height = el.scrollHeight + "px";
}

function setupCharCounter(textareaId, counterId) {
  const ta = document.getElementById(textareaId);
  const counter = document.getElementById(counterId);
  if (!ta || !counter) return;

  function update() {
    autoGrowTextarea(ta);
    const len = ta.value.length;
    counter.textContent = `${len}/${RULE_MAX_CHARS}`;
    counter.classList.toggle("over", len >= RULE_MAX_CHARS);
  }
  ta.addEventListener("input", update);
  update();
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

function setupRulesGrip() {
  const grip = document.getElementById("rules-grip");
  const panel = document.getElementById("rules-panel");
  if (!grip || !panel) return;

  let dragging = false;
  let startX = 0;
  let startWidth = 0;

  grip.addEventListener("mousedown", (e) => {
    e.preventDefault();
    dragging = true;
    startX = e.clientX;
    startWidth = panel.offsetWidth;
    grip.classList.add("dragging");
    panel.style.transition = "none";
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });

  document.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const delta = startX - e.clientX;
    const newWidth = Math.min(
      Math.max(startWidth + delta, 220),
      window.innerWidth * 0.5,
    );
    panel.style.setProperty("--panel-w", newWidth + "px");
    panel.style.width = newWidth + "px";
    panel.style.minWidth = newWidth + "px";
  });

  document.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    grip.classList.remove("dragging");
    panel.style.transition = "";
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  });
}

function setupRuleForm() {
  // Form is now inline via showCreateRule(), no persistent elements to set up
}

// ---------------------------------------------------------------------------
// State (local to rules-panel)
// ---------------------------------------------------------------------------

let _rulesArchivedExpanded = false;
let _draggedRuleId = null;
let _seenRuleIds = new Set();

// ---------------------------------------------------------------------------
// Core
// ---------------------------------------------------------------------------

function handleRuleEvent(action, rule) {
  const rules = window.rules;
  if (action === "propose") {
    rules.push(rule);
  } else if (["activate", "deactivate", "edit", "approve"].includes(action)) {
    const idx = rules.findIndex((r) => r.id === rule.id);
    if (idx >= 0) rules[idx] = rule;
  } else if (action === "delete") {
    window.rules = rules.filter((r) => r.id !== rule.id);
    _seenRuleIds.delete(rule.id);
  }
  renderRulesPanel();
  updateRulesBadge();
}

function toggleRulesPanel() {
  window._preserveScroll(() => {
    const panel = document.getElementById("rules-panel");
    panel.classList.toggle("hidden");
    document
      .getElementById("rules-toggle")
      .classList.toggle("active", !panel.classList.contains("hidden"));
    if (!panel.classList.contains("hidden")) {
      // Mark all current drafts as seen
      for (const r of window.rules) {
        if (r.status === "proposed" || r.status === "draft")
          _seenRuleIds.add(r.id);
      }
      updateRulesBadge();
      renderRulesPanel();
    }
  });
}

function remindAgents() {
  const btn = document.querySelector(".rules-remind-btn");
  if (btn) btn.disabled = true;
  fetch("/api/rules/remind", { method: "POST" })
    .then((r) => r.json())
    .then(() => {
      if (btn) {
        const orig = btn.textContent;
        btn.textContent = "Queued — next trigger";
        setTimeout(() => {
          btn.textContent = orig;
          btn.disabled = false;
        }, 2500);
      }
    })
    .catch((err) => {
      console.error("remind failed", err);
      if (btn) btn.disabled = false;
    });
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function renderRulesPanel() {
  const rules = window.rules;
  const list = document.getElementById("rules-list");
  if (!list) return;

  // Preserve in-progress create form across re-renders
  const activeForm = list.querySelector(".job-create-form");
  let savedForm = null,
    savedFocusSelector = null,
    savedSelStart = 0,
    savedSelEnd = 0;
  if (activeForm) {
    const focused = document.activeElement;
    if (focused && activeForm.contains(focused)) {
      savedFocusSelector =
        focused.tagName.toLowerCase() +
        (focused.className ? "." + focused.className.split(" ")[0] : "");
      savedSelStart = focused.selectionStart || 0;
      savedSelEnd = focused.selectionEnd || 0;
    }
    savedForm = activeForm.parentNode.removeChild(activeForm);
  }
  list.innerHTML = "";

  const normalize = (s) =>
    s === "proposed" ? "draft" : s === "approved" ? "active" : s || "draft";
  // Filter out pending rules — they only exist as proposal cards in the timeline
  const panelRules = rules.filter((r) => r.status !== "pending");
  const activeCount = panelRules.filter(
    (r) => normalize(r.status) === "active",
  ).length;

  // Update header counter
  const counter = document.getElementById("rules-counter");
  if (counter) counter.textContent = `${activeCount}/10`;

  if (panelRules.length === 0) {
    const ghost = document.createElement("div");
    ghost.className = "sb-ghost-card";
    ghost.innerHTML = `
            <div class="sb-ghost-title">No rules yet</div>
            <div class="sb-ghost-meta">Tell your agents how to work</div>
        `;
    ghost.onclick = () => showCreateRule();
    list.appendChild(ghost);
    // Centered helper text
    const helper = document.createElement("div");
    helper.className = "rules-centered-hint";
    helper.textContent = "New rules are sent on the next agent trigger";
    list.appendChild(helper);
    if (savedForm) {
      list.prepend(savedForm);
      if (savedFocusSelector) {
        const el = savedForm.querySelector(savedFocusSelector);
        if (el) {
          el.focus();
          try {
            el.setSelectionRange(savedSelStart, savedSelEnd);
          } catch {}
        }
      }
    }
    return;
  }

  // Group order: drafts, active, archive (mirrors jobs: to-do, active, closed)
  const groups = [
    { key: "draft", label: "DRAFTS", items: [] },
    { key: "active", label: "ACTIVE", items: [] },
    { key: "archived", label: "ARCHIVE", items: [] },
  ];
  for (const r of panelRules) {
    const status = normalize(r.status);
    const g = groups.find((g) => g.key === status);
    if (g) g.items.push(r);
    else groups[2].items.push(r);
  }

  // Soft warning at 7+ active
  if (activeCount >= 7) {
    const warning = document.createElement("div");
    warning.className = "rules-soft-warning";
    warning.textContent = "Less than seven active rules tends to work better";
    list.appendChild(warning);
  }

  for (const group of groups) {
    group.items.sort((a, b) => b.id - a.id);

    const isCollapsible = group.key === "archived";
    const isCollapsed = isCollapsible && !_rulesArchivedExpanded;

    const header = document.createElement("div");
    header.className =
      "rules-group-header " + group.key + (isCollapsed ? " collapsed" : "");
    const isEmpty = group.items.length === 0;
    header.textContent = isEmpty
      ? group.label
      : `${group.label} (${group.items.length})`;
    if (isEmpty) header.classList.add("empty-group");
    if (isCollapsible) {
      header.onclick = () => {
        _rulesArchivedExpanded = !_rulesArchivedExpanded;
        header.classList.toggle("collapsed");
        const container = header.nextElementSibling;
        if (container) container.classList.toggle("collapsed");
      };
    }

    // Drop target: drag a rule card onto this header to change its status
    header.addEventListener("dragover", (e) => {
      if (!_draggedRuleId) return;
      e.preventDefault();
      header.classList.add("drop-target");
    });
    header.addEventListener("dragleave", () => {
      header.classList.remove("drop-target");
    });
    header.addEventListener("drop", (e) => {
      e.preventDefault();
      header.classList.remove("drop-target");
      if (!_draggedRuleId) return;
      const id = _draggedRuleId;
      const d = rules.find((r) => r.id === id);
      if (!d) return;
      const currentStatus = normalize(d.status);
      if (currentStatus === group.key) return;
      if (group.key === "active") {
        window.ws.send(JSON.stringify({ type: "rule_activate", id }));
      } else if (group.key === "draft") {
        window.ws.send(JSON.stringify({ type: "rule_make_draft", id }));
      } else {
        window.ws.send(JSON.stringify({ type: "rule_deactivate", id }));
      }
    });

    list.appendChild(header);

    const itemsContainer = document.createElement("div");
    itemsContainer.className =
      "rules-group-items" + (isCollapsed ? " collapsed" : "");
    const itemsInner = document.createElement("div");
    itemsInner.className = "rules-group-items-inner";

    for (const d of group.items) {
      const card = document.createElement("div");
      card.className = "rule-card";
      card.dataset.id = d.id;
      card.draggable = true;
      card.onclick = () => editRule(d.id);

      card.addEventListener("dragstart", (e) => {
        _draggedRuleId = d.id;
        card.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
      });
      card.addEventListener("dragend", () => {
        _draggedRuleId = null;
        card.classList.remove("dragging");
      });

      const displayStatus = normalize(d.status);

      card.innerHTML = `
                <div class="rule-card-header">
                    <span class="rule-status-dot ${displayStatus}"></span>
                    <div class="rule-text">${window.escapeHtml(d.text || d.decision || "")}</div>
                </div>
            `;
      itemsInner.appendChild(card);
    }

    // Trash zone for archived rules — same style as Jobs
    if (group.key === "archived" && group.items.length > 0) {
      const trashZone = document.createElement("div");
      trashZone.className = "archive-trash-zone visible";
      trashZone.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M6 4V3h4v1M5 4v8.5h6V4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="archive-trash-hint">Drag here to delete</span>`;

      trashZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        trashZone.classList.add("hover");
      });
      trashZone.addEventListener("dragleave", () => {
        trashZone.classList.remove("hover");
      });
      trashZone.addEventListener("drop", async (e) => {
        e.preventDefault();
        trashZone.classList.remove("hover");
        if (!_draggedRuleId) return;
        const id = _draggedRuleId;
        const d = rules.find((r) => r.id === id);
        if (!d) return;
        trashZone.classList.add("chomping");
        window.ws.send(JSON.stringify({ type: "rule_delete", id }));
        setTimeout(() => trashZone.classList.remove("chomping"), 500);
      });

      itemsInner.appendChild(trashZone);
    }

    itemsContainer.appendChild(itemsInner);
    list.appendChild(itemsContainer);
  }

  // Centered helper text at bottom
  const helper = document.createElement("div");
  helper.className = "rules-centered-hint";
  helper.textContent = "New rules are sent on the next agent trigger";
  list.appendChild(helper);

  // Re-insert preserved create form at top
  if (savedForm) {
    list.prepend(savedForm);
    if (savedFocusSelector) {
      const el = savedForm.querySelector(savedFocusSelector);
      if (el) {
        el.focus();
        try {
          el.setSelectionRange(savedSelStart, savedSelEnd);
        } catch {}
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Badge
// ---------------------------------------------------------------------------

function updateRulesBadge() {
  const rules = window.rules;
  const badge = document.getElementById("rules-badge");
  if (!badge) return;
  // Only count unseen proposals — not all drafts
  const panel = document.getElementById("rules-panel");
  const panelOpen = panel && !panel.classList.contains("hidden");
  if (panelOpen) {
    // Panel is open — everything is seen
    for (const r of rules) {
      if (r.status === "proposed" || r.status === "draft")
        _seenRuleIds.add(r.id);
    }
  }
  const count = rules.filter(
    (r) =>
      (r.status === "proposed" || r.status === "draft") &&
      !_seenRuleIds.has(r.id),
  ).length;
  badge.textContent = count;
  badge.classList.toggle("hidden", count === 0);
}

// ---------------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------------

function showCreateRule() {
  const list = document.getElementById("rules-list");
  if (!list) return;
  const existing = list.querySelector(".job-create-form");
  if (existing) {
    existing.remove();
    return;
  }

  const form = document.createElement("div");
  form.className = "job-create-form";
  form.innerHTML = `
        <input type="text" placeholder="Write a short rule agents should follow" class="rule-create-text" maxlength="160" autofocus>
        <div class="job-create-actions">
            <button class="cancel-btn" onclick="this.closest('.job-create-form').remove()">Cancel</button>
            <button class="create-btn" onclick="submitCreateRule(this)">Create</button>
        </div>
    `;
  list.prepend(form);
  const textInput = form.querySelector(".rule-create-text");
  textInput.focus();
  textInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submitCreateRule(form.querySelector(".create-btn"));
    } else if (e.key === "Escape") {
      form.remove();
    }
  });
}

function submitCreateRule(btn) {
  const form = btn.closest(".job-create-form");
  const textInput = form.querySelector(".rule-create-text");
  const text = (textInput.value || "").trim();
  if (!text) {
    textInput.focus();
    return;
  }

  window.ws.send(
    JSON.stringify({
      type: "rule_propose",
      text: text,
      author: window.username,
      channel: window.activeChannel,
    }),
  );
  form.remove();
}

function toggleRuleStatus(id) {
  const rules = window.rules;
  const d = rules.find((r) => r.id === id);
  if (!d) return;

  if (d.status === "active" || d.status === "approved") {
    window.ws.send(JSON.stringify({ type: "rule_deactivate", id }));
  } else {
    window.ws.send(JSON.stringify({ type: "rule_activate", id }));
  }
}

function editRule(id) {
  const rules = window.rules;
  const d = rules.find((r) => r.id === id);
  if (!d) return;

  const card = document.querySelector(`.rule-card[data-id="${id}"]`);
  if (!card || card.classList.contains("editing")) return;
  card.classList.add("editing");

  const editArea = document.createElement("div");
  editArea.className = "rule-edit-area";
  editArea.onclick = (e) => e.stopPropagation();
  editArea.innerHTML = `
        <textarea class="rule-edit-field" maxlength="${RULE_MAX_CHARS}" rows="1" data-limit="${RULE_MAX_CHARS}">${window.escapeHtml(d.text || "")}</textarea>
        <div class="char-counter">${(d.text || "").length}/${RULE_MAX_CHARS}</div>
        <div class="rule-edit-actions">
            <button class="save-btn" onclick="event.stopPropagation();saveRuleEdit(${id})">Save</button>
            <button class="cancel-btn" onclick="event.stopPropagation();cancelRuleEdit(${id})">Cancel</button>
            <span style="flex:1"></span>
            <button class="delete-inline-btn" onclick="event.stopPropagation();deleteRule(${id})">Delete</button>
        </div>
    `;
  card.appendChild(editArea);

  // Wire auto-grow + counters on edit fields
  editArea.querySelectorAll(".rule-edit-field").forEach((ta) => {
    const counter = ta.nextElementSibling;
    const limit = parseInt(ta.dataset.limit) || RULE_MAX_CHARS;
    autoGrowTextarea(ta);
    ta.addEventListener("input", () => {
      autoGrowTextarea(ta);
      if (counter && counter.classList.contains("char-counter")) {
        counter.textContent = `${ta.value.length}/${limit}`;
        counter.classList.toggle("over", ta.value.length >= limit);
      }
    });
  });

  // Focus the textarea, cursor at end
  const firstField = editArea.querySelector("textarea");
  firstField.focus();
  firstField.selectionStart = firstField.selectionEnd = firstField.value.length;
}

function saveRuleEdit(id) {
  const card = document.querySelector(`.rule-card[data-id="${id}"]`);
  if (!card) return;

  const field = card.querySelector(".rule-edit-field");
  const newText = field?.value.trim();

  if (!newText) return;

  window.ws.send(
    JSON.stringify({
      type: "rule_edit",
      id,
      text: newText,
    }),
  );
}

function cancelRuleEdit(id) {
  const card = document.querySelector(`.rule-card[data-id="${id}"]`);
  if (!card) return;
  card.classList.remove("editing");
  const editArea = card.querySelector(".rule-edit-area");
  if (editArea) editArea.remove();
}

function startDeleteRule(id) {
  const card = document.querySelector(`.rule-card[data-id="${id}"]`);
  if (!card) return;
  const actions = card.querySelector(".rule-actions");
  if (!actions || actions.dataset.confirming) return;
  actions.dataset.confirming = "1";
  actions.style.opacity = "1";
  actions.innerHTML = `
        <span style="font-size:11px;color:var(--error-color);white-space:nowrap;margin-right:4px">Delete?</span>
        <button class="confirm-yes" style="background:var(--error-color);color:#fff;border:none;border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600;cursor:pointer;font-family:inherit" onclick="deleteRule(${id})">Yes</button>
        <button class="confirm-no" style="background:transparent;color:var(--text-dim);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600;cursor:pointer;font-family:inherit" onclick="cancelDeleteRule(${id})">No</button>
    `;
}

function deleteRule(id) {
  const rules = window.rules;
  const d = rules.find((r) => r.id === id);
  window.ws.send(JSON.stringify({ type: "rule_delete", id }));

  // Prefill a rejection message to the proposer
  const author = d?.author || d?.owner;
  if (d && author && author.toLowerCase() !== window.username.toLowerCase()) {
    const input = document.getElementById("input");
    const reasonBit = d.reason ? ` (reason: ${d.reason})` : "";
    input.value = `@${author} Rule rejected: "${d.text || d.decision || ""}"${reasonBit} — `;
    input.focus();
    input.selectionStart = input.selectionEnd = input.value.length;
    input.dispatchEvent(new Event("input"));
  }
}

function cancelDeleteRule(id) {
  renderRulesPanel();
}

// ---------------------------------------------------------------------------
// Proposal resolve / dismiss
// ---------------------------------------------------------------------------

async function resolveRuleProposal(msgId, action) {
  try {
    await fetch(`/api/messages/${msgId}/resolve_rule_proposal`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
  } catch (e) {
    console.error("Failed to resolve rule proposal:", e);
  }
}

async function dismissRuleProposal(msgId) {
  // Demote to regular chat message — same as job proposal dismiss
  try {
    await fetch(`/api/messages/${msgId}/demote_rule_proposal`, {
      method: "POST",
    });
  } catch (e) {
    console.error("Failed to dismiss rule proposal:", e);
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function _rulesPanelInit() {
  setupRuleForm();
  setupRulesGrip();
}

// ---------------------------------------------------------------------------
// Window exports (for inline onclick in index.html and chat.js callers)
// ---------------------------------------------------------------------------

window.handleRuleEvent = handleRuleEvent;
window.renderRulesPanel = renderRulesPanel;
window.updateRulesBadge = updateRulesBadge;
window.toggleRulesPanel = toggleRulesPanel;
window.remindAgents = remindAgents;
window.showCreateRule = showCreateRule;
window.submitCreateRule = submitCreateRule;
window.toggleRuleStatus = toggleRuleStatus;
window.editRule = editRule;
window.saveRuleEdit = saveRuleEdit;
window.cancelRuleEdit = cancelRuleEdit;
window.startDeleteRule = startDeleteRule;
window.deleteRule = deleteRule;
window.cancelDeleteRule = cancelDeleteRule;
window.resolveRuleProposal = resolveRuleProposal;
window.dismissRuleProposal = dismissRuleProposal;
window.RulesPanel = { init: _rulesPanelInit };
