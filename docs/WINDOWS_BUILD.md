# Windows desktop preview

The `desktop/` project packages the hosted Auto Poster AI app in a sandboxed Electron window. Web and desktop use the same service and customer account data. This is client source and unsigned testing-package tooling, not a Microsoft Store listing or a production-signed application.

## Configure and build

Use Node 24 on Windows. The service must already be deployed at its actual public HTTPS origin; no Render hostname is assumed. Do not put backend credentials, API keys, Page tokens or signing keys in this project.

```powershell
cd desktop
npm ci
npm test
$env:AUTOPOST_APP_ORIGIN = "https://YOUR-REAL-APP-HOST"
npm run dist
```

Replace the entire example origin with the actual working deployment address. Missing origins, plaintext URLs, local/IP hosts, embedded credentials, paths, queries and fragments are rejected. `npm run configure` creates the ignored `app-config.json` containing only the public origin. The NSIS x64 installer is written to `desktop/dist/`. `npm start` starts an unpackaged preview with the same required origin. Run these commands on a supported Windows runner to verify the actual installer.

## Connections and security

The renderer has no Node access, no preload bridge, sandboxing, context isolation and HTTPS validation. Only the configured app origin stays in the app window; external HTTPS links ask for confirmation and open in the system browser. Other URL protocols and pop-up windows are blocked. Camera, microphone, geolocation and other native permissions are denied; add specific user-consented capabilities only when the product implements them.

Facebook/Canva connection starts and subscription checkout are redirected to an explicit system-browser handoff **before** the embedded page creates an OAuth nonce or checkout session. Sign in to the same Auto Poster AI account in that browser, finish the connection there, then return to desktop and reload. Accounts are stored on the shared backend, but browser cookies are deliberately not copied into the desktop app. This is a documented handoff, not an implemented native OAuth callback or a guarantee that providers approve embedded login. Provider setup and end-to-end live authorization still require verification.

The client packages no backend source or credentials. JavaScript shipped in any desktop or web client can still be inspected; sensitive logic and authorization belong on the server. ASAR is a packaging format, not source-code protection. This preview requires an internet connection and does not cache private customer work for offline use.

The lockfile pins Electron and the build tools. A `minimatch` override applies only to the affected version range and moves build-tool dependencies to patched 11.0.1 for GHSA-g3hv-75wg-m249 while preserving older compatible dependencies elsewhere. Re-run `npm audit` when updating the build toolchain; do not use `npm audit fix --force` to silently downgrade it.

## Production distribution still required

Before public release, verify the real HTTPS deployment, live customer accounts and connections, privacy/support policies, installer behavior and accessibility on a Windows device. Configure the operator's code-signing certificate and publish through the intended distribution channel. This project does not upload to any store.

Automatic updates are deliberately not active: no signed update feed or publishing credentials were supplied. Hosted web changes appear on reload, but the Electron runtime requires a new signed installer. Configure and test a trusted signed update service before promising app update prompts. Windows reputation warnings are expected for unsigned test installers; do not tell customers to disable security protections.
