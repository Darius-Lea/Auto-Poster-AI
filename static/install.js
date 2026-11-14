(() => {
  'use strict';
  const button = document.getElementById('install-app');
  const status = document.getElementById('install-status');
  const connection = document.getElementById('install-connection');
  let prompt = null;
  const installed = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const network = () => {
    connection.textContent = navigator.onLine ?
      'Your browser is online. Opening the app still requires access to its hosted service.' :
      'You are offline. Connect to load your workspace and manage cloud publishing.';
  };
  network();
  window.addEventListener('online', network);
  window.addEventListener('offline', network);
  if (installed()) status.textContent = 'You are already using the installed web app.';
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    prompt = event;
    if (!installed()) {
      button.hidden = false;
      button.disabled = false;
      status.textContent = 'This browser can install Auto Poster AI as a web app.';
    }
  });
  window.addEventListener('appinstalled', () => {
    prompt = null;
    button.hidden = true;
    status.textContent = 'Web app installed. Open Auto Poster AI from your device’s app launcher.';
  });
  button.addEventListener('click', async () => {
    if (!prompt) return;
    const current = prompt;
    prompt = null;
    button.disabled = true;
    try {
      await current.prompt();
      const result = await current.userChoice;
      status.textContent = result.outcome === 'accepted' ?
        'Installation accepted. Follow your browser’s remaining instructions.' :
        'Installation cancelled. You can continue using the website.';
    } catch (_) {
      status.textContent = 'Use your browser’s Install app or Add to Home Screen command.';
    } finally {
      button.hidden = true;
    }
  });
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/service-worker.js', {scope:'/'}).catch(() => {
      status.textContent = 'The install helper could not initialize. You can still open the website and follow the device instructions below.';
    });
  }
})();
