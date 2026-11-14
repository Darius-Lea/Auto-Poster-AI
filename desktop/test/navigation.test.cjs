'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { validateOrigin, sameOrigin, browserFlow, externalHttps } = require('../navigation.cjs');

const origin = 'https://marketing.example.com';

test('public HTTPS origin is normalized without guessing a host', () => {
  assert.equal(validateOrigin(`${origin}/`), origin);
  assert.equal(validateOrigin('https://marketing.example.com:443/'), origin);
});

test('missing, local, plaintext and non-origin release addresses fail closed', () => {
  for (const value of ['', undefined, 'http://marketing.example.com', 'https://user:pass@marketing.example.com', `${origin}/private`, `${origin}/?token=x`, `${origin}/#token`, 'https://localhost', 'https://app.local', 'https://127.0.0.1', 'https://[::1]', `${origin}:8443`]) {
    assert.throws(() => validateOrigin(value), undefined, String(value));
  }
});

test('navigation accepts only exact service origin', () => {
  assert.equal(sameOrigin(`${origin}/studio?draft_id=2`, origin), true);
  for (const value of ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,x', 'https://marketing.example.com.evil.example/', 'https://evil.example/?next='+origin, 'https://user@marketing.example.com/', 'http://marketing.example.com/']) assert.equal(sameOrigin(value, origin), false, value);
});

test('official connection and subscription starts use browser destinations, never replay POST data', () => {
  assert.deepEqual(browserFlow(`${origin}/facebook/connect?x=y`, origin), { label: 'Connect Facebook', destination: `${origin}/pages` });
  assert.equal(browserFlow(`${origin}/connections/canva/connect`, origin).destination, `${origin}/connections`);
  assert.equal(browserFlow(`${origin}/facebook/%63onnect`, origin).destination, `${origin}/pages`);
  assert.equal(browserFlow(`${origin}//connections//canva/connect/`, origin).destination, `${origin}/connections`);
  assert.equal(browserFlow(`${origin}/billing/checkout`, origin).destination, `${origin}/control`);
  assert.equal(browserFlow(`${origin}/facebook/callback`, origin), null);
  assert.equal(browserFlow('https://evil.example/facebook/connect', origin), null);
});

test('external handler excludes operating-system and script protocols', () => {
  assert.equal(externalHttps('https://www.canva.com/design/123'), true);
  for (const value of ['mailto:user@example.com', 'ms-settings:privacy', 'file:///c:/x', 'javascript:alert(1)', 'https://u:p@example.com']) assert.equal(externalHttps(value), false, value);
});

test('packaging config embeds only client files and never publishes implicitly', () => {
  const pkg = require('../package.json');
  assert.equal(pkg.build.asar, true);
  assert.equal(pkg.build.publish, null);
  assert.match(pkg.scripts.dist, /--publish never/);
  assert.deepEqual(pkg.build.files, ['main.cjs', 'navigation.cjs', 'app-config.json', 'assets/icon.ico', 'package.json']);
  assert.ok(!pkg.build.files.some(file => file.includes('..') || file.includes('.env')));
});

test('configuration failure returns nonzero without producing a guessed origin', () => {
  const run = spawnSync(process.execPath, [path.join(__dirname, '../scripts/configure.cjs')], { env: { ...process.env, AUTOPOST_APP_ORIGIN: '' }, encoding: 'utf8' });
  assert.equal(run.status, 1);
  assert.match(run.stderr, /real public HTTPS/);
});
