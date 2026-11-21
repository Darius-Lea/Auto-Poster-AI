'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {validateOrigin, configForOrigin} = require('../scripts/configure.cjs');
const {spawnSync} = require('node:child_process');
const path = require('node:path');

test('requires the operator HTTPS deployment with no guessed default', () => {
  for (const value of [undefined, '', 'https://', 'http://app.autoposterai.com', 'https://app.autoposterai.com/path', 'https://app.autoposterai.com?token=private', 'https://user:secret@app.autoposterai.com', 'https://app.autoposterai.com#x', 'https://app.autoposterai.com:8080', ' https://app.autoposterai.com', 'https://localhost', 'https://127.0.0.1', 'https://[::1]', 'https://10.1.1.1', 'https://app.local', 'https://example.com', 'https://app.example.com', 'https://app.invalid']) {
    assert.throws(() => validateOrigin(value), undefined, String(value));
  }
  assert.equal(validateOrigin('https://app.autoposterai.com/'), 'https://app.autoposterai.com');
});

test('configuration includes only explicit origin and no injected service credentials', () => {
  const config = configForOrigin('https://app.autoposterai.com');
  assert.equal(config.server.cleartext, false);
  assert.equal(config.android.allowMixedContent, false);
  assert.equal(config.android.webContentsDebuggingEnabled, false);
  assert.deepEqual(config.server.allowNavigation, []);
  assert.deepEqual(config.plugins, {});
  assert.equal(JSON.stringify(config).includes('secret'), false);
});

test('build configuration fails closed even when a previous host might have been configured', () => {
  const result = spawnSync(process.execPath, [path.resolve(__dirname, '../scripts/configure.cjs')], {env: {...process.env, AUTOPOST_APP_ORIGIN:''}, encoding:'utf8'});
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /actual deployed HTTPS origin/);
});
