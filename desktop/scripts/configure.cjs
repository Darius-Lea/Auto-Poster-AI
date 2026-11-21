'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { validateOrigin } = require('../navigation.cjs');

try {
  const origin = validateOrigin(process.env.AUTOPOST_APP_ORIGIN);
  fs.writeFileSync(path.join(__dirname, '..', 'app-config.json'), `${JSON.stringify({ origin }, null, 2)}\n`, { mode: 0o644 });
  process.stdout.write(`Desktop client configured for ${origin}\n`);
} catch (error) {
  process.stderr.write(`Desktop configuration failed: ${error.message}\n`);
  process.exitCode = 1;
}
