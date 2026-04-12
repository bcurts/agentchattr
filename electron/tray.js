const fs = require('fs');
const path = require('path');
const { Tray, Menu, nativeImage, app } = require('electron');

const ICON_PATH = path.join(__dirname, 'assets', 'icon.png');
const FALLBACK_COLOUR = '#da7756';

let trayInstance = null;
let trackedWindow = null;

function createSvgDataUrl(svgMarkup) {
  return `data:image/svg+xml;base64,${Buffer.from(svgMarkup).toString('base64')}`;
}

function createFallbackSquareIcon() {
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
      <rect width="16" height="16" fill="${FALLBACK_COLOUR}" />
    </svg>
  `;

  return nativeImage.createFromDataURL(createSvgDataUrl(svg));
}

function loadTrayIcon() {
  if (fs.existsSync(ICON_PATH)) {
    const icon = nativeImage.createFromPath(ICON_PATH);

    if (!icon.isEmpty()) {
      return icon;
    }
  }

  return createFallbackSquareIcon();
}

function createBadgeImage(count) {
  const displayCount = count > 99 ? '99+' : String(count);
  const fontSize = displayCount.length > 2 ? 11 : 14;
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
      <circle cx="16" cy="16" r="15" fill="${FALLBACK_COLOUR}" />
      <text
        x="16"
        y="16"
        font-family="Segoe UI, sans-serif"
        font-size="${fontSize}"
        font-weight="700"
        dominant-baseline="central"
        text-anchor="middle"
        fill="#ffffff"
      >${displayCount}</text>
    </svg>
  `;

  return nativeImage.createFromDataURL(createSvgDataUrl(svg));
}

function toggleWindowVisibility(mainWindow) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }

  if (mainWindow.isVisible()) {
    mainWindow.hide();
    return;
  }

  mainWindow.show();
  mainWindow.focus();
}

function createTray(mainWindow) {
  trackedWindow = mainWindow ?? null;

  if (trayInstance) {
    trayInstance.destroy();
  }

  trayInstance = new Tray(loadTrayIcon());
  trayInstance.setToolTip('agentchattr');
  trayInstance.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: 'Show/Hide',
        click: () => toggleWindowVisibility(trackedWindow),
      },
      { type: 'separator' },
      {
        label: 'Quit',
        click: () => app.quit(),
      },
    ])
  );

  trayInstance.on('double-click', () => {
    if (!trackedWindow || trackedWindow.isDestroyed()) {
      return;
    }

    trackedWindow.show();
    trackedWindow.focus();
  });

  return trayInstance;
}

function setBadge(count) {
  if (!trackedWindow || trackedWindow.isDestroyed()) {
    return;
  }

  if (count > 0) {
    trackedWindow.setOverlayIcon(
      createBadgeImage(count),
      `${count} unread notification${count === 1 ? '' : 's'}`
    );
    return;
  }

  trackedWindow.setOverlayIcon(null, '');
}

module.exports = {
  createTray,
  setBadge,
};
