/**
 * launcher.js -- Agent Launcher panel UI module
 *
 * Depends on core.js (Hub) loaded first.
 * Uses escapeHtml() from chat.js for ALL user content.
 *
 * Owns all launcher state, rendering, and interaction logic.
 * Subscribes to Hub for WS events.
 *
 * NOTE: All user-provided strings are sanitised via escapeHtml() before
 * insertion into the DOM, following the same XSS-prevention pattern used
 * throughout the codebase (jobs.js, chat.js, rules-panel.js).
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let launcherDefinitions = {}; // { name: { command, color, label, cwd } }
let launcherFlagPresets = {}; // { base: [{ label, flag }] }
let launcherProcesses = []; // [{ name, base, state, pid, flags, cwd, started_at }]
let launcherLogs = {}; // { name: ["line1", ...] }
let launcherLogsOpen = {}; // { name: bool }
let launcherConfigOpen = {}; // { base: bool }
let _selectedColour = "#7c3aed";

const COLOUR_SWATCHES = [
  "#7c3aed",
  "#3b82f6",
  "#06b6d4",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#ec4899",
  "#8b5cf6",
];

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function fetchDefinitions() {
  try {
    const res = await fetch("/api/agent-definitions");
    if (!res.ok) return;
    const data = await res.json();
    launcherDefinitions = data.definitions || {};
    launcherFlagPresets = data.flag_presets || {};
    renderLauncherPanel();
  } catch (e) {
    console.error("[launcher] fetchDefinitions error:", e);
  }
}

async function fetchManagedAgents() {
  try {
    const res = await fetch("/api/agents/managed");
    if (!res.ok) return;
    const data = await res.json();
    launcherProcesses = data.processes || [];
    renderLauncherPanel();
  } catch (e) {
    console.error("[launcher] fetchManagedAgents error:", e);
  }
}

// ---------------------------------------------------------------------------
// Panel toggle
// ---------------------------------------------------------------------------

function toggleLauncherPanel() {
  const panel = document.getElementById("launcher-panel");
  const btn = document.getElementById("launcher-toggle");
  if (!panel) return;
  const isHidden = panel.classList.toggle("hidden");
  if (btn) btn.classList.toggle("active", !isHidden);
  if (!isHidden) {
    fetchDefinitions();
    fetchManagedAgents();
  }
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function renderLauncherPanel() {
  const list = document.getElementById("launcher-list");
  if (!list) return;

  const esc =
    window.escapeHtml ||
    function (s) {
      return String(s);
    };

  // Group processes by base
  const procsByBase = {};
  for (const p of launcherProcesses) {
    const base = p.base || p.name;
    if (!procsByBase[base]) procsByBase[base] = [];
    procsByBase[base].push(p);
  }

  // Render cards for each definition
  const bases = Object.keys(launcherDefinitions);
  if (bases.length === 0 && launcherProcesses.length === 0) {
    list.textContent = "";
    const empty = document.createElement("div");
    empty.style.cssText =
      "padding:16px;color:var(--text-dim);font-size:12px;text-align:center;";
    empty.textContent = "No agents defined. Click + Add Agent to get started.";
    list.appendChild(empty);
    return;
  }

  // Build new content in a fragment to minimise reflows
  const frag = document.createDocumentFragment();

  for (const base of bases) {
    const def = launcherDefinitions[base];
    const instances = procsByBase[base] || [];
    frag.appendChild(buildAgentCard(base, def, instances));
  }

  // Render any processes whose base isn't in definitions
  for (const base of Object.keys(procsByBase)) {
    if (!launcherDefinitions[base]) {
      const instances = procsByBase[base];
      frag.appendChild(
        buildAgentCard(
          base,
          { command: "", color: "#888", label: base },
          instances,
        ),
      );
    }
  }

  list.textContent = "";
  list.appendChild(frag);
}

function buildAgentCard(base, def, instances) {
  const esc =
    window.escapeHtml ||
    function (s) {
      return String(s);
    };
  const el = document.createElement("div");
  el.className = "launcher-card";

  const running = instances.filter(function (p) {
    return p.state === "running";
  });
  const crashed = instances.filter(function (p) {
    return p.state === "crashed";
  });
  const hasRunning = running.length > 0;
  const hasCrashed = crashed.length > 0;

  // Header: dot + info + badge
  var header = document.createElement("div");
  header.className = "launcher-card-header";

  var dot = document.createElement("span");
  dot.className =
    "launcher-dot " +
    (hasRunning ? "online" : hasCrashed ? "crashed" : "offline");
  header.appendChild(dot);

  var info = document.createElement("div");
  info.className = "launcher-card-info";
  var nameDiv = document.createElement("div");
  nameDiv.className = "launcher-card-name";
  if (def.color) nameDiv.style.color = def.color;
  nameDiv.textContent = def.label || base;
  info.appendChild(nameDiv);
  if (def.command) {
    var cmdDiv = document.createElement("div");
    cmdDiv.className = "launcher-card-meta";
    cmdDiv.textContent = def.command;
    info.appendChild(cmdDiv);
  }
  if (def.cwd) {
    var cwdDiv = document.createElement("div");
    cwdDiv.className = "launcher-card-meta";
    cwdDiv.textContent = def.cwd;
    info.appendChild(cwdDiv);
  }
  header.appendChild(info);

  var badge = document.createElement("span");
  badge.className =
    "launcher-badge " +
    (hasRunning ? "running" : hasCrashed ? "crashed" : "stopped");
  badge.textContent = hasRunning
    ? "running"
    : hasCrashed
      ? "crashed"
      : "stopped";
  header.appendChild(badge);

  el.appendChild(header);

  // Running instances
  for (var ri = 0; ri < running.length; ri++) {
    var p = running[ri];
    var inst = document.createElement("div");
    inst.className = "launcher-instance";
    var iName = document.createElement("span");
    iName.className = "launcher-instance-name";
    iName.textContent = p.name;
    inst.appendChild(iName);
    if (p.pid) {
      var pidSpan = document.createElement("span");
      pidSpan.className = "launcher-instance-pid";
      pidSpan.textContent = "PID " + p.pid;
      inst.appendChild(pidSpan);
    }
    if (p.started_at) {
      var upSpan = document.createElement("span");
      upSpan.className = "launcher-instance-pid";
      upSpan.textContent = formatUptime(p.started_at);
      inst.appendChild(upSpan);
    }
    el.appendChild(inst);

    var acts = document.createElement("div");
    acts.className = "launcher-actions";
    var stopBtn = document.createElement("button");
    stopBtn.className = "launcher-btn danger";
    stopBtn.textContent = "Stop";
    stopBtn.setAttribute("data-name", p.name);
    stopBtn.onclick = function () {
      window.stopAgent(this.getAttribute("data-name"));
    };
    acts.appendChild(stopBtn);
    var logsBtn = document.createElement("button");
    logsBtn.className = "launcher-btn";
    logsBtn.textContent = "Logs";
    logsBtn.setAttribute("data-name", p.name);
    logsBtn.onclick = function () {
      window.toggleAgentLogs(this.getAttribute("data-name"));
    };
    acts.appendChild(logsBtn);
    el.appendChild(acts);

    if (launcherLogsOpen[p.name]) {
      var logsDiv = document.createElement("div");
      logsDiv.className = "launcher-logs";
      logsDiv.id = "logs-" + p.name;
      var lines = launcherLogs[p.name];
      if (lines && lines.length > 0) {
        logsDiv.textContent = lines.join("\n");
      } else {
        var emptySpan = document.createElement("span");
        emptySpan.className = "launcher-logs-empty";
        emptySpan.textContent = "No logs yet...";
        logsDiv.appendChild(emptySpan);
      }
      el.appendChild(logsDiv);
    }
  }

  // Crashed instances
  for (var ci = 0; ci < crashed.length; ci++) {
    var cp = crashed[ci];
    var cInst = document.createElement("div");
    cInst.className = "launcher-instance";
    var cName = document.createElement("span");
    cName.className = "launcher-instance-name";
    cName.textContent = cp.name + " (crashed)";
    cInst.appendChild(cName);
    el.appendChild(cInst);

    var cActs = document.createElement("div");
    cActs.className = "launcher-actions";
    var relaunchBtn = document.createElement("button");
    relaunchBtn.className = "launcher-btn primary";
    relaunchBtn.textContent = "Relaunch";
    relaunchBtn.setAttribute("data-base", base);
    relaunchBtn.onclick = function () {
      window.launchAgent(this.getAttribute("data-base"));
    };
    cActs.appendChild(relaunchBtn);
    var cLogsBtn = document.createElement("button");
    cLogsBtn.className = "launcher-btn";
    cLogsBtn.textContent = "Logs";
    cLogsBtn.setAttribute("data-name", cp.name);
    cLogsBtn.onclick = function () {
      window.toggleAgentLogs(this.getAttribute("data-name"));
    };
    cActs.appendChild(cLogsBtn);
    el.appendChild(cActs);

    if (launcherLogsOpen[cp.name]) {
      var cLogsDiv = document.createElement("div");
      cLogsDiv.className = "launcher-logs";
      cLogsDiv.id = "logs-" + cp.name;
      var cLines = launcherLogs[cp.name];
      if (cLines && cLines.length > 0) {
        cLogsDiv.textContent = cLines.join("\n");
      } else {
        var cEmptySpan = document.createElement("span");
        cEmptySpan.className = "launcher-logs-empty";
        cEmptySpan.textContent = "No logs yet...";
        cLogsDiv.appendChild(cEmptySpan);
      }
      el.appendChild(cLogsDiv);
    }
  }

  // Actions row: Launch / Launch Another
  var mainActs = document.createElement("div");
  mainActs.className = "launcher-actions";
  var launchBtn = document.createElement("button");
  launchBtn.className = "launcher-btn primary";
  launchBtn.textContent = hasRunning ? "Launch Another" : "Launch";
  launchBtn.setAttribute("data-base", base);
  launchBtn.onclick = function () {
    window.toggleLaunchConfig(this.getAttribute("data-base"));
  };
  mainActs.appendChild(launchBtn);
  el.appendChild(mainActs);

  // Launch config (if open)
  if (launcherConfigOpen[base]) {
    el.appendChild(buildLaunchConfig(base, def));
  }

  return el;
}

function buildLaunchConfig(base, def) {
  var esc =
    window.escapeHtml ||
    function (s) {
      return String(s);
    };
  var container = document.createElement("div");
  container.className = "launch-config";

  var defaultCwd = def.cwd || "";
  var presets = launcherFlagPresets[base] || [];

  // CWD field
  var cwdField = document.createElement("div");
  cwdField.className = "launch-config-field";
  var cwdLabel = document.createElement("label");
  cwdLabel.textContent = "Working directory";
  cwdField.appendChild(cwdLabel);
  var cwdInput = document.createElement("input");
  cwdInput.type = "text";
  cwdInput.id = "launch-cwd-" + base;
  cwdInput.value = defaultCwd;
  cwdInput.placeholder = "Default cwd";
  cwdField.appendChild(cwdInput);
  container.appendChild(cwdField);

  // Flag presets
  if (presets.length > 0) {
    var flagField = document.createElement("div");
    flagField.className = "launch-config-field";
    var flagLabel = document.createElement("label");
    flagLabel.textContent = "Flags";
    flagField.appendChild(flagLabel);
    var flagsWrap = document.createElement("div");
    flagsWrap.className = "launch-flags";
    flagsWrap.id = "launch-flags-" + base;
    for (var fi = 0; fi < presets.length; fi++) {
      var flagBtn = document.createElement("button");
      flagBtn.className = "launch-flag-toggle";
      flagBtn.setAttribute("data-flag", presets[fi].flag);
      flagBtn.textContent = presets[fi].label;
      flagBtn.onclick = function () {
        this.classList.toggle("active");
      };
      flagsWrap.appendChild(flagBtn);
    }
    flagField.appendChild(flagsWrap);
    container.appendChild(flagField);
  }

  // Extra arguments
  var extraField = document.createElement("div");
  extraField.className = "launch-config-field";
  var extraLabel = document.createElement("label");
  extraLabel.textContent = "Extra arguments";
  extraField.appendChild(extraLabel);
  var extraInput = document.createElement("textarea");
  extraInput.id = "launch-extra-" + base;
  extraInput.rows = 1;
  extraInput.placeholder = "Additional args...";
  extraField.appendChild(extraInput);
  container.appendChild(extraField);

  // Launch button
  var launchBtn = document.createElement("button");
  launchBtn.className = "launcher-btn primary";
  launchBtn.textContent = "Launch " + (def.label || base);
  launchBtn.setAttribute("data-base", base);
  launchBtn.onclick = function () {
    window.launchAgent(this.getAttribute("data-base"));
  };
  container.appendChild(launchBtn);

  return container;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

async function launchAgent(base) {
  var cwdInput = document.getElementById("launch-cwd-" + base);
  var extraInput = document.getElementById("launch-extra-" + base);

  // Gather active flags from the config form for this base
  var flags = [];
  var flagsContainer = document.getElementById("launch-flags-" + base);
  if (flagsContainer) {
    var flagEls = flagsContainer.querySelectorAll(".launch-flag-toggle.active");
    flagEls.forEach(function (el) {
      var flag = el.getAttribute("data-flag");
      if (flag) flags.push(flag);
    });
  }

  var body = {
    flags: flags,
    cwd: cwdInput ? cwdInput.value : undefined,
    extra_args: extraInput ? extraInput.value : undefined,
  };

  try {
    var res = await fetch(
      "/api/agents/" + encodeURIComponent(base) + "/launch",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (!res.ok) {
      var err = await res.json().catch(function () {
        return {};
      });
      console.error("[launcher] launch failed:", err);
    }
    // Close config and refresh
    launcherConfigOpen[base] = false;
    await fetchManagedAgents();
  } catch (e) {
    console.error("[launcher] launchAgent error:", e);
  }
}

async function stopAgent(name) {
  try {
    var res = await fetch("/api/agents/" + encodeURIComponent(name) + "/stop", {
      method: "POST",
    });
    if (!res.ok) {
      var err = await res.json().catch(function () {
        return {};
      });
      console.error("[launcher] stop failed:", err);
    }
    await fetchManagedAgents();
  } catch (e) {
    console.error("[launcher] stopAgent error:", e);
  }
}

function toggleLaunchConfig(base) {
  launcherConfigOpen[base] = !launcherConfigOpen[base];
  renderLauncherPanel();
}

async function toggleAgentLogs(name) {
  launcherLogsOpen[name] = !launcherLogsOpen[name];
  if (launcherLogsOpen[name]) {
    await fetchAgentLogs(name);
  }
  renderLauncherPanel();
}

async function fetchAgentLogs(name) {
  try {
    var res = await fetch("/api/agents/" + encodeURIComponent(name) + "/logs");
    if (!res.ok) return;
    var data = await res.json();
    launcherLogs[name] = data.lines || [];
  } catch (e) {
    console.error("[launcher] fetchAgentLogs error:", e);
  }
}

function renderAgentLogs(name) {
  var el = document.getElementById("logs-" + name);
  if (!el) return;
  var lines = launcherLogs[name] || [];
  if (lines.length > 0) {
    el.textContent = lines.join("\n");
  } else {
    el.textContent = "";
    var emptySpan = document.createElement("span");
    emptySpan.className = "launcher-logs-empty";
    emptySpan.textContent = "No logs yet...";
    el.appendChild(emptySpan);
  }
  // Auto-scroll to bottom
  el.scrollTop = el.scrollHeight;
}

// ---------------------------------------------------------------------------
// Uptime formatting
// ---------------------------------------------------------------------------

function formatUptime(startedAt) {
  var start = new Date(startedAt);
  var now = new Date();
  var diff = Math.floor((now - start) / 1000);
  if (diff < 60) return diff + "s";
  if (diff < 3600) return Math.floor(diff / 60) + "m";
  if (diff < 86400)
    return (
      Math.floor(diff / 3600) + "h " + Math.floor((diff % 3600) / 60) + "m"
    );
  return (
    Math.floor(diff / 86400) + "d " + Math.floor((diff % 86400) / 3600) + "h"
  );
}

// ---------------------------------------------------------------------------
// Add Agent form
// ---------------------------------------------------------------------------

function toggleAddAgentForm() {
  var form = document.getElementById("add-agent-form");
  if (!form) return;
  var isHidden = form.classList.toggle("hidden");
  if (!isHidden) {
    renderAddAgentForm();
  }
}

function renderAddAgentForm() {
  var form = document.getElementById("add-agent-form");
  if (!form) return;
  form.textContent = "";

  // Name field
  var nameField = document.createElement("div");
  nameField.className = "add-agent-field";
  var nameLabel = document.createElement("label");
  nameLabel.textContent = "Agent name";
  nameField.appendChild(nameLabel);
  var nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.id = "new-agent-name";
  nameInput.placeholder = "e.g. claude";
  nameField.appendChild(nameInput);
  form.appendChild(nameField);

  // Command field
  var cmdField = document.createElement("div");
  cmdField.className = "add-agent-field";
  var cmdLabel = document.createElement("label");
  cmdLabel.textContent = "Command";
  cmdField.appendChild(cmdLabel);
  var cmdInput = document.createElement("input");
  cmdInput.type = "text";
  cmdInput.id = "new-agent-command";
  cmdInput.placeholder = "e.g. claude --dangerously-skip-permissions";
  cmdField.appendChild(cmdInput);
  form.appendChild(cmdField);

  // Label field
  var lblField = document.createElement("div");
  lblField.className = "add-agent-field";
  var lblLabel = document.createElement("label");
  lblLabel.textContent = "Display label";
  lblField.appendChild(lblLabel);
  var lblInput = document.createElement("input");
  lblInput.type = "text";
  lblInput.id = "new-agent-label";
  lblInput.placeholder = "e.g. Claude Code";
  lblField.appendChild(lblInput);
  form.appendChild(lblField);

  // Colour field
  var colField = document.createElement("div");
  colField.className = "add-agent-field";
  var colLabel = document.createElement("label");
  colLabel.textContent = "Colour";
  colField.appendChild(colLabel);
  var swatches = document.createElement("div");
  swatches.className = "colour-swatches";
  for (var si = 0; si < COLOUR_SWATCHES.length; si++) {
    var swatch = document.createElement("div");
    swatch.className =
      "colour-swatch" +
      (COLOUR_SWATCHES[si] === _selectedColour ? " selected" : "");
    swatch.style.background = COLOUR_SWATCHES[si];
    swatch.setAttribute("data-colour", COLOUR_SWATCHES[si]);
    swatch.onclick = function () {
      selectColourSwatch(this.getAttribute("data-colour"), this);
    };
    swatches.appendChild(swatch);
  }
  colField.appendChild(swatches);
  form.appendChild(colField);

  // Actions
  var actions = document.createElement("div");
  actions.className = "add-agent-actions";
  var saveBtn = document.createElement("button");
  saveBtn.className = "launcher-btn primary";
  saveBtn.textContent = "Save";
  saveBtn.onclick = saveNewAgent;
  actions.appendChild(saveBtn);
  var cancelBtn = document.createElement("button");
  cancelBtn.className = "launcher-btn";
  cancelBtn.textContent = "Cancel";
  cancelBtn.onclick = toggleAddAgentForm;
  actions.appendChild(cancelBtn);
  form.appendChild(actions);
}

function selectColourSwatch(colour, el) {
  _selectedColour = colour;
  // Update selection state
  var swatches = el.parentElement.querySelectorAll(".colour-swatch");
  swatches.forEach(function (s) {
    s.classList.remove("selected");
  });
  el.classList.add("selected");
}

async function saveNewAgent() {
  var nameEl = document.getElementById("new-agent-name");
  var cmdEl = document.getElementById("new-agent-command");
  var labelEl = document.getElementById("new-agent-label");

  var name = nameEl ? nameEl.value.trim() : "";
  var command = cmdEl ? cmdEl.value.trim() : "";
  var label = labelEl ? labelEl.value.trim() : "";

  if (!name || !command) {
    console.warn("[launcher] Name and command are required");
    return;
  }

  try {
    var res = await fetch("/api/agent-definitions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name,
        command: command,
        label: label || name,
        color: _selectedColour,
      }),
    });
    if (!res.ok) {
      var err = await res.json().catch(function () {
        return {};
      });
      console.error("[launcher] saveNewAgent failed:", err);
      return;
    }
    toggleAddAgentForm();
    await fetchDefinitions();
  } catch (e) {
    console.error("[launcher] saveNewAgent error:", e);
  }
}

// ---------------------------------------------------------------------------
// Session restore
// ---------------------------------------------------------------------------

async function fetchRestoreAgents() {
  try {
    var res = await fetch("/api/agents/restore");
    if (!res.ok) return;
    var data = await res.json();
    if (data.agents && data.agents.length > 0) {
      showRestoreBanner(data.agents);
    }
  } catch (e) {
    console.error("[launcher] fetchRestoreAgents error:", e);
  }
}

function showRestoreBanner(agents) {
  // Remove existing banner if any
  var existing = document.getElementById("restore-banner");
  if (existing) existing.remove();

  var timeline = document.getElementById("timeline");
  if (!timeline) return;

  var banner = document.createElement("div");
  banner.id = "restore-banner";

  var title = document.createElement("div");
  title.className = "restore-title";
  title.textContent = "Restore previous agents?";
  banner.appendChild(title);

  var list = document.createElement("div");
  list.className = "restore-list";
  for (var ai = 0; ai < agents.length; ai++) {
    var agent = agents[ai];
    var item = document.createElement("label");
    item.className = "restore-item";
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.value = agent.name || agent.base || "";
    cb.setAttribute("data-base", agent.base || "");
    item.appendChild(cb);
    var span = document.createElement("span");
    span.textContent = agent.label || agent.name || agent.base;
    item.appendChild(span);
    list.appendChild(item);
  }
  banner.appendChild(list);

  var actions = document.createElement("div");
  actions.className = "restore-actions";
  var restoreBtn = document.createElement("button");
  restoreBtn.className = "launcher-btn primary";
  restoreBtn.textContent = "Restore";
  restoreBtn.onclick = relaunchSelected;
  actions.appendChild(restoreBtn);
  var dismissBtn = document.createElement("button");
  dismissBtn.className = "launcher-btn";
  dismissBtn.textContent = "Dismiss";
  dismissBtn.onclick = dismissRestore;
  actions.appendChild(dismissBtn);
  banner.appendChild(actions);

  timeline.insertBefore(banner, timeline.firstChild);
}

async function relaunchSelected() {
  var banner = document.getElementById("restore-banner");
  if (!banner) return;

  var checkboxes = banner.querySelectorAll('input[type="checkbox"]:checked');
  var launches = [];
  checkboxes.forEach(function (cb) {
    var base = cb.getAttribute("data-base") || cb.value;
    if (base) launches.push(base);
  });

  for (var li = 0; li < launches.length; li++) {
    try {
      await fetch(
        "/api/agents/" + encodeURIComponent(launches[li]) + "/launch",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        },
      );
    } catch (e) {
      console.error("[launcher] relaunch error for", launches[li], e);
    }
  }

  banner.remove();
  fetchManagedAgents();
}

async function dismissRestore() {
  var banner = document.getElementById("restore-banner");
  if (banner) banner.remove();

  try {
    await fetch("/api/agents/restore/dismiss", { method: "POST" });
  } catch (e) {
    console.error("[launcher] dismissRestore error:", e);
  }
}

// ---------------------------------------------------------------------------
// Hub event subscriptions
// ---------------------------------------------------------------------------

Hub.on("agent_processes", function (event) {
  launcherProcesses = event.processes || event.data || [];
  renderLauncherPanel();
});

Hub.on("agent_log", function (event) {
  var name = event.name;
  if (!name) return;
  if (!launcherLogs[name]) launcherLogs[name] = [];
  if (event.line) {
    launcherLogs[name].push(event.line);
    // Cap at 500 lines
    if (launcherLogs[name].length > 500) {
      launcherLogs[name] = launcherLogs[name].slice(-500);
    }
  }
  if (event.lines) {
    launcherLogs[name] = launcherLogs[name].concat(event.lines);
    if (launcherLogs[name].length > 500) {
      launcherLogs[name] = launcherLogs[name].slice(-500);
    }
  }
  if (launcherLogsOpen[name]) {
    renderAgentLogs(name);
  }
});

Hub.on("session_restore", function (event) {
  var agents = event.agents || (event.data && event.data.agents) || [];
  if (agents.length > 0) {
    showRestoreBanner(agents);
  }
});

// ---------------------------------------------------------------------------
// Init: fetch restore agents on load
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", function () {
  fetchRestoreAgents();
});

// ---------------------------------------------------------------------------
// Window exports (for onclick handlers)
// ---------------------------------------------------------------------------

window.toggleLauncherPanel = toggleLauncherPanel;
window.toggleAddAgentForm = toggleAddAgentForm;
window.launchAgent = launchAgent;
window.stopAgent = stopAgent;
window.toggleLaunchConfig = toggleLaunchConfig;
window.toggleAgentLogs = toggleAgentLogs;
window.selectColourSwatch = selectColourSwatch;
window.saveNewAgent = saveNewAgent;
window.relaunchSelected = relaunchSelected;
window.dismissRestore = dismissRestore;
