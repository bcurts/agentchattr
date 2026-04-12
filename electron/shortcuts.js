const { globalShortcut } = require('electron');

const DEFAULT_SHORTCUT = 'CommandOrControl+Shift+A';

let registeredShortcut = null;

function getShortcut(preferences) {
  const configuredShortcut =
    preferences && typeof preferences.get === 'function'
      ? preferences.get('globalShortcut')
      : null;

  if (typeof configuredShortcut === 'string' && configuredShortcut.trim()) {
    return configuredShortcut.trim();
  }

  return DEFAULT_SHORTCUT;
}

function toggleMainWindow(mainWindow) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }

  if (typeof mainWindow.isVisible === 'function' && mainWindow.isVisible()) {
    mainWindow.hide();
    return;
  }

  if (typeof mainWindow.isMinimized === 'function' && mainWindow.isMinimized()) {
    mainWindow.restore();
  }

  mainWindow.show();
  mainWindow.focus();
}

function registerShortcuts(mainWindow, preferences) {
  const accelerator = getShortcut(preferences);

  if (registeredShortcut) {
    globalShortcut.unregister(registeredShortcut);
    registeredShortcut = null;
  }

  try {
    const registered = globalShortcut.register(accelerator, () => {
      toggleMainWindow(mainWindow);
    });

    if (!registered) {
      console.warn(`Failed to register global shortcut: ${accelerator}`);
      return;
    }

    registeredShortcut = accelerator;
  } catch (error) {
    console.warn(`Failed to register global shortcut: ${accelerator}`, error);
  }
}

function unregisterAll() {
  registeredShortcut = null;
  globalShortcut.unregisterAll();
}

module.exports = {
  registerShortcuts,
  unregisterAll,
};
