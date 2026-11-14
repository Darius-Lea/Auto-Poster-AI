import assert from 'node:assert/strict';
import test from 'node:test';
import { validateOrigin } from './validate-origin.mjs';

test('accepts canonical HTTPS deployment origins', () => {
  assert.equal(validateOrigin('https://app.autoposterai.com'), 'https://app.autoposterai.com');
  assert.equal(validateOrigin('https://app.autoposterai.com/'), 'https://app.autoposterai.com');
  assert.equal(validateOrigin('https://app.autoposterai.com:443/'), 'https://app.autoposterai.com');
});

test('rejects missing deployment, local and development addresses', () => {
  for (const value of [undefined, '', 'http://app.example.org', 'https://localhost',
    'https://localhost.local', 'https://127.0.0.1', 'https://10.0.0.1', 'https://[::1]',
    'https://app.test', 'https://app.invalid', 'https://app.internal', 'https://app.example',
    'https://example.com', 'https://app.example.org', 'https://app.example.net',
    'https://app.autoposterai.com:8443', ' https://app.autoposterai.com ']) {
    assert.throws(() => validateOrigin(value), undefined, String(value));
  }
});

test('rejects credentials, callbacks, query injection and noncanonical URL forms', () => {
  for (const value of ['https://user:secret@app.autoposterai.com', 'https://app.autoposterai.com/path',
    'https://app.autoposterai.com?token=secret', 'https://app.autoposterai.com/#x',
    'https://app.autoposterai.com/../', 'https://APP.AUTOPOSTERAI.COM',
    'https://app.autoposterai.com\\@evil.example.org', 'https://app.autoposterai.com\nmalicious']) {
    assert.throws(() => validateOrigin(value), undefined, value);
  }
});
