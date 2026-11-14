# Integration evidence and customer flow — November 14, 2026

The new integration identified for this release is **Clip Studio**, imported from `feature/clip-studio`. A provider appearing on the Connections page means only the capability described there. Configuration, stored authorization, read-only API access and a successful customer workflow are separate evidence.

| Integration | Implemented workflow | What still needs live verification |
|---|---|---|
| Clip Studio | Video engine, transcript corrections, vertical framing, caption rendering, face QA and local output. The authenticated app bridge is `/clips`: upload a local video, wait for its background job, preview/download your own clip and correct its captions. Real local sample rendering and caption replacement passed using an explicitly supplied test transcript. | Fresh Whisper transcription, opt-in hosting flag, durable storage, CPU capacity and a full customer render on the deployed host. Automatic social **video** publishing is not connected by the original outbox sidecar. |
| Facebook Pages | Official customer sign-in, explicit owned Page selection, tenant-scoped encrypted tokens, approved photo/text scheduling and real post metrics. | Approved Meta app/scopes, exact registered callback, customer sign-in and Page selection, a controlled permitted post and actual metrics on the deployed app. The owner's legacy publisher has separate credentials. |
| OpenAI, Anthropic, Gemini | Backend text drafting, bounded transient/quota fallback, preferred-provider retry after cooldown. | Real managed API credentials, model availability/credits and text generation. Personal ChatGPT/Claude subscription logins do not supply managed API credits. |
| Canva | Customer OAuth with PKCE, own editable design creation, opening Canva's editor and supported export jobs. | Registered callback, approved scopes/application, actual consent, create/edit/export and eligible Canva plan. Arbitrary AI layout manipulation is not implemented. |
| CapCut | Export a caption and brand-guidance package, then open the external editor. | Customer OAuth and automatic timeline editing are not implemented. The package is not a CapCut project file. |
| muse.ai | Open the external video-service website. | Customer library authorization, private video synchronization and analytics are not implemented. It is not one of the text-model fallback providers. |

## Clip Studio: how it should work

1. Sign in to your workspace and open `/clips`.
2. Upload a video you are allowed to edit. Hosted uploads stay separate for each workspace. Public arbitrary video-URL ingestion is not enabled by the hosted bridge.
3. Choose the available clip options and start the background render. The application shows the actual job state; it should not call a queued or failed render finished.
4. Review the rendered video and captions. Correct misheard words and re-render when needed. Download only your own job's video, subtitles and build evidence.
5. Publish the reviewed file through a supported video destination. The existing legacy `outbox/clip_studio/*.json` handoff does **not** add a video delivery worker, create a customer SaaS schedule or publish to Facebook by itself.

Hosted rendering is disabled unless the operator enables `AUTOPOST_CLIP_ENABLED=true` after installing `requirements-clip.txt`, `ffmpeg` and `ffprobe` and configuring persistent storage. Keep rendering off on a host that cannot support the workload. Run the worker for the hosted app; starting only the standalone local demo is not the customer SaaS integration.

`AUTOPOST_CLIP_DATA_DIR` holds private uploaded videos and render artifacts. It defaults to a `clip-data` directory beside the database selected by `AUTOPOST_WEB_DB`. The separate `Dockerfile.app` places both under `/var/lib/autoposter`; mount persistent storage there and keep the directory outside public static files. Its `HF_HOME=/var/lib/autoposter/models` also preserves downloaded speech models. `AUTOPOST_CLIP_MAX_UPLOAD_MB` can lower the upload cap from 200 MB to at least 1 MB. Sources must be 15 seconds to 90 minutes long; hosted clip requests are limited to 15–60 seconds. These bounds do not establish that a low-memory host can render every permitted input.

The engine's moment ranking is a heuristic. It is not measured audience engagement and does not guarantee viral or revenue results. A face-QA result measures that particular render's framing checks; it does not establish transcription accuracy or general model quality.

## Read-only connection checks

Run using the **same process environment as the deployed application**:

```bash
python scripts/qa/check_integrations.py
python scripts/qa/check_integrations.py --probe
python scripts/qa/check_integrations.py --probe --workspace YOUR_WORKSPACE_ID
```

The first command checks configuration and runtime dependency presence without network calls. It never loads `.env` implicitly, creates a database, imports the running application or migrates tables. The second makes bounded GET requests to fixed official AI model-catalog APIs when credentials are present. The third additionally reads only the selected workspace's saved connections through SQLite `mode=ro`, checking Facebook Page identity and Canva's design catalog when their stored credentials can be read.

The checker does not post content, generate paid AI responses, charge customers, send email, create or export a Canva design, refresh/revoke tokens or modify the database. Expired Canva access is reported without refreshing it. It follows no redirects and reports no credentials, response bodies, customer names, Page IDs or selected workspace ID. A network or authorization denial cannot turn into a successful check.

`read_only_api_verified` proves the specific GET request succeeded with the expected shape. It does not verify paid model generation, publishing permissions, delivery, quota fallback, a completed edit or an entire customer journey. All services keep `workflow_verification: not_checked` until a separate acceptance test records that evidence. Unsupported services remain explicitly marked as handoffs.

## Configuration corrections and current snapshot

- Canva's callback route is `/connections/canva/callback`. The older `/creative/canva/callback` example was incorrect and has been corrected in `EDITOR_CONNECTIONS.md`.
- Facebook's callback route is `/facebook/callback`. Register the actual deployed HTTPS origin with each provider; do not copy example domains into production.
- At the first local check on November 14, the Codex process had no customer Meta, Canva or managed AI credentials bound. This says nothing about credentials privately configured in Render. No live provider login or billable action was run by this check.
- The Clip Studio engine and its Python packages plus `ffmpeg`/`ffprobe` were present locally. The operator enable flag remained off. Dependency presence alone did not verify a transcript, finished render, deployed worker or social video delivery.
- Separate actual local sample runs subsequently produced a 1080×1920, 24 fps MP4 with audio and passing sampled face framing, followed by a successful caption correction and re-render. Those runs used a supplied test transcript; they did **not** verify Whisper recognition or a fresh end-to-end hosted upload/transcription journey. See `CLIP_STUDIO_INTEGRATION.md` for the exact media evidence.

Final focused verification: all 11 integration-checker tests passed, and all 23 Clip Studio adapter/web tests passed (12 adapter, 11 web). The same 23 tests passed with imports of optional `cv2`, `numpy`, `faster_whisper` and `yt_dlp` deliberately blocked, matching the lighter app-checks environment. With `ffmpeg`/`ffprobe` deliberately hidden from discovery, 22 tests passed and the one real media-toolchain regression skipped explicitly. These fixture/targeted checks do not turn the live-service gaps above into verified connections.

The operator must run the checks in Render and then complete controlled live acceptance tests. Never paste credentials into a chat or customer-facing form to make a status badge turn green.
