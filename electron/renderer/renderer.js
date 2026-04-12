"use strict";

const state = {
  activeTab: "chat",
  portRows: [],
  notice: null,
  pendingChannel: null,
  webviewReady: false,
};

const elements = {
  tabButtons: Array.from(document.querySelectorAll(".tab-button")),
  popOutButtons: Array.from(document.querySelectorAll(".pop-out-button")),
  chatWebview: document.getElementById("chat-webview"),
  portsContainer: document.getElementById("ports-container"),
};

const RESERVED_DEEP_LINK_TARGETS = new Set(["chat", "ports", "channel"]);

function fileUrlToPath(fileUrl) {
  const pathname = decodeURIComponent(new URL(fileUrl).pathname);
  if (/^\/[A-Za-z]:/.test(pathname)) {
    return pathname.slice(1).replace(/\//g, "\\");
  }

  return pathname;
}

function configureWebview() {
  const preloadPath = fileUrlToPath(
    new URL("../preload-webview.js", window.location.href).toString(),
  );
  const currentPreload = elements.chatWebview.getAttribute("preload");

  if (currentPreload === preloadPath) {
    return;
  }

  elements.chatWebview.setAttribute("preload", preloadPath);
  elements.chatWebview.setAttribute(
    "src",
    elements.chatWebview.getAttribute("src"),
  );
}

function activateTab(tabName) {
  state.activeTab = tabName;

  for (const button of elements.tabButtons) {
    const isActive = button.dataset.tab === tabName;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-selected", String(isActive));
  }

  elements.chatWebview.hidden = tabName !== "chat";
  elements.portsContainer.hidden = tabName !== "ports";
}

function readField(row, keys, fallback = "\u2014") {
  for (const key of keys) {
    const value = row?.[key];

    if (value !== undefined && value !== null && value !== "") {
      return value;
    }
  }

  return fallback;
}

function readPid(row) {
  const value = readField(row, ["pid", "processId"], null);
  return value === null ? null : String(value);
}

function normalisePortRows(payload) {
  if (Array.isArray(payload)) {
    return payload;
  }

  if (Array.isArray(payload?.ports)) {
    return payload.ports;
  }

  if (Array.isArray(payload?.rows)) {
    return payload.rows;
  }

  if (Array.isArray(payload?.items)) {
    return payload.items;
  }

  if (Array.isArray(payload?.data)) {
    return payload.data;
  }

  return [];
}

function setNotice(message, tone = "info") {
  state.notice = message ? { message, tone } : null;
}

function buildCell(text, className = "") {
  const cell = document.createElement("td");
  cell.textContent = text;

  if (className) {
    cell.className = className;
  }

  return cell;
}

async function handleKillProcess(pid) {
  if (!pid || !window.electronAPI?.killProcess) {
    return;
  }

  const result = await window.electronAPI.killProcess(pid);

  if (result?.success) {
    setNotice(`Termination signal sent to PID ${pid}.`);
  } else {
    setNotice(result?.error ?? `Unable to terminate PID ${pid}.`, "error");
  }

  renderPorts();
}

function renderPorts() {
  const container = elements.portsContainer;
  container.innerHTML = "";

  const shell = document.createElement("div");
  shell.className = "ports-shell";

  const header = document.createElement("div");
  header.className = "ports-header";

  const title = document.createElement("h1");
  title.className = "ports-title";
  title.textContent = "Ports";

  const meta = document.createElement("div");
  meta.className = "ports-meta";
  meta.textContent = `${state.portRows.length} entr${state.portRows.length === 1 ? "y" : "ies"}`;

  header.append(title, meta);
  shell.appendChild(header);

  if (state.notice) {
    const notice = document.createElement("div");
    notice.className =
      `ports-notice ${state.notice.tone === "error" ? "error" : ""}`.trim();
    notice.textContent = state.notice.message;
    shell.appendChild(notice);
  }

  if (state.portRows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "ports-empty";
    empty.innerHTML =
      "<strong>Waiting for port data</strong><span>Port information will appear here when the Electron main process emits it.</span>";
    shell.appendChild(empty);
    container.appendChild(shell);
    return;
  }

  const wrapper = document.createElement("div");
  wrapper.className = "ports-table-wrap";

  const table = document.createElement("table");
  table.className = "ports-table";

  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");

  for (const label of [
    "Port",
    "Address",
    "PID",
    "Process",
    "Agent",
    "Actions",
  ]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    headerRow.appendChild(th);
  }

  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");

  for (const row of state.portRows) {
    const tr = document.createElement("tr");
    const pid = readPid(row);

    tr.appendChild(
      buildCell(
        String(readField(row, ["port", "localPort", "listenPort"])),
        "mono",
      ),
    );
    tr.appendChild(
      buildCell(String(readField(row, ["address", "host", "bind", "ip"]))),
    );
    tr.appendChild(buildCell(pid ?? "\u2014", "mono"));
    tr.appendChild(
      buildCell(
        String(
          readField(row, ["process", "processName", "command", "name", "exe"]),
        ),
        "muted",
      ),
    );
    tr.appendChild(
      buildCell(
        String(readField(row, ["agent", "agentName", "owner", "channel"])),
      ),
    );

    const actionCell = document.createElement("td");
    const killButton = document.createElement("button");
    killButton.type = "button";
    killButton.className = "kill-button";
    killButton.textContent = "Kill";
    killButton.disabled = !pid;
    killButton.addEventListener("click", async () => {
      killButton.disabled = true;
      await handleKillProcess(pid);
      killButton.disabled = !pid;
    });
    actionCell.appendChild(killButton);
    tr.appendChild(actionCell);

    tbody.appendChild(tr);
  }

  table.appendChild(tbody);
  wrapper.appendChild(table);
  shell.appendChild(wrapper);
  container.appendChild(shell);
}

function parseDeepLink(rawUrl) {
  try {
    const url = new URL(rawUrl);

    if (url.protocol !== "agentchattr:") {
      return null;
    }

    const host = url.hostname.toLowerCase();
    const segments = url.pathname
      .split("/")
      .filter(Boolean)
      .map((segment) => decodeURIComponent(segment));

    const tabParam = url.searchParams.get("tab")?.toLowerCase();
    let targetTab = tabParam === "ports" ? "ports" : "chat";
    let targetChannel = url.searchParams.get("channel");

    if (host === "ports" || segments[0] === "ports") {
      targetTab = "ports";
    } else if (host === "chat") {
      targetTab = "chat";
      targetChannel ??= segments[0] ?? null;
    } else if (host === "channel") {
      targetTab = "chat";
      targetChannel ??= segments[0] ?? null;
    } else if (host && !RESERVED_DEEP_LINK_TARGETS.has(host)) {
      targetTab = "chat";
      targetChannel ??= host;
    } else if (
      segments[0] &&
      !RESERVED_DEEP_LINK_TARGETS.has(segments[0].toLowerCase())
    ) {
      targetChannel ??= segments[0];
    }

    return {
      tab: targetTab,
      channel: targetChannel ? decodeURIComponent(targetChannel) : null,
    };
  } catch (error) {
    console.error("Unable to parse deep link:", error);
    return null;
  }
}

async function synchronisePendingChannel(attempt = 0) {
  if (!state.pendingChannel || !state.webviewReady) {
    return;
  }

  const channel = state.pendingChannel;
  const script = `
    (() => {
      try {
        const channel = ${JSON.stringify(channel)};
        localStorage.setItem('agentchattr-channel', channel);
        if (typeof window.switchChannel === 'function') {
          window.switchChannel(channel);
          return 'switched';
        }
        return 'stored';
      } catch (error) {
        return 'error:' + error.message;
      }
    })();
  `;

  try {
    const result = await elements.chatWebview.executeJavaScript(script, true);

    if (result === "switched" || attempt >= 10) {
      state.pendingChannel = null;
      return;
    }

    setTimeout(() => {
      void synchronisePendingChannel(attempt + 1);
    }, 250);
  } catch (error) {
    console.error("Unable to switch channel in chat webview:", error);
  }
}

function handleDeepLink(input) {
  // C-4 fix: accept both raw URL strings and pre-parsed { type, value } objects
  // from deep-links.js
  let target;
  if (typeof input === "object" && input !== null && input.type) {
    // Pre-parsed object from deep-links.js: { type: 'channel'|'agent'|'port', value }
    if (input.type === "port") {
      target = { tab: "ports", channel: null };
    } else if (input.type === "channel") {
      target = { tab: "chat", channel: String(input.value) };
    } else if (input.type === "agent") {
      target = { tab: "chat", channel: null };
    } else {
      target = { tab: "chat", channel: null };
    }
  } else {
    // Raw URL string
    target = parseDeepLink(input);
  }

  if (!target) {
    return;
  }

  activateTab(target.tab);

  if (target.channel) {
    state.pendingChannel = target.channel;
    void synchronisePendingChannel();
  }
}

function bindEvents() {
  for (const button of elements.tabButtons) {
    button.addEventListener("click", () => {
      activateTab(button.dataset.tab);
    });
  }

  for (const button of elements.popOutButtons) {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      window.electronAPI?.requestPopOut?.(button.dataset.popout);
    });
  }

  elements.chatWebview.addEventListener("dom-ready", () => {
    state.webviewReady = true;
    void synchronisePendingChannel();
  });

  // H-2 fix: bridge webview ipc-message to main process for notifications
  elements.chatWebview.addEventListener("ipc-message", (event) => {
    if (event.channel === "send-notification") {
      window.electronAPI?.sendNotification?.(event.args[0]);
    }
  });

  // H-3 fix: listen for pop-out-requested and other notification events
  window.electronAPI?.onNotification?.((data) => {
    if (data?.type === "pop-out-requested") {
      window.electronAPI?.requestPopOut?.(data.view);
    }
  });

  window.electronAPI?.onPortData?.((data) => {
    state.portRows = normalisePortRows(data);
    renderPorts();
  });

  window.electronAPI?.onDeepLink?.((url) => {
    handleDeepLink(url);
  });
}

function init() {
  configureWebview();
  bindEvents();
  activateTab("chat");
  renderPorts();
}

init();
