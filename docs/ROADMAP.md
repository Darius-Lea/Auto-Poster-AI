# A simpler route from one Page to a subscription product

First make one Facebook Page connection reliable, then add more destinations and
the product interface. The current repository contains a batch scraper,
watermarking, captions, publishing backends, and Telegram summaries. It does not
contain an OAuth connection screen, subscriptions, an analytics dashboard, or a
general-purpose content engine. The Fort Wayne Allen County Sheriff roster is
the active source; the owner's TheMostWanted.net source is configured for preview
and remains blocked from publishing until its site-specific parsing is verified.

## First: your Facebook Page

Use the official Page API, a one-record manual test, and a modest schedule. Keep
the Page name visible during connection checks, record confirmed publication
IDs, surface failures, and hold uncertain uploads for review. Telegram remains
a useful preview and notification channel. Add a durable delivery database
before depending on this for long-term unattended operation; GitHub's cache can
disappear.

Keep crime-related posts factual and attributable to their source. Retain source
URLs and capture times, distinguish allegations from convictions, and provide a
way to correct or remove outdated information. A fluent AI caption is not proof
that a claim is true. Do not invent accusations, details, or accompanying photos.

## Next: a hosted application people can simply open

A hosted web app, optionally installable as a PWA, is the simplest first product.
Users open it, create an account, and click **Connect Facebook**. Their scheduled
jobs continue on the server even when their phone or laptop is closed. This
avoids requiring each customer to install Python, keep a desktop process running,
or maintain browser sessions.

Use each platform's official OAuth flow in its own authorization screen. Keep
App Secrets and token exchanges on the backend, protect stored tokens, show
which Page or account is connected, and support reconnect and disconnect. Do
not ask customers for their social account passwords. Logging in to your app
and authorizing a social account are separate steps.

Connecting customers outside your Meta app's own roles can require App Review,
appropriate access levels, and business verification depending on the permissions
and app setup. A working owner-only connection does not establish approval for a
public subscription service. Other social networks have their own account
eligibility, publishing formats, permissions, review processes, limits, and
pricing. Add and test each connector individually.

## Then: a reliable subscription service

Use a durable database for customers, workspaces, account connections, content,
and a delivery row for every destination. Put scheduled work in a persistent job
queue with limits, retries, and a record of each publishing attempt. Keep
uncertain outcomes separate from definite failures and confirmed successes.

Add customer isolation, encrypted credential storage, account deletion and
disconnect controls, billing and subscription entitlements, usage budgets, and
operational alerts. A customer should see a clear preview, destination, schedule,
and delivery status. Keep draft review available and make autonomous publishing
an explicit setting for the connected accounts.

## Finally: posts about more topics, informed by analytics

Replace the crime-specific `Record` as the central content model with a general
draft containing topic, verified source material, image provenance, text,
destination, review state, and schedule. Keep collectors, caption generation,
platform formatting, publishing, and measurement as separate components so a
new topic does not require rewriting delivery code.

Let users describe their topic and permitted sources. Gather relevant material
from supported sources, preserve citations, check image rights and relevance,
and create a draft grounded in that material. Let users review it before enabling
automatic posting for that source and topic.

Collect performance metrics only through the platforms and permissions available
to the connected accounts. Use actual results to compare formats, timing, and
topics over time. Analytics can guide experiments; they do not guarantee reach,
engagement, virality, or knowledge of a platform's ranking algorithm.

Free tiers can support an initial personal workflow, but limits, availability,
and prices can change. A hosted subscription product needs an operating budget
for hosting, storage, AI usage, platform APIs, monitoring, and support.
