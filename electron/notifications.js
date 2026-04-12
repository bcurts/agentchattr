const fs = require('fs');
const path = require('path');
const { Notification, ipcMain, nativeImage } = require('electron');

const ICON_PATH = path.join(__dirname, 'assets', 'icon.png');
const FALLBACK_COLOUR = '#da7756';

let unreadCount = 0;
let registeredWindow = null;
let notificationListener = null;
let focusListener = null;

function createSvgDataUrl(svgMarkup) {
  return `data:image/svg+xml;base64,${Buffer.from(svgMarkup).toString('base64')}`;
}

function createFallbackIcon() {
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
      <rect width="32" height="32" rx="6" ry="6" fill="${FALLBACK_COLOUR}" />
    </svg>
  `;

  return nativeImage.createFromDataURL(createSvgDataUrl(svg));
}

function buildNotificationOptions(title, body) {
  const options = {
    title,
    body,
  };

  if (fs.existsSync(ICON_PATH)) {
    options.icon = ICON_PATH;
  } else {
    options.icon = createFallbackIcon();
  }

  return options;
}

function updateBadge(trayModule, count) {
  if (trayModule && typeof trayModule.setBadge === 'function') {
    trayModule.setBadge(count);
  }
}

function resetUnreadCount(trayModule) {
  unreadCount = 0;
  updateBadge(trayModule, 0);
}

function setupNotifications(mainWindow, trayModule) {
  if (registeredWindow && focusListener) {
    registeredWindow.removeListener('focus', focusListener);
  }

  registeredWindow = mainWindow ?? null;
  focusListener = () => {
    resetUnreadCount(trayModule);
  };

  if (registeredWindow && !registeredWindow.isDestroyed()) {
    registeredWindow.on('focus', focusListener);
  }

  if (notificationListener) {
    ipcMain.removeListener('send-notification', notificationListener);
  }

  notificationListener = (_event, payload = {}) => {
    const {
      title = 'agentchattr',
      body = '',
      channel = null,
    } = payload && typeof payload === 'object' ? payload : {};

    unreadCount += 1;
    updateBadge(trayModule, unreadCount);

    const notification = new Notification(buildNotificationOptions(title, body));

    notification.on('click', () => {
      if (!registeredWindow || registeredWindow.isDestroyed()) {
        return;
      }

      registeredWindow.show();
      registeredWindow.focus();

      if (
        registeredWindow.webContents &&
        typeof registeredWindow.webContents.isDestroyed === 'function' &&
        !registeredWindow.webContents.isDestroyed()
      ) {
        registeredWindow.webContents.send('focus-channel', channel);
      }
    });

    notification.show();
  };

  ipcMain.on('send-notification', notificationListener);
}

module.exports = {
  setupNotifications,
};
