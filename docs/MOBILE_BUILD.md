# Android and iPhone source preparation

`mobile/` contains native Android and iOS projects generated with official Capacitor **8.6.2**, pinned in `package.json` and `package-lock.json`. The source uses the existing Auto Poster AI artwork. This is a client build foundation, **not a completed native product or store release**. No signed app, store account, purchase integration, submission or public listing was created.

The client loads the deployed HTTPS web service. It contains no Flask backend, Facebook/Canva/AI/Stripe service credentials, or customer data. The web address is visible to the device; packaging does not hide the hosting provider or browser-delivered code. Updating the website updates remote content when the client loads it, while native changes still require a new installed build. A working offline native publishing experience is not implemented.

## Required deployment setting

The actual production origin has not been provided or verified. There is deliberately no default Render address. Set **`AUTOPOST_APP_ORIGIN`** to your real deployed HTTPS origin in your local shell or CI input before configuring or building. Use an origin only, without credentials, a path, query, fragment or custom port. Local, IP and reserved example hosts are rejected. This setting is an address, not a service secret. Never put backend environment keys or tokens in `mobile/`.

`npm run configure`, both `prepare:*` scripts, both `build:*` scripts and the native prebuild checks fail when the address is absent. Native checks also reject missing or stale synced configuration. Syntax validation cannot prove domain ownership, service availability or production readiness; verify the intended deployment independently before distributing a client.

The source identifier `io.autoposterai.app` is provisional and is **not registered or verified as available**. Before creating store records, confirm the final owner-controlled identifier and consistently update `scripts/configure.cjs`, `scripts/verify-native-config.cjs`, Android namespace/application ID/Java package and the Xcode bundle identifier. Both projects start at version `0.1.0`, build `1`; increment store build numbers for every uploaded build.

## Reproducible installation and checks

Use Node **22 or newer** and npm 10 or newer. CI should use `npm ci` to honor the lockfile.

```sh
cd mobile
npm ci
npm test
```

Android and iOS projects were generated from the official templates and are already checked in, with native origin guards and the product artwork added. The initial source generation used an origin-free local setup screen; it did not create a working service client. Generated service configuration and copied web assets are ignored by Git and must be recreated with `prepare:*` after checkout.

Once the real `AUTOPOST_APP_ORIGIN` is set:

```sh
npm run prepare:android
npm run prepare:ios
```

The scripts configure the explicit address and run the pinned Capacitor sync command. No public connection, account login or post is performed by generation or sync.

## Android test build

Install Java **21**, Android SDK platform **36**, the matching Android build tools, and accept the SDK licenses. Set `ANDROID_HOME` for the installed SDK. The generated project targets API 36 with minimum API 24, Android Gradle Plugin 8.13.0 and a Gradle 8.14.3 wrapper. Its distribution SHA-256 was obtained from the official Gradle checksum endpoint and is pinned in `gradle-wrapper.properties`.

```sh
cd mobile
npm run build:android
```

This prepares configuration and runs `./gradlew assembleDebug`. The expected output is `mobile/android/app/build/outputs/apk/debug/app-debug.apk`. A debug APK uses a development signing key; it is **not a Play release or signed production artifact**. Direct Android Studio/Gradle builds run the same origin/configuration guard. `NODE_BINARY` can select the Node executable when the IDE PATH omits it.

A production Android App Bundle still needs the confirmed package ID/version, owner-controlled signing/upload keys stored outside Git, Play app-signing setup, a release signing configuration, and release validation. No signing passwords or keys are generated or committed by this work.

## iOS simulator build

Use a Mac with Xcode compatible with Capacitor 8 and the selected Apple submission SDK; verify the current Capacitor and App Store requirements before release. The generated project uses Swift Package Manager, pins the Capacitor Swift dependency exactly to 8.6.2, and has a minimum iOS deployment target of 15.0. CocoaPods is not required for this source. Initial dependency resolution needs access to the official Capacitor Swift package repository.

```sh
cd mobile
npm run build:ios
```

The script runs `xcodebuild` against `ios/App/App.xcodeproj`, scheme `App`, with the simulator SDK, Debug configuration and `CODE_SIGNING_ALLOWED=NO`. Expected simulator output is `mobile/build/ios/Build/Products/Debug-iphonesimulator/App.app`. The Xcode prebuild phase requires the same origin and synced configuration; set `NODE_BINARY` if necessary. An unsigned simulator app cannot be installed on an ordinary iPhone or submitted to the App Store.

A device build, signed archive, provisioning, export and TestFlight upload require the owner's Apple Developer/App Store Connect accounts, a Mac/Xcode, signing credentials and review of the concrete release. None was performed here.

## Native integration blockers

- **Social authorization:** the website's existing Facebook/Canva OAuth flows are not proof of valid native authorization. Embedded WebView login may be prohibited by the provider. The native source does not implement ASWebAuthenticationSession, Chrome Custom Tabs, universal/app links, callback handling or a secure browser-to-WebView session exchange. Simply opening the provider in an external browser does not transfer its cookies/session back into this client. Implement provider-approved native/system-browser authorization and test account connection, cancellation, expiry and reconnection on real iOS and Android devices before advertising native connections as working.
- **Billing:** StoreKit, Google Play Billing, server receipt/purchase-token validation, restore purchases, entitlement reconciliation, refunds and subscription management are absent. The website's Stripe checkout is not automatically an approved native digital-subscription flow. This remote-web shell does not filter website billing links. Resolve the billing model, applicable storefront rules and native flows before public native distribution.
- **Store experience:** Capacitor's remote `server.url` option is primarily intended for live reload. This source uses it explicitly as a development/evaluation wrapper; it does not establish a production architecture or compliance with Apple's minimum-functionality rules. Review remote-content/bridge security and implement and verify the final native navigation, connectivity/error recovery, accessibility, lifecycle and useful mobile behavior before release. The source installs no additional native plugins and adds no camera, microphone or photo permissions.
- **Release verification:** verify the deployed account setup/recovery/deletion, tenant isolation, provider permissions, schedule execution and paid entitlements with actual authorized test accounts. Test hardware, small screens, keyboards, safe areas, external links and bad connections. Complete privacy/support disclosures, Apple privacy information, Google Data Safety, screenshots and reviewer access against the final app. No public release is established by a green source/configuration test.

## What was verified in this environment

Official npm registry versions were checked, the locked Node dependencies were installed, both Capacitor native source projects were generated, and configuration rejection/safe defaults were tested. There is no known live app origin in this task. The Linux workspace has Java 21 but no configured Android SDK; iOS requires macOS/Xcode. Consequently no Android APK/AAB, iOS simulator app, signed archive, device OAuth flow, purchase or store submission was built or verified here. See [STORE_LAUNCH.md](STORE_LAUNCH.md) for the wider release boundary.
