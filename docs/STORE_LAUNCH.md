# Auto Poster AI — Apple and Google launch preparation

The requested next distribution targets are **Apple App Store and Google Play**. The current deliverable is a mobile-friendly web application with install icons and a web manifest. It is not a signed iOS or Android application and has not been submitted to either store.

## Product work before public store distribution

Complete these requirements in the actual product before representing the application as ready for public customers:

- Independent customer accounts, recovery and server-enforced workspace isolation. The present owner password and `owner` workspace must not be shared with unrelated subscribers.
- A complete onboarding handoff from the public draft into the customer's own saved account and connected destination. Browser-local setup and private owner import are not customer registration.
- Durable production data, a reliable scheduler/queue, recovery from interruption, clear delivery status, cancellation and account disconnection.
- Photo/video support and destination-specific previews if advertised. Publishing beyond Facebook requires each destination's working connector and required permissions; a planned option is not a functioning integration.
- Verified production subscription entitlements, renewals, cancellation, restore/recovery, webhook handling and clear activation behavior. Saving a post must never silently purchase a subscription or activate publishing.
- Real-device testing of keyboard/form behavior, accessibility, camera/photo permissions when added, connection/reconnection, scheduling, failed delivery and account deletion.

## Prepare the store applications

Choose and implement a maintained iOS/Android application architecture, package identifiers, version/build numbering, signing and release builds. The existing PWA assets can seed the visual identity, but manifest icons do not create store binaries. Any web-based shell still needs useful functionality, reliable navigation, appropriate permissions and compliance with the store's current review requirements.

Apple preparation includes an owner-controlled Apple Developer account, the correct legal identity, App Store Connect app record, signing/provisioning and a real iOS build. Google preparation includes an owner-controlled Play Console account, developer verification, a package/app record, app signing and an Android App Bundle. Account fees, legal agreements, signing credentials and submissions require their own owner actions; none have been performed here. Enter credentials through approved secret/account settings, never chat or repository files.

## Billing and customer disclosures

Review Apple's and Google's current rules for digital subscriptions in the selected countries and distribution model. A Stripe checkout implemented on the website is not automatically an approved in-app purchase flow. Implement the required platform billing, entitlement verification and restore/manage/cancel behavior for the final store application and make pricing clear before purchase.

Prepare a real privacy policy, support contact, terms, account deletion route, Apple privacy disclosures and Google Data Safety answers that match the data the released application actually collects. Explain social-account permissions and use official authorization. Verify required Meta/platform approvals for customers outside the owner's app roles before advertising public connections.

## Test and submit in stages

1. Verify the intended web deployment and customer workflows first.
2. Build signed applications and use TestFlight and Google Play testing tracks with intended test users.
3. Test real iPhone/Android hardware, purchases/restoration in the relevant test environments, authorization callbacks, broken-network recovery, notifications if implemented and accessibility.
4. Prepare screenshots from the shipping app, the Signal Fold icon, accurate descriptions, age/content classifications and reviewer instructions. Do not market planned connectors, analytics or AI video clipping as current capabilities.
5. Submit only after the owner reviews the concrete builds, listing and required disclosures. Store approval cannot be guaranteed.

At this release-candidate stage there are no store accounts, signed mobile packages, test-track invitations, purchase submissions or public store listings created by this work. See [RELEASE_2026-11-12.md](RELEASE_2026-11-12.md) for the current web release and verification boundaries.
