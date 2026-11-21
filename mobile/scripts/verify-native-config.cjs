'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {validateOrigin} = require('./configure.cjs');
try {
  const origin = validateOrigin(process.env.AUTOPOST_APP_ORIGIN);
  const root = path.resolve(__dirname, '..');
  const platform = process.argv[2];
  const asset = {android:'android/app/src/main/assets/capacitor.config.json', ios:'ios/App/App/capacitor.config.json'}[platform];
  if (!asset) throw new Error('Specify android or ios.');
  for (const relative of ['capacitor.config.json', asset]) {
    const config = JSON.parse(fs.readFileSync(path.join(root, relative), 'utf8'));
    if (config.server?.url !== origin || config.server?.cleartext !== false || config.appId !== 'io.autoposterai.app') {
      throw new Error('Mobile configuration is missing or stale. Run npm run prepare:' + platform + ' with the actual AUTOPOST_APP_ORIGIN.');
    }
  }
} catch (error) {
  console.error('Native build blocked: ' + error.message);
  process.exitCode = 1;
}
