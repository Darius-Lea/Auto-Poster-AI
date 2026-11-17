(() => {
  'use strict';
  const form = document.getElementById('account-form');
  const button = document.getElementById('account-submit');
  const status = document.getElementById('account-status');
  const password = document.getElementById('password');
  const toggle = document.getElementById('show-password');
  if (!form || !button || !status) return;

  const originalLabel = button.textContent;
  let submitting = false;
  let slowTimer;

  function clearSlowTimer() {
    if (slowTimer !== undefined) window.clearTimeout(slowTimer);
    slowTimer = undefined;
  }

  function reset() {
    clearSlowTimer();
    submitting = false;
    button.disabled = false;
    button.textContent = originalLabel;
    form.removeAttribute('aria-busy');
    status.textContent = '';
  }

  if (toggle && password) {
    toggle.addEventListener('click', () => {
      const visible = password.type === 'password';
      password.type = visible ? 'text' : 'password';
      toggle.textContent = visible ? 'Hide password' : 'Show password';
      toggle.setAttribute('aria-pressed', String(visible));
    });
  }

  form.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    if (submitting) {
      event.preventDefault();
      return;
    }
    // Native validation normally runs before submit. Keep restored or scripted
    // invalid forms usable as well; no credential field is disabled or stored.
    if (!form.checkValidity()) return;
    submitting = true;
    button.disabled = true;
    form.setAttribute('aria-busy', 'true');
    const message = form.dataset.accountMode === 'register' ?
      'Creating your account…' : 'Signing you in…';
    button.textContent = message;
    status.textContent = message;
    slowTimer = window.setTimeout(() => {
      if (submitting) {
        status.textContent = 'The service is taking longer than usual. Your request is still in progress. Keep this page open and avoid submitting again.';
      }
    }, 8000);
    // Continue the ordinary browser POST and redirect. Do not fetch, abort or
    // automatically retry a registration that may already have succeeded.
  });

  window.addEventListener('pageshow', reset);
  window.addEventListener('pagehide', clearSlowTimer);
})();
