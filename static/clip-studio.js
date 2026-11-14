(() => {
  'use strict';
  const form = document.getElementById('clip-upload');
  if (!form) return;
  const status = document.getElementById('clip-status');
  const jobs = document.getElementById('clip-jobs');
  const empty = document.getElementById('clip-empty');
  const csrf = form.querySelector('[name="_csrf"]').value;
  const cards = new Map();
  let polling = false;
  const mode = form.querySelector('[name="mode"]');
  mode.addEventListener('change', () => {
    const label = document.getElementById('clip-start-label');
    label.hidden = mode.value !== 'manual'; label.style.display = label.hidden ? 'none' : '';
  });
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  async function request(url, options = {}) {
    const response = await fetch(url, {credentials: 'same-origin', ...options});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || (response.status === 401 ? 'Sign in again to see your clips.' : 'We could not complete that request. Please try again.'));
    return data;
  }
  async function action(url, values = {}) {
    const data = new FormData();
    data.set('_csrf', csrf);
    Object.entries(values).forEach(([key, value]) => data.set(key, value));
    return request(url, {method: 'POST', body: data});
  }
  function showJob(job) {
    const existing = cards.get(job.id);
    const signature = JSON.stringify([job.status, job.error]);
    if (existing && existing.signature === signature) return;
    const card = element('article', '', 'studio-card');
    card.append(element('h3', job.options.title || 'Your video clip'));
    const names = {uploading: 'Uploading', queued: 'Waiting for the renderer', rendering: 'Transcribing and rendering — this may take several minutes', ready: 'Ready for your review', failed: 'Could not finish this clip'};
    card.append(element('p', names[job.status] || job.status, 'status'));
    if (job.error) card.append(element('p', job.error));
    if (job.status === 'ready') {
      const video = element('video');
      video.controls = true; video.preload = 'metadata';
      video.src = job.downloads.mp4;
      video.style.cssText = 'display:block;max-width:100%;max-height:480px;width:auto;margin:1rem auto';
      card.append(video);
      if (job.summary.hook) card.append(element('p', job.summary.hook));
      const links = element('div', '', 'actions');
      [['mp4', 'Download video'], ['srt', 'Download captions'], ['json', 'Download summary']].forEach(([kind, text]) => {
        const link = element('a', text, 'studio-button secondary');
        link.href = job.downloads[kind] + '?attachment=1'; links.append(link);
      });
      card.append(links);
      const sharing = element('details');
      sharing.append(element('summary', 'How do I share my finished video?'));
      sharing.append(element('p', 'Choose Download video. On a phone, look in your browser’s Downloads or the Downloads folder in Files. Open the social app where you want to share it, switch to the correct business Page or account, choose its video or Reel creation option, and select the downloaded video. Review the caption and audience before you publish.'));
      card.append(sharing);
      const edit = element('details');
      edit.append(element('summary', 'Correct captions and make a revised clip'));
      edit.append(element('p', 'Correct any words below. We keep their timing for you. A revision uses one render attempt and leaves the original clip available.'));
      const text = element('div');
      let cues = [];
      const load = element('button', 'Load captions', 'secondary'); load.type = 'button';
      load.addEventListener('click', async () => {
        load.disabled = true;
        try {
          const response = await fetch(job.downloads.srt, {credentials: 'same-origin'});
          if (!response.ok) throw new Error('Could not load these captions.');
          const source = (await response.text()).replace(/\r/g, '').trim();
          text.replaceChildren();
          cues = source.split(/\n\s*\n/).map(block => {
            const lines = block.split('\n');
            if (lines.length < 3 || !/^\d+$/.test(lines[0]) || !lines[1].includes(' --> ')) throw new Error('These captions could not be opened for editing. You can still download them.');
            const label = element('label', 'Caption ' + lines[0]);
            const input = element('textarea'); input.rows = 2; input.maxLength = 1000;
            input.value = lines.slice(2).join('\n'); label.append(input); text.append(label);
            return {number: lines[0], timing: lines[1], input};
          });
        } catch (error) { status.textContent = error.message; }
        finally { load.disabled = false; }
      });
      const revise = element('button', 'Render corrected captions'); revise.type = 'button';
      revise.addEventListener('click', async () => {
        revise.disabled = true;
        try {
          if (!cues.length) throw new Error('Load your captions first, then correct the words.');
          const captions = cues.map(cue => cue.number + '\n' + cue.timing + '\n' + cue.input.value.trim()).join('\n\n') + '\n';
          await action('/clips/jobs/' + job.id + '/captions', {captions});
          status.textContent = 'Caption revision queued. Your original clip is unchanged.';
          await refresh();
        } catch (error) { status.textContent = error.message; }
        finally { revise.disabled = false; }
      });
      edit.append(load, text, revise); card.append(edit);
    }
    if (!['uploading', 'rendering'].includes(job.status)) {
      const remove = element('button', 'Delete clip and source', 'secondary'); remove.type = 'button';
      remove.addEventListener('click', async () => {
        if (!window.confirm('Delete this clip and its source video? This cannot be undone.')) return;
        remove.disabled = true;
        try {
          await action('/clips/jobs/' + job.id + '/delete');
          card.remove(); cards.delete(job.id);
          status.textContent = 'Clip and source video deleted.';
          await refresh();
        } catch (error) { status.textContent = error.message; remove.disabled = false; }
      });
      card.append(remove);
    }
    if (existing) existing.node.replaceWith(card); else jobs.prepend(card);
    cards.set(job.id, {node: card, signature});
  }
  async function refresh() {
    if (polling) return;
    polling = true;
    try {
      const data = await request('/clips/jobs');
      empty.textContent = data.jobs.length ? '' : 'Your first clip will appear here. Nothing is scheduled or published.';
      [...data.jobs].reverse().forEach(showJob);
      const present = new Set(data.jobs.map(job => job.id));
      cards.forEach((value, key) => { if (!present.has(key)) {value.node.remove(); cards.delete(key);} });
    } catch (error) { empty.textContent = error.message; }
    finally { polling = false; }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = document.getElementById('clip-submit'); button.disabled = true;
    status.textContent = 'Uploading and checking your video. Keep this page open until it is queued.';
    try {
      await request('/clips/jobs', {method: 'POST', body: new FormData(form)});
      status.textContent = 'Your clip is queued. You can leave and return while it renders.';
      form.querySelector('[name="video"]').value = '';
      await refresh();
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  refresh();
  window.setInterval(() => { if (!document.hidden) refresh(); }, 4000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
