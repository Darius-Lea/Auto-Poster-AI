import { createHash } from 'node:crypto';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { validateOrigin } from './validate-origin.mjs';

// This runs after packaging, against only the dedicated CI output directory.
const [target, directory] = process.argv.slice(2);
if (!['android-debug', 'ios-simulator', 'windows-unsigned'].includes(target) || !directory) {
  throw new Error('Usage: node release/package-report.mjs <target> <artifact-directory>');
}
const origin = validateOrigin(process.env.AUTOPOST_APP_ORIGIN);
const root = path.resolve(directory);
await mkdir(root, { recursive: true });
const artifacts = [];
for (const entry of await readdir(root, { withFileTypes: true })) {
  if (!entry.isFile() || entry.name === 'build-info.json') continue;
  const bytes = await readFile(path.join(root, entry.name));
  artifacts.push({ filename: entry.name, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') });
}
if (!artifacts.length) throw new Error('No built application artifact was found; refusing an empty release report.');
await writeFile(path.join(root, 'build-info.json'), `${JSON.stringify({
  target,
  distribution: 'testing-only; not a store submission',
  app_origin: origin,
  commit: process.env.GITHUB_SHA || null,
  workflow_run: process.env.GITHUB_RUN_ID || null,
  created_at: new Date().toISOString(),
  artifacts: artifacts.sort((a, b) => a.filename.localeCompare(b.filename)),
}, null, 2)}\n`);
