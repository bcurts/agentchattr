const { dialog, ipcMain } = require('electron');

function resolveMainWindow(mainWindow) {
  return typeof mainWindow === 'function' ? mainWindow() : mainWindow;
}

function setupDialogs(mainWindow) {
  ipcMain.removeHandler('show-open-dialog');

  ipcMain.handle('show-open-dialog', (_event, options = {}) => {
    return dialog.showOpenDialog(resolveMainWindow(mainWindow), options);
  });
}

module.exports = {
  setupDialogs,
};
