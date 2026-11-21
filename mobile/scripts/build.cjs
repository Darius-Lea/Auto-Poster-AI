'use strict';
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {validateOrigin} = require('./configure.cjs');
try { validateOrigin(process.env.AUTOPOST_APP_ORIGIN); } catch (error) { console.error(error.message); process.exit(1); }
const root = path.resolve(__dirname, '..');
let command, args, cwd;
if (process.argv[2] === 'android') {
  if (!process.env.ANDROID_HOME && !process.env.ANDROID_SDK_ROOT) { console.error('Install Android SDK and set ANDROID_HOME before building.'); process.exit(1); }
  cwd = path.join(root, 'android'); command = process.platform === 'win32' ? 'gradlew.bat' : './gradlew'; args = ['assembleDebug'];
} else if (process.argv[2] === 'ios') {
  if (process.platform !== 'darwin') { console.error('iOS compilation requires macOS and Xcode.'); process.exit(1); }
  cwd = root; command = 'xcodebuild'; args = ['-project', 'ios/App/App.xcodeproj', '-scheme', 'App', '-sdk', 'iphonesimulator', '-configuration', 'Debug', '-derivedDataPath', 'build/ios', 'CODE_SIGNING_ALLOWED=NO', 'build'];
} else { console.error('Choose android or ios.'); process.exit(1); }
const result = spawnSync(command, args, {cwd, stdio: 'inherit'});
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
