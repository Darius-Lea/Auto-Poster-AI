# Beginner experience audit — November 14, 2026

**Verdict:** the source now offers a clearer first step and a usable clip-review interface, but the complete “I saw an ad, created my first clip, and launched a professional social business Page” journey is not finished. A new user still needs an existing video, an app account with confirmed email, an available renderer, and separate social-platform setup. A clip download is not a published video or a completed business.

This is a source-based walkthrough from the perspective of a 70-year-old with little social-media experience. It is not a usability study with an older participant. Reviewed `website/index.html`, the account/confirmation flow, `templates/clip_studio.html`, `static/clip-studio.js`, the hosted clip adapter, help and first-week planning source. Other helpers handle browser/regression checks; this audit does not claim a live Render login, email delivery, completed customer render, trend feed, sales, earnings or virality was verified.

## What the customer can actually do

| Goal | What the current source supports | Boundary the customer needs to understand |
| --- | --- | --- |
| Understand the offer before registering | “Make my first clip,” an alternative first-week plan, and a public finished example | The example is a bundled demonstration, not a promise that the deployed renderer is enabled. |
| Start with only a business idea | Build and edit seven planning drafts, download the plan, and save drafts after account creation | These are starting templates based on the user's answers, not current market research, a manufactured product, an online shop or evidence of demand. |
| Create an Auto Poster account | Name, email and password registration; separate workspace; login; password-recovery and email-confirmation screens | This creates an account in this app, not a Facebook account/Page. Recovery and confirmation require configured, working email delivery. |
| Make a clip | Upload an owned/permitted local video; suggest a moment from speech; produce a short vertical captioned video when the service is enabled and ready | No YouTube/Kick link importing in the hosted screen. No live trend search or future engagement prediction. Source must have video/audio and be 15 seconds–90 minutes; upload limit is up to 200 MB. |
| Give it a business identity | Optional business/creator name, accent color, title, hook, caption style and layout | This is a limited clip treatment. The hosted screen does not upload a customer's logo or create a social Page, cover image, complete profile or brand kit. |
| Check and correct the result | Watch a ready clip; edit caption words while retaining their timing; queue a revision; download video/captions/summary; delete supported jobs | A revision uses another daily attempt. There is no full timeline editor, frame-by-frame crop editor or human caption review. |
| Publish the finished clip | Download the video for manual upload in the chosen platform | No direct clip publishing, “post everywhere” video workflow or automatic CapCut/muse.ai account synchronization is completed by this screen. |
| Start a Facebook business Page | Help links to Facebook's own Page-creation screen and explains returning to choose the exact managed Page | Facebook eligibility and setup happen outside this app. The existing app publishing workflow supports selected Facebook Page text posts when authorized and activated, not the generated video. |

## Completed fixes in the current source

These four problems found during the walkthrough have been corrected in source and have targeted regression coverage. Full-suite and browser results are recorded separately; this is not evidence that production email or rendering has been verified.

- [x] **A first-time clip visitor now starts with registration.** Anonymous browser requests to `/clips` preserve `next=clips` and open registration rather than a returning-user login screen. Registration now has a clip-specific heading and explanation; existing customers can still choose sign-in.
- [x] **Unconfirmed email is explained before upload.** Clip Studio disables the video picker and render button when confirmation is required and shows a direct “Confirm my email” link to account settings. Shared studio Settings navigation now opens customer account settings for customers and owner controls for the owner. Email delivery and the post-confirmation return journey still need real-service validation.
- [x] **Automatic clips fit shorter valid videos.** In automatic mode, the selected duration is capped to the actual source duration. A valid 20-second phone video no longer fails merely because the default clip length is 45 seconds. Explicit manual ranges must still fit inside the source.
- [x] **Pre-queue failures restore the render allowance.** Upload validation/storage failures before queuing, and failures preparing a caption revision, refund their reserved attempt. Actual queued/rendering work still uses the daily allowance. This does not add an on-screen remaining-attempt counter or local reset time.

## Outstanding priorities

**Validate the real first result before inviting paying beginners:** use a fresh customer account to receive confirmation mail, return from the mail app, upload a short phone video, complete transcription/rendering on the intended host and retrieve the actual MP4. Check recovery and a working support channel. Those end-to-end service checks remain unverified here, even though the account and clip source exists.

**Then make creation and sharing easier:** the highest-value remaining interface work is guidance for users with no video, a clear return to Clip Studio after confirmation, and phone-specific download/publishing instructions. The checklist below remains outstanding unless separately verified.

- [x] **A direct Clip Studio return action now appears after email confirmation.** The success screen offers “Continue to Clip Studio” through the safe sign-in destination. Switching between a real mail app and the original browser still needs a live usability check.
- [ ] **Show the remaining render allowance in everyday terms.** Validation failures are now refunded, but the screen still describes a three-attempt “UTC day.” Show attempts remaining and the next reset in the user's local time without changing the server's quota boundary.
- [x] **No-video beginners now have a recording checklist and an idea-only route.** Clip Studio explains recording a 20–60 second introduction, answer or demonstration in a quiet room, saving it to the device and selecting it. A direct first-plan link and public finished example remain available. This is guidance, not an in-app recording tool.
- [ ] **Style choices lack previews.** “Bar,” “Minimal,” “Follow the speaker,” “Two speakers stacked,” and “Opening hook” still require interpretation. Optional disclosure reduces clutter; visual examples and a plain explanation would reduce failed experiments. Avoid making users choose the business name twice under two different labels.
- [ ] **Branding stops short of a professional Page.** Business name/color do not create an identity kit or fill a platform profile. A guided name, description, profile/cover artwork, contact details and Page-creation checklist would help, with explicit external steps where the provider requires them. Customer logo upload remains absent in the hosted clip interface.
- [ ] **The wait has limited reassurance.** Queued/rendering/ready states and resumable job listing exist, but there is no percent complete, completion notification or clear running-job cancellation control. Show elapsed time and a useful next action without inventing a completion estimate.
- [x] **Ready clips now include plain sharing instructions.** A disclosure explains browser/Files Downloads, opening the social app, selecting the correct business Page/account and reviewing a video/Reel before publishing. Native sharing and automated video publishing remain absent; platform/device instructions need real-device verification.
- [ ] **Recovery needs an actual service test.** Password reset/verification screens exist, but unavailable SMTP disables the actions. Test delivered messages, expiry, wrong/expired links, switching to the mail app and returning to the original task. Explain the minimum password length beside the registration field; consider an accessible show-password control.
- [ ] **Reading comfort needs real older-user testing.** Clip Studio has a light work surface, visible labels and large primary controls; the public website/account screens remain dark. Darkness alone does not establish whether customers will leave. Small 11–13 px notes, compact workspace links and dense pricing should be tested at enlarged text/200% zoom and on phones, with keyboard and screen-reader checks. Do not claim this audit measured contrast or accessibility conformance.
- [ ] **Keep marketing aligned with what is available.** The hero describes clips while later steps/pricing focus on text workflows and future plan capacities. Unify the first journey and explain exactly which platform/action is usable today. A current trend feed, hosted YouTube/Kick importing and automatic video posting remain unimplemented; do not present them, proposed pricing or future integrations as activated capabilities.

## Short path to validate before inviting paying beginners

1. From a fresh phone browser, an unregistered person watches the public example and understands the difference between a clip, a plan, an app account and a business Page.
2. They create their own app account, receive and complete email confirmation, and return directly to the clip task without developer instructions.
3. They upload a short phone video, understand a rejected file without losing work or an unexplained allowance, and obtain a real rendered clip on the intended deployment.
4. They recognize their business identity, correct a caption, download the MP4, and follow clear instructions to publish it through the selected platform's supported flow.
5. They can return later, recover their account, locate and delete their files, and reach a working support channel. Website/native-store billing and social authorization must be verified separately for each release.

The immediate useful outcome is **one reviewed, correctly branded downloadable clip plus clear sharing instructions**. “Automatically launch my professional Page and make my side hustle earn money” remains a broader product goal, not a working outcome demonstrated by the current source.
