(() => {
  'use strict';
  const form = document.getElementById('clip-brand-form');
  if (!form) return;
  const status = document.getElementById('clip-brand-status');
  const save = document.getElementById('clip-brand-save');
  const reload = document.getElementById('clip-brand-reload');
  const image = document.getElementById('clip-brand-image');
  const preview = document.getElementById('clip-brand-preview');
  let loaded = false;
  let busy = false;
  let dirty = false;
  const controls = form.elements;
  const fields = () => [controls.name, controls.accent, controls.logo, controls.remove_logo];
  function apply(profile) {
    controls.name.value = profile.name;
    controls.accent.value = profile.accent;
    controls.revision.value = String(profile.revision);
    controls.logo.value = '';
    controls.remove_logo.checked = false;
    preview.hidden = !profile.has_logo;
    if (profile.has_logo) image.src = profile.logo_url;
    else image.removeAttribute('src');
    const clip = document.getElementById('clip-upload');
    if (clip) {
      const name = clip.querySelector('[name="business_name"]');
      const accent = clip.querySelector('[name="accent"]');
      if (name) name.value = profile.name;
      if (accent) accent.value = profile.accent;
    }
    loaded = true;
    dirty = false;
  }
  async function request(options) {
    const response = await fetch('/clips/brand', {credentials: 'same-origin', ...options});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 409) loaded = false;
      throw new Error(data.error || (response.status === 401 ? 'Sign in again to manage your business look.' : 'We could not load your brand. Please try again.'));
    }
    return data;
  }
  function lock(value) {
    busy = value;
    fields().forEach(field => { field.disabled = value || !loaded; });
    save.disabled = value || !loaded;
    reload.disabled = value;
  }
  async function load() {
    if (busy) return;
    if (dirty && !window.confirm('Reload your saved settings and discard these unsaved brand changes?')) return;
    lock(true);
    try {
      const data = await request();
      apply(data.profile);
      status.textContent = 'Your saved look is ready. Make a change, then save before creating a new clip.';
    } catch (error) { status.textContent = error.message; }
    finally { lock(false); }
  }
  fields().forEach(field => field.addEventListener('input', () => {dirty = true;}));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!loaded || busy) return;
    const file = controls.logo.files[0];
    if (file && file.size > 4 * 1024 * 1024) {
      status.textContent = 'Choose a PNG, JPG or WebP logo no larger than 4 MB.';
      return;
    }
    if (file && controls.remove_logo.checked) {
      status.textContent = 'Choose a new logo or remove the saved logo, rather than both.';
      return;
    }
    // Capture before disabling controls: disabled fields are omitted by FormData.
    const body = new FormData(form);
    lock(true);
    status.textContent = 'Saving your business look…';
    try {
      const data = await request({method: 'POST', body});
      apply(data.profile);
      status.textContent = data.message;
    } catch (error) { status.textContent = error.message; }
    finally { lock(false); }
  });
  reload.addEventListener('click', load);
  lock(false);
  load();
})();
