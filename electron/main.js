const { app, BrowserWindow, shell, Menu } = require('electron');
const path = require('path');
const http = require('http');

const SERVER_HOST = '127.0.0.1';
const SERVER_PORT = 8300;
const SERVER_URL = `http://${SERVER_HOST}:${SERVER_PORT}`;

const isInternal = (urlString) => {
  try {
    const u = new URL(urlString);
    return (
      (u.hostname === '127.0.0.1' || u.hostname === 'localhost') &&
      u.port === String(SERVER_PORT)
    );
  } catch {
    return false;
  }
};

const pingServer = () =>
  new Promise((resolve) => {
    const req = http.get(
      { host: SERVER_HOST, port: SERVER_PORT, path: '/', timeout: 1500 },
      (res) => {
        res.resume();
        resolve(true);
      }
    );
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });

const wireExternalLinks = (contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    if (!isInternal(url)) shell.openExternal(url);
    return { action: 'deny' };
  });

  contents.on('will-navigate', (event, url) => {
    if (!isInternal(url)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });
};

const createWindow = async () => {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    title: 'agentchattr',
    icon: path.join(__dirname, '..', 'static', 'logo.png'),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  wireExternalLinks(win.webContents);

  const load = async () => {
    if (await pingServer()) {
      win.loadURL(SERVER_URL);
    } else {
      win.loadFile(path.join(__dirname, 'offline.html'));
    }
  };

  win.webContents.on('did-finish-load', () => {
    win.webContents.executeJavaScript(
      `window.__agentchattrRetry = () => location.hash = '#retry-' + Date.now();`,
      true
    ).catch(() => {});
  });

  win.webContents.on('did-navigate-in-page', (_e, url) => {
    if (url.includes('#retry-')) load();
  });

  await load();
};

app.whenReady().then(() => {
  const template = [
    {
      label: 'File',
      submenu: [
        { label: 'Reload', accelerator: 'CmdOrCtrl+R', role: 'reload' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
        { role: 'toggleDevTools' },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('web-contents-created', (_e, contents) => {
  wireExternalLinks(contents);
});
