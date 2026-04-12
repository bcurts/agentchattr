const { URL } = require('url');

const PROTOCOL = 'agentchattr';

let hasSingleInstanceLock = null;
let secondInstanceListener = null;

function resolveMainWindow(mainWindow) {
  return typeof mainWindow === 'function' ? mainWindow() : mainWindow;
}

function extractDeepLinkUrl(argv) {
  if (!Array.isArray(argv)) {
    return null;
  }

  return (
    argv.find(
      (value) => typeof value === 'string' && value.startsWith(`${PROTOCOL}://`)
    ) ?? null
  );
}

function parseDeepLink(urlString) {
  if (typeof urlString !== 'string' || !urlString.startsWith(`${PROTOCOL}://`)) {
    return null;
  }

  let parsedUrl;

  try {
    parsedUrl = new URL(urlString);
  } catch (error) {
    console.warn(`Failed to parse deep link URL: ${urlString}`, error);
    return null;
  }

  const type = parsedUrl.hostname;
  const value = decodeURIComponent(parsedUrl.pathname.replace(/^\/+/, ''));

  if (!value) {
    return null;
  }

  if (type === 'channel' || type === 'agent') {
    return { type, value };
  }

  if (type === 'port') {
    const port = Number(value);

    if (Number.isInteger(port) && port > 0) {
      return { type, value: port };
    }
  }

  return null;
}

function showAndFocusWindow(mainWindow) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }

  if (typeof mainWindow.isMinimized === 'function' && mainWindow.isMinimized()) {
    mainWindow.restore();
  }

  mainWindow.show();
  mainWindow.focus();
}

function dispatchDeepLink(mainWindow, payload) {
  if (!payload) {
    return;
  }

  const window = resolveMainWindow(mainWindow);

  if (!window || window.isDestroyed()) {
    return;
  }

  const { webContents } = window;

  if (
    !webContents ||
    (typeof webContents.isDestroyed === 'function' && webContents.isDestroyed())
  ) {
    return;
  }

  webContents.send('deep-link', payload);
  showAndFocusWindow(window);
}

function ensureSingleInstanceLock(app) {
  if (hasSingleInstanceLock !== null) {
    return hasSingleInstanceLock;
  }

  hasSingleInstanceLock = app.requestSingleInstanceLock();

  if (!hasSingleInstanceLock) {
    app.quit();
  }

  return hasSingleInstanceLock;
}

function setupDeepLinks(app, mainWindow) {
  if (!ensureSingleInstanceLock(app)) {
    return false;
  }

  app.setAsDefaultProtocolClient(PROTOCOL);

  if (secondInstanceListener) {
    app.removeListener('second-instance', secondInstanceListener);
  }

  secondInstanceListener = (_event, argv) => {
    const urlString = extractDeepLinkUrl(argv);

    if (!urlString) {
      return;
    }

    const payload = parseDeepLink(urlString);

    if (!payload) {
      return;
    }

    dispatchDeepLink(mainWindow, payload);
  };

  app.on('second-instance', secondInstanceListener);

  return true;
}

module.exports = {
  setupDeepLinks,
};
