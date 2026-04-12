const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const net = require("net");
const { pathToFileURL } = require("url");

// --- Constants (CASK: Constants first) ---
const REPO_ROOT = path.resolve(__dirname, "..");
const SERVER_PORT = 8300;
const PYTHON_PATH = path.join(REPO_ROOT, ".venv", "Scripts", "python.exe");
const RENDERER_ENTRY = path.join(__dirname, "renderer", "index.html");
const READY_SIGNAL = "Uvicorn running on";
const FORCE_KILL_DELAY_MS = 5000;

// --- State ---
let mainWindow = null;
let serverProcess = null;
let serverReady = false;
let serverExited = false;
let isQuitting = false;
let forceKillTimer = null;
let stdoutBuffer = "";
let stderrBuffer = "";
let trayInstance = null;
let preferences = null;

// --- Single-instance lock (must happen before app.whenReady) ---
// H-4 fix: acquire lock synchronously at module load
const { setupDeepLinks } = require("./deep-links");
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
}

function findPythonPath() {
  return fs.existsSync(PYTHON_PATH) ? PYTHON_PATH : null;
}

function registerIpcHandlers() {
  // Pop-out handler — echoes request back to renderer
  ipcMain.on("pop-out", (_event, view) => {
    if (!mainWindow || !view) return;
    mainWindow.webContents.send("notification", {
      type: "pop-out-requested",
      view,
    });
  });

  // H-6 fix: validate PID before calling process.kill()
  ipcMain.handle("kill-process", async (_event, pid) => {
    const safePid = Number.isInteger(pid) && pid > 0 ? pid : null;
    if (!safePid) return { success: false, error: "Invalid PID" };
    try {
      process.kill(safePid);
      return { success: true };
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  });

  // H-1 fix: Do NOT register get-preference, set-preference, or show-open-dialog here.
  // Those are handled by preferences.js and dialogs.js respectively.

  // Forwarded notification from renderer (webview ipc-message bridge)
  // H-2 fix: renderer forwards webview 'send-notification' here
  ipcMain.on("send-notification", (_event, payload) => {
    // Handled by notifications.js — this is a fallback in case notifications
    // module hasn't registered yet. The notifications module uses removeListener
    // before re-registering, so this won't conflict.
  });
}

function createWindow() {
  if (mainWindow) {
    mainWindow.show();
    return;
  }

  const bounds = preferences
    ? preferences.get("windowBounds")
    : { width: 1200, height: 800 };

  mainWindow = new BrowserWindow({
    width: bounds.width || 1200,
    height: bounds.height || 800,
    x: bounds.x,
    y: bounds.y,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      webviewTag: true,
    },
  });

  mainWindow.loadURL(pathToFileURL(RENDERER_ENTRY).toString());

  mainWindow.on("close", (event) => {
    if (isQuitting) return;
    event.preventDefault();
    mainWindow.hide();
  });

  // Save window bounds on move/resize
  const saveBounds = () => {
    if (preferences && mainWindow && !mainWindow.isDestroyed()) {
      preferences.set("windowBounds", mainWindow.getBounds());
    }
  };
  mainWindow.on("moved", saveBounds);
  mainWindow.on("resized", saveBounds);

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function waitForServerPort(
  port,
  host = "127.0.0.1",
  retries = 40,
  delayMs = 250,
) {
  return new Promise((resolve, reject) => {
    let attemptsRemaining = retries;
    const tryConnect = () => {
      const socket = net.createConnection({ host, port });
      socket.once("connect", () => {
        socket.end();
        resolve();
      });
      socket.once("error", () => {
        socket.destroy();
        attemptsRemaining -= 1;
        if (attemptsRemaining <= 0) {
          reject(new Error(`Timed out waiting for ${host}:${port}`));
          return;
        }
        setTimeout(tryConnect, delayMs);
      });
    };
    tryConnect();
  });
}

function showStartupError(message) {
  dialog.showErrorBox("agentchattr desktop", message);
}

async function handleReadySignal() {
  if (serverReady) return;
  serverReady = true;
  try {
    await waitForServerPort(SERVER_PORT);
    createWindow();
    wireModules();
  } catch (error) {
    showStartupError(
      `The Python server reported ready, but port ${SERVER_PORT} never opened.\n\n${
        error instanceof Error ? error.message : String(error)
      }`,
    );
    app.quit();
  }
}

function handleServerOutput(chunk, streamName) {
  const text = chunk.toString();
  const currentBuffer = streamName === "stdout" ? stdoutBuffer : stderrBuffer;
  const nextBuffer = currentBuffer + text;
  const lines = nextBuffer.split(/\r?\n/);
  const remainder = lines.pop() ?? "";
  if (streamName === "stdout") {
    stdoutBuffer = remainder;
  } else {
    stderrBuffer = remainder;
  }
  for (const line of lines) {
    if (line.includes(READY_SIGNAL)) {
      void handleReadySignal();
    }
  }
}

function shutdownServer() {
  if (!serverProcess || serverExited) return;
  // H-5 fix: on Windows, use taskkill for graceful shutdown instead of SIGTERM
  // (SIGTERM is silently coerced to SIGKILL on Windows by Node.js)
  if (process.platform === "win32") {
    const { execFile } = require("child_process");
    // taskkill /T kills the process tree (Python + uvicorn workers)
    execFile(
      "taskkill",
      ["/PID", String(serverProcess.pid), "/T", "/F"],
      (err) => {
        if (err) console.warn("taskkill failed:", err);
      },
    );
  } else {
    try {
      serverProcess.kill("SIGTERM");
    } catch (e) {
      console.warn("SIGTERM failed:", e);
    }
    forceKillTimer = setTimeout(() => {
      if (!serverProcess || serverExited) return;
      try {
        serverProcess.kill("SIGKILL");
      } catch (e) {
        console.warn("SIGKILL failed:", e);
      }
    }, FORCE_KILL_DELAY_MS);
  }
}

function startServer(pythonPath) {
  serverExited = false;
  serverProcess = spawn(pythonPath, ["run.py"], {
    cwd: REPO_ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });

  serverProcess.stdout.on("data", (chunk) =>
    handleServerOutput(chunk, "stdout"),
  );
  serverProcess.stderr.on("data", (chunk) =>
    handleServerOutput(chunk, "stderr"),
  );

  serverProcess.once("error", (error) => {
    showStartupError(
      `Failed to launch the Python server with ${pythonPath}.\n\n${
        error instanceof Error ? error.message : String(error)
      }`,
    );
    app.quit();
  });

  serverProcess.once("exit", (code, signal) => {
    serverExited = true;
    if (forceKillTimer) {
      clearTimeout(forceKillTimer);
      forceKillTimer = null;
    }
    if (!serverReady && !isQuitting) {
      showStartupError(
        `The Python server exited before becoming ready.\n\nExit code: ${code ?? "null"}\nSignal: ${signal ?? "null"}`,
      );
      app.quit();
    }
  });
}

// C-3 fix: wire all satellite modules after window creation
function wireModules() {
  if (!mainWindow) return;

  // Preferences (H-1 fix: this replaces the duplicate handlers from registerIpcHandlers)
  const { createPreferences } = require("./preferences");
  preferences = createPreferences();

  // Dialogs
  const { setupDialogs } = require("./dialogs");
  setupDialogs(mainWindow);

  // System tray
  const { createTray } = require("./tray");
  trayInstance = createTray(mainWindow);

  // Notifications (needs tray for badge)
  const { setupNotifications } = require("./notifications");
  setupNotifications(mainWindow, trayInstance);

  // Port scanner
  const { startScanning } = require("./port-scanner");
  const scanInterval = preferences.get("portScanInterval") || 5000;
  startScanning(mainWindow, scanInterval);

  // Global shortcuts
  const { registerShortcuts } = require("./shortcuts");
  registerShortcuts(mainWindow, preferences);

  // Deep links — already acquired single-instance lock above,
  // now wire the second-instance event handler
  // C-4 fix: deep-links.js sends raw URL string, renderer parses it
  // (deep-links.js already sends { type, value } object — renderer needs to handle that)
  setupDeepLinks(app, () => mainWindow);
}

app.whenReady().then(() => {
  registerIpcHandlers();

  const pythonPath = findPythonPath();
  if (!pythonPath) {
    showStartupError(
      `Python was not found at:\n${PYTHON_PATH}\n\nCreate the project virtualenv before starting the desktop wrapper.`,
    );
    app.quit();
    return;
  }

  startServer(pythonPath);
});

app.on("before-quit", () => {
  isQuitting = true;
  const { stopScanning } = require("./port-scanner");
  stopScanning();
  const { unregisterAll } = require("./shortcuts");
  unregisterAll();
  shutdownServer();
});

app.on("window-all-closed", () => {
  // Don't quit — tray keeps the app alive
});
