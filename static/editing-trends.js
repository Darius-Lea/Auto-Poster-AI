(() => {
  'use strict';
  const root = document.getElementById('editing-inspiration');
  if (!root) return;
  const status = document.getElementById('trend-status');
  const updated = document.getElementById('trend-updated');
  const videos = document.getElementById('trend-videos');
  const refresh = document.getElementById('trend-refresh');
  const region = document.getElementById('trend-region');
  function element(tag, text) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    return node;
  }
  function count(number, label) {
    return Number.isSafeInteger(number) && number >= 0 ? number.toLocaleString() + ' ' + label : null;
  }
  async function load(useSelectedRegion = true) {
    refresh.disabled = true;
    status.textContent = 'Checking popular-video inspiration…';
    try {
      const response = await fetch('/clips/trends' + (useSelectedRegion ? '?region=' + encodeURIComponent(region.value) : ''), {credentials:'same-origin'});
      const data = await response.json();
      if (!response.ok) throw new Error(response.status === 401 ? 'Sign in again to see video inspiration.' : 'Video inspiration could not be loaded. Your clip work is unchanged.');
      if (!useSelectedRegion && /^[A-Z]{2}$/.test(data.region)) {
        if (![...region.options].some(option => option.value === data.region)) {
          const option = element('option', data.region); option.value = data.region; region.append(option);
        }
        region.value = data.region;
      }
      status.textContent = data.message;
      updated.textContent = data.fetched_at ? 'Source: YouTube · Last checked ' + new Date(data.fetched_at * 1000).toLocaleString() + (data.stale ? ' · Older observations' : '') + '. Checks are cached for at least 30 minutes.' : '';
      videos.replaceChildren();
      (data.videos || []).slice(0, 6).forEach(video => {
        // Construct links from validated IDs rather than trusting arbitrary URLs.
        if (!/^[A-Za-z0-9_-]{11}$/.test(video.id)) return;
        const card = element('article');
        const link = element('a', video.title); link.href = 'https://www.youtube.com/watch?v=' + video.id;
        link.target = '_blank'; link.rel = 'noopener noreferrer';
        card.append(element('h4')); card.lastChild.append(link);
        card.append(element('p', video.channel + (video.published_at ? ' · Published ' + new Date(video.published_at).toLocaleDateString() : '')));
        const metrics = [count(video.views,'views'),count(video.likes,'likes'),count(video.comments,'comments')].filter(Boolean);
        card.append(element('p', metrics.length ? metrics.join(' · ') : 'Counts are not available for this video.'));
        videos.append(card);
      });
    } catch (error) {
      status.textContent = error.message || 'Video inspiration could not be loaded. Your clip work is unchanged.';
      videos.replaceChildren(); updated.textContent = '';
    } finally {refresh.disabled = false;}
  }
  refresh.addEventListener('click', () => load());
  region.addEventListener('change', () => load());
  document.querySelectorAll('[data-editing-preset]').forEach(button => button.addEventListener('click', () => {
    const select = document.querySelector('#clip-upload [name="style"]');
    if (!select || !['standard','bar','minimal'].includes(button.dataset.editingPreset)) return;
    select.value = button.dataset.editingPreset;
    select.dispatchEvent(new Event('change', {bubbles:true}));
    document.getElementById('editing-preset-status').textContent = button.textContent + ' selected for your next clip. Review it before sharing.';
  }));
  load(false);
})();
