'use strict';
const { app, BrowserWindow, dialog, Menu, session, shell } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { validateOrigin, sameOrigin, browserFlow, externalHttps } = require('./navigation.cjs');

let origin;
let mainWindow;
let promptOpen = false;

async function openBrowser(url, label = 'Open this link in your browser?') {
  if (!externalHttps(url) || promptOpen) return;
  promptOpen = true;
  try {
    const choice = await dialog.showMessageBox(mainWindow, {
      type: 'question', title: 'Auto Poster AI', message: label,
      detail: new URL(url).origin, buttons: ['Open browser', 'Cancel'], defaultId: 1, cancelId: 1,
    });
    if (choice.response === 0) await shell.openExternal(url);
  } catch {
    await dialog.showMessageBox(mainWindow, { type: 'info', message: 'The browser could not be opened.', detail: 'Open your normal browser and visit the app to continue.' });
  } finally { promptOpen = false; }
}

function blockBrowserFlow(url) {
  const flow = browserFlow(url, origin);
  if (!flow) return false;
  void openBrowser(flow.destination, `${flow.label} securely in your browser. Sign in to the same Auto Poster AI account there, then return and reload this app.`);
  return true;
}

function navigate(event, url) {
  if (blockBrowserFlow(url) || !sameOrigin(url, origin)) {
    event.preventDefault();
    if (!sameOrigin(url, origin)) void openBrowser(url);
  }
}

function createWindow() {
  const isolated = session.fromPartition('persist:autoposter');
  isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  isolated.setPermissionCheckHandler(() => false);
  // Cancel connect/checkout before a POST or redirect can create state in the
  // embedded cookie jar. Official provider login runs in the system browser.
  isolated.webRequest.onBeforeRequest({ urls: [`${origin}/*`] }, (details, callback) => {
    if (details.webContentsId === mainWindow?.webContents.id && blockBrowserFlow(details.url)) callback({ cancel: true });
    else callback({});
  });
  mainWindow = new BrowserWindow({
    title: 'Auto Poster AI', width: 1280, height: 900, minWidth: 390, minHeight: 640,
    show: false, backgroundColor: '#f5f8fc', icon: path.join(__dirname, 'assets', 'icon.ico'),
    webPreferences: {
      session: isolated, sandbox: true, contextIsolation: true, nodeIntegration: false,
      webSecurity: true, allowRunningInsecureContent: false, webviewTag: false, devTools: !app.isPackaged,
    },
  });
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.webContents.on('will-navigate', navigate);
  mainWindow.webContents.on('will-redirect', navigate);
  mainWindow.webContents.on('will-attach-webview', event => event.preventDefault());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (sameOrigin(url, origin) && !blockBrowserFlow(url)) void mainWindow.loadURL(url);
    else if (!sameOrigin(url, origin)) void openBrowser(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('did-fail-load', (_event, code, _description, _url, isMainFrame) => {
    if (isMainFrame && code !== -3) void dialog.showMessageBox(mainWindow, {
      type: 'info', message: 'The app could not connect.', detail: 'Check your internet connection, then choose View → Reload. Saved schedules run on the service while it is available.',
    });
  });
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: 'App', submenu: [
      { label: 'Open account in browser', click: () => void openBrowser(`${origin}/account/settings`) },
      { label: 'Connect accounts in browser', click: () => void openBrowser(`${origin}/connections`) },
      { type: 'separator' }, { role: 'quit' },
    ] },
    { label: 'Edit', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
    { label: 'View', submenu: [{ role: 'reload' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'togglefullscreen' }] },
    { label: 'Help', submenu: [
      { label: 'Help', click: () => void openBrowser(`${origin}/help`) },
      { label: 'About updates', click: () => void dialog.showMessageBox(mainWindow, { message: 'Desktop preview', detail: 'Automatic updates are not configured. This testing installer is unsigned. Use only installers from your trusted operator; a signed update service is required before public distribution.' }) },
    ] },
  ]));
  void mainWindow.loadURL(`${origin}/launch?source=desktop`);
}

app.whenReady().then(() => {
  try {
    origin = validateOrigin(JSON.parse(fs.readFileSync(path.join(__dirname, 'app-config.json'), 'utf8')).origin);
  } catch {
    dialog.showErrorBox('Auto Poster AI is not configured', 'Build this client with the real public AUTOPOST_APP_ORIGIN. No connection was attempted.');
    app.quit();
    return;
  }
  createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
