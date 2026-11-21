(() => {
  'use strict';
  if (document.getElementById('ap-tool-drawer')) return;
  const script = document.currentScript;
  const signedIn = script?.dataset.signedIn === 'true';
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const trigger = make('button', '☰ Tools', 'ap-tools-trigger');
  trigger.type = 'button';
  trigger.setAttribute('aria-controls', 'ap-tool-drawer');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.setAttribute('aria-haspopup', 'dialog');
  const drawer = make('dialog', '', 'ap-tool-drawer');
  drawer.id = 'ap-tool-drawer';
  drawer.setAttribute('aria-labelledby', 'ap-tools-title');
  const header = make('div', '', 'ap-tools-header');
  const heading = make('h2', 'Your tools');
  heading.id = 'ap-tools-title';
  const closeButton = make('button', 'Close ×', 'ap-tools-close');
  closeButton.type = 'button';
  closeButton.setAttribute('aria-label', 'Close tools');
  header.append(heading, closeButton);
  const introduction = make('p', signedIn ? 'Your account works across these tools.' : 'Explore Auto Poster AI. Create one account to keep your work.', 'ap-tools-intro');
  const navigation = make('nav', '', 'ap-tools-links');
  navigation.setAttribute('aria-label', 'Tools and account');
  const links = [
    ['Build my first plan', '/launch'],
    ['Posts', '/'],
    ['Clip Studio', '/clips'],
    ['Creative studio', '/studio'],
    ['Connections', '/connections'],
    ['Reports', '/reports'],
  ];
  if (signedIn) links.push(['Account settings', '/account/settings'], ['Publishing settings', '/control']);
  else links.push(['Sign in', '/account?mode=login'], ['Create account', '/account?mode=register']);
  links.push(['Help', '/help'], ['Install app', '/install']);
  if (signedIn) links.push(['Sign out', '/logout']);
  for (const [label, target] of links) {
    const link = make('a', label);
    link.href = target;
    const url = new URL(target, location.origin);
    if (url.pathname === location.pathname && (!url.search || url.search === location.search)) link.setAttribute('aria-current', 'page');
    navigation.append(link);
  }
  drawer.append(header, introduction, navigation);
  document.body.append(trigger, drawer);
  let previousFocus;
  function close() {
    if (!drawer.open) return;
    if (typeof drawer.close === 'function') drawer.close();
    else drawer.removeAttribute('open');
    trigger.setAttribute('aria-expanded', 'false');
    document.documentElement.classList.remove('ap-tools-open');
    (previousFocus?.isConnected ? previousFocus : trigger).focus();
  }
  trigger.addEventListener('click', () => {
    if (drawer.open) { close(); return; }
    previousFocus = document.activeElement;
    if (typeof drawer.showModal === 'function') drawer.showModal();
    else { drawer.setAttribute('open', ''); drawer.setAttribute('aria-modal', 'true'); }
    trigger.setAttribute('aria-expanded', 'true');
    document.documentElement.classList.add('ap-tools-open');
    closeButton.focus();
  });
  closeButton.addEventListener('click', close);
  drawer.addEventListener('cancel', event => { event.preventDefault(); close(); });
  drawer.addEventListener('click', event => {
    const bounds = drawer.getBoundingClientRect();
    if (event.target === drawer && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) close();
  });
  drawer.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (event.key !== 'Tab') return;
    const focusable = [...drawer.querySelectorAll('button:not([disabled]), a[href]')];
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  navigation.addEventListener('click', event => { if (event.target.closest('a')) close(); });
})();
