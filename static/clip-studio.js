(() => {
  'use strict';
  const form = document.getElementById('clip-upload');
  if (!form) return;
  const status = document.getElementById('clip-status');
  const jobs = document.getElementById('clip-jobs');
  const empty = document.getElementById('clip-empty');
  const csrf = form.querySelector('[name="_csrf"]').value;
  const cards = new Map();
  const config=JSON.parse(document.getElementById('clip-config')?.textContent || '{"pages":[]}');
  const canRender=form.dataset.canRender==='true';
  const sourceMode=document.getElementById('clip-source-mode');
  sourceMode?.addEventListener('change',()=>{
    const link=sourceMode.value==='link';
    const file=form.querySelector('[name="video"]'), url=form.querySelector('[name="video_url"]');
    file.disabled=link || !canRender; file.required=!link;
    url.disabled=!link || !canRender; url.required=link;
    for (const [id,show] of [['clip-video-file',!link],['clip-video-link',link]]) {
      const box=document.getElementById(id);box.hidden=!show;box.style.display=show?'':'none';
    }
  });
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
    const names = {uploading: 'Uploading', import_queued:'Waiting to import your video', importing:'Importing your video link', queued: 'Waiting for the renderer', rendering: 'Transcribing and rendering — this may take several minutes', ready: 'Ready for your review', failed: 'Could not finish this clip'};
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
      card.append(publishingForm(job));
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
    if (!['uploading', 'rendering','importing'].includes(job.status)) {
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
      await refreshPublishing();
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
    finally { button.disabled = !canRender; }
  });
  let controlIndex=0;
  function labeled(parent,label,node) {
    const wrapper=element('div'), title=element('label',label);
    node.id='clip-control-'+(++controlIndex);title.htmlFor=node.id;
    wrapper.append(title,node);parent.append(wrapper);return node;
  }
  function publishingForm(job) {
    const box=element('details');box.append(element('summary','Schedule this video to Facebook'));
    if (!config.pages.length) {
      box.append(element('p','Connect and choose the Facebook business Page you manage first.'));
      const link=element('a','Connect a business Page');link.href='/pages';box.append(link);return box;
    }
    const post=element('form');post.dataset.clipPublish=job.id;
    const page=labeled(post,'Facebook business Page',element('select'));page.name='page_id';
    for(const target of config.pages) {const option=element('option',target.name);option.value=target.page_id;page.append(option);}
    const caption=labeled(post,'Video caption',element('textarea'));caption.name='caption';caption.maxLength=10000;caption.required=true;
    caption.value=[job.summary.hook,job.summary.peak_line].filter(Boolean).join('\n\n') || job.options.title || 'My new video';
    const when=labeled(post,'When should it post?',element('select'));when.name='when';
    for(const [value,text] of [['later','Choose a future time'],['now','After I confirm']]) {const option=element('option',text);option.value=value;when.append(option);}
    const date=labeled(post,'Future posting time',element('input'));date.name='local_due';date.type='datetime-local';date.required=true;
    const approval=labeled(post,'I reviewed this clip and approve posting to this Page.',element('input'));approval.type='checkbox';approval.required=true;
    const button=element('button','Schedule video');button.type='submit';post.append(button);
    const message=element('p');message.setAttribute('role','status');post.append(message);
    when.addEventListener('change',()=>{date.required=when.value==='later';date.parentElement.hidden=when.value!=='later';date.parentElement.style.display=date.parentElement.hidden?'none':'';button.textContent=when.value==='later'?'Schedule video':'Post video now';});
    post.addEventListener('submit',async event=>{
      event.preventDefault();button.disabled=true;
      try {
        const due=when.value==='now'?new Date():new Date(date.value);
        if(Number.isNaN(due.getTime()) || (when.value==='later' && due<=new Date())) throw new Error('Choose a future posting time.');
        await action('/clips/jobs/'+job.id+'/publish',{page_id:page.value,caption:caption.value,due_at:due.toISOString(),confirmed:approval.checked?'yes':'no'});
        message.textContent='Your video post is scheduled. Check its confirmed status below.';await refreshPublishing();
      }catch(error){message.textContent=error.message;}finally{button.disabled=false;}
    });
    box.append(element('p','Scheduling requires an active publishing subscription and a connected Page. Basic shares one Facebook post per UTC day across text and video.'),post);return box;
  }
  async function refreshPublishing() {
    const target=document.getElementById('clip-publishing-jobs');if(!target)return;
    const notice=document.getElementById('clip-publishing-status');
    try {
      const data=await request('/clips/publishing');target.replaceChildren();
      notice.textContent=data.jobs.length?'':'Your confirmed video posts will appear here.';
      const names={scheduled:'Scheduled',sending:'Uploading to Facebook',processing:'Facebook is processing your video',published:'Published — confirmed by Facebook',needs_review:'Check your Facebook Page before trying again',quota_held:'Daily publishing allowance reached',canceled:'Canceled'};
      for(const job of data.jobs){
        const card=element('article','','clip-publish-job');card.append(element('h3',names[job.status]||job.status),element('p',job.caption));
        card.append(element('p','Planned time: '+new Date(job.due_at).toLocaleString()));
        if(job.error)card.append(element('p',job.error));
        if(job.permalink_url){const link=element('a','Open the published video');link.href=job.permalink_url;link.target='_blank';link.rel='noopener noreferrer';card.append(link);}
        if(['scheduled','quota_held'].includes(job.status)){
          const cancel=element('button','Cancel video post','secondary');cancel.type='button';cancel.addEventListener('click',async()=>{cancel.disabled=true;try{await action('/clips/publishing/'+job.id+'/cancel');await refreshPublishing();}catch(error){notice.textContent=error.message;cancel.disabled=false;}});card.append(cancel);
        }target.append(card);
      }
    }catch(error){notice.textContent=error.message;}
  }
  refresh();
  window.setInterval(() => { if (!document.hidden) refresh(); }, 4000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
