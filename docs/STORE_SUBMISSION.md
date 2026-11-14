# Auto Poster AI distribution and submission

The **Package test clients** GitHub Actions workflow builds testing artifacts from the checked-out branch. It does not publish a release, deploy Render, sign a public app, or submit an app-store listing. A workflow definition is not evidence that its remote build succeeded.

## Build test clients

1. Deploy the intended backend commit and obtain its actual HTTPS origin from Render or your verified custom domain. Do not guess the hostname. Verify registration, email verification/recovery, saved drafts, and authorized test-Page posting on that deployment first.
2. Open GitHub **Actions → Package test clients → Run workflow** and select the branch containing the client sources. Enter the actual origin, for example the full `https://` address with no route, query, credentials, or fragment. Choose one target or all targets.
3. Read each job result. Download artifacts only from successful jobs and inspect `build-info.json`, which records the commit, deployment origin, target and SHA-256 of the packaged file. Artifacts expire after seven days.
4. Test each downloaded package against a dedicated customer/test Page. Packaging contains the client shell and public deployment origin; server credentials, Python backend, workspace database and `.env` files must remain on the server.

| Artifact | What it can test | What it cannot establish |
| --- | --- | --- |
| Android debug APK | Installation on an Android test device with developer installation allowed; client navigation against the selected deployment | A signed release AAB, Play approval, production billing or working native OAuth |
| iOS simulator ZIP | The `.app` in a compatible Xcode iPhone/iPad simulator on macOS | Installation on a physical iPhone, TestFlight, signing, App Store approval or device capabilities |
| Unsigned Windows installer | Installation on a Windows test machine; desktop navigation | Trusted publisher identity, Microsoft Store certification, signed updates or absence of SmartScreen prompts |

The Android debug build uses the disposable debug signing identity created for that runner. It is not the owner's production signing key. Use uninstall/reinstall for separate debug builds if Android rejects mismatched debug signatures; never use these packages as the public update channel.

## Provider connections before release

Connecting a service to the development assistant does not automatically authorize each customer in Auto Poster AI. The deployed app must expose its supported official authorization flow, bind the returned account to the correct workspace, store credentials on the server, and let that user choose destinations and disconnect.

| Connection | Release evidence needed |
| --- | --- |
| Facebook Pages | Meta app review/business requirements for intended public users, official login, explicit Page choice from a multiple-Page account, reconnect/disconnect, successful scoped test post, failure/duplicate handling, and Basic plan enforcement |
| Canva | Production client registration and approved redirect URI; authorization, refresh, a created design, reopening/editing and owned-design export from a test customer |
| Managed text AI | Operator provisioned supported API credentials; a real request, quota fallback and preferred-provider recovery. A consumer ChatGPT/Claude subscription is not an API integration |
| muse.ai and CapCut | Verify the product's actual supported API and authorization before advertising automated editing or library/analytics access. Opening an external editor and exporting captions are handoffs, not connected video-editing workflows |
| Email and subscriptions | Delivered verification/reset email, one-use/expired-token behavior, Stripe test checkout/webhooks and entitlement changes, cancellation, and customer recovery |

No provider should appear as fully connected merely because its button opens, configuration exists, or mocked tests pass. Document the test customer, provider action and outcome without recording tokens or personal data in the repository.

## Apple App Store

Requires the owner's verified Apple Developer account, final bundle identifier, App Store Connect record, signing/provisioning, physical-device tests and a signed archive uploaded to TestFlight. Supply real support/privacy/terms/account-deletion URLs and matching privacy disclosures, age/content answers, screenshots and reviewer instructions. Review the current digital-subscription/payment rules for the intended markets and implement any required StoreKit purchases, server entitlement verification and restore/manage behavior. Existing website Stripe checkout alone does not establish compliance.

The hosted mobile shell still requires a complete native authorization return flow and real-device validation. A simulator package does not satisfy these requirements. App review may require additional functionality beyond a website wrapper.

## Google Play

Requires the owner's verified Play Console account, final application ID, release signing/Play App Signing, a signed Android App Bundle, required testing tracks and current target-SDK compliance. Prepare Data Safety, privacy/account-deletion/support links, content classification, screenshots, reviewer access and any required Play Billing and purchase restoration/entitlement flows. Verify official social login in the installed application; provider policies can reject embedded-webview authorization.

## Microsoft Store and direct Windows distribution

Choose the intended Microsoft Store packaging/submission path and create a Partner Center app record using the owner's account. The current unsigned NSIS test installer is not an MSIX package or a completed Store submission. For direct public downloads, establish a code-signing identity, signed release artifacts, installation/uninstallation tests and an authenticated update strategy. This sprint does not implement automatic application updates.

## Final release gate

Keep public paid sales disabled until the operator has verified the live checks in [MARKET_READINESS_2026-11-13.md](MARKET_READINESS_2026-11-13.md). Test the complete customer flow on real devices and retain build/run evidence. Store accounts, signing identity, privacy/legal identity, billing decisions, provider review and store approval remain operator/reviewer dependencies; source code and CI cannot create those approvals.
