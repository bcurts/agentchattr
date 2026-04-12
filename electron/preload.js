const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  onPortData(callback) {
    ipcRenderer.on("port-data", (_event, data) => callback(data));
  },
  onNotification(callback) {
    ipcRenderer.on("notification", (_event, data) => callback(data));
  },
  onDeepLink(callback) {
    ipcRenderer.on("deep-link", (_event, url) => callback(url));
  },
  // H-2 fix: forward webview notifications to main process
  sendNotification(payload) {
    ipcRenderer.send("send-notification", payload);
  },
  requestPopOut(view) {
    ipcRenderer.send("pop-out", view);
  },
  killProcess(pid) {
    return ipcRenderer.invoke("kill-process", pid);
  },
  getPreference(key) {
    return ipcRenderer.invoke("get-preference", key);
  },
  setPreference(key, value) {
    return ipcRenderer.invoke("set-preference", key, value);
  },
  showOpenDialog(options) {
    return ipcRenderer.invoke("show-open-dialog", options);
  },
});
