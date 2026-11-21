import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

test('release report identifies the packaged bytes and commit without copying environment secrets', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'autoposter-artifact-'));
  try {
    const bytes = Buffer.from('synthetic build artifact');
    await writeFile(path.join(directory, 'test.apk'), bytes);
    const result = spawnSync(process.execPath, ['release/package-report.mjs', 'android-debug', directory], {
      encoding: 'utf8',
      env: { ...process.env, AUTOPOST_APP_ORIGIN: 'https://app.autoposterai.com',
        GITHUB_SHA: 'test-commit', GITHUB_RUN_ID: '42', DO_NOT_EXPORT_SECRET: 'not-for-artifacts' },
    });
    assert.equal(result.status, 0, result.stderr);
    const raw = await readFile(path.join(directory, 'build-info.json'), 'utf8');
    const report = JSON.parse(raw);
    assert.equal(report.commit, 'test-commit');
    assert.equal(report.workflow_run, '42');
    assert.deepEqual(report.artifacts, [{ filename: 'test.apk', bytes: bytes.length,
      sha256: createHash('sha256').update(bytes).digest('hex') }]);
    assert.equal(raw.includes('not-for-artifacts'), false);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('empty builds and unsupported targets fail instead of emitting a success report', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'autoposter-empty-build-'));
  try {
    for (const target of ['android-debug', 'public-store-release']) {
      const result = spawnSync(process.execPath, ['release/package-report.mjs', target, directory], {
        encoding: 'utf8', env: { ...process.env, AUTOPOST_APP_ORIGIN: 'https://app.autoposterai.com' },
      });
      assert.notEqual(result.status, 0);
    }
  } finally { await rm(directory, { recursive: true, force: true }); }
});
