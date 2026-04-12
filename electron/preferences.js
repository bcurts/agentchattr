const { ipcMain } = require("electron");
const ElectronStore = require("electron-store");

const DEFAULT_SHORTCUT = "CommandOrControl+Shift+A";

const schema = {
  closeBehaviour: {
    type: "string",
    enum: ["ask", "tray", "quit"],
    default: "ask",
  },
  windowBounds: {
    type: "object",
    properties: {
      width: {
        type: "number",
      },
      height: {
        type: "number",
      },
    },
    default: {
      width: 1200,
      height: 800,
    },
  },
  globalShortcut: {
    type: "string",
    default: DEFAULT_SHORTCUT,
  },
  portScanInterval: {
    type: "number",
    default: 5000,
  },
  poppedOutWindows: {
    type: "array",
    default: [],
  },
};

function createPreferences() {
  const store = new ElectronStore({ schema });

  ipcMain.removeHandler("get-preference");
  ipcMain.removeHandler("set-preference");

  ipcMain.handle("get-preference", (_event, key) => {
    return store.get(key);
  });

  ipcMain.handle("set-preference", (_event, key, value) => {
    store.set(key, value);
    return value;
  });

  return store;
}

module.exports = {
  createPreferences,
};
