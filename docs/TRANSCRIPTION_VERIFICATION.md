# Speech model preparation and actual verification

Fresh Clip Studio transcription uses a **local, operator-provisioned Whisper
model**. Customer render requests never download model weights or ask the user
for an AI key. This is separate from managed OpenAI/Claude/Gemini caption writing.

`features.clip_studio.speech_model.speech_model_status()` is a cheap local check.
It reports ready only when the required weights/tokenizer/vocabulary are present
and match a successful local inference record, including dependency versions and
file fingerprints. Weight hashes are recorded once by the verifier. Changing the
model or speech runtime requires verification again. A readiness record is a
smoke-test result, not a certification of caption accuracy or future uptime.

## Operator configuration

- `AUTOPOST_CLIP_MODEL`: `base.en` by default. Supported names include `tiny`,
  `tiny.en`, `base`, `base.en`, `small`, `small.en`, `medium`, `medium.en`,
  and `large-v3`. Larger models require more RAM and CPU.
- `AUTOPOST_CLIP_MODEL_DIR`: explicit directory containing the converted model;
  or `AUTOPOST_CLIP_MODEL_CACHE`, with a model-name subdirectory.
- The fallback cache is `$HF_HOME/clip-studio/<model>`, or the normal local
  Hugging Face cache root when `HF_HOME` is not configured.
- `AUTOPOST_CLIP_CPU_THREADS`: CPU threads per transcription, bounded to 1–8,
  default 2. The application permits one background clip render at a time.
- `AUTOPOST_CLIP_MODEL_REVISION`: optional official model repository revision for
  reproducible downloads. The operator should pin the reviewed revision.

Keep the cache on the service's persistent volume. Pause rendering and wait for
active jobs before changing weights. Store no customer files inside the model
cache. Missing weights, an absent record or a changed runtime keeps fresh
rendering unavailable; saved-transcript caption revisions remain independent of
the speech-model code path.

## Commands on the intended host

From the repository root, using the installed app/Clip dependencies:

```bash
# Read-only status; exits 2 when not prepared, and never uses the network.
.venv/bin/python scripts/qa/clip_transcription_smoke.py

# Explicit operator download from the official configured Systran model repo,
# followed by actual offline model loading and sample speech inference.
.venv/bin/python scripts/qa/clip_transcription_smoke.py --warm

# Verify weights already installed by the operator, without downloading.
.venv/bin/python scripts/qa/clip_transcription_smoke.py --verify

# Full fresh transcription and real media render, with no supplied transcript.
.venv/bin/python scripts/qa/clip_transcription_smoke.py --verify --render \
  --output-dir /private/operator-check-output
```

The default speech sample is the bundled `app/sample/sample-podcast.mp4`, using
seconds 10–30. An installation that omits the sample must supply `--source` with
an authorized local speech video. `--start` and `--duration` choose a 5–60 second
verification excerpt. The bounded inference subprocess defaults to 900 seconds;
it terminates and removes private temporary audio on timeout. The optional full
render has its own adapter timeout and private-work cleanup.

The operator-only `--warm` operation needs permitted access to the official
Hugging Face model service and its actual signed artifact destinations. Keep the
inherited proxy, certificate checks and network policy. A denied destination is
a configuration blocker; do not download through another host to bypass it.
Ordinary jobs load an explicit local path with `local_files_only=True`.

Successful output reports only model/status, check type and timed-word count;
it does not print sample captions, credentials, private source paths or signed
download URLs. The health record stores an audio hash, not the sample words.
Use an actual customer upload/render test and full playback before a paid launch.

## Evidence from this workspace

On November 14, 2026, the default local model was absent. The normal sandbox could
not reach its proxy; the supported escalated request to the official
`huggingface.co/api/models/Systran/faster-whisper-base.en` endpoint was explicitly
denied by the proxy with CONNECT 403. No alternate-host workaround was attempted.
The local status command correctly exits 2 with `reason: missing_weights`.

Twelve focused fixture tests cover the no-download boundary, missing/changed
weights, runtime changes, invalid health records, model-path loading, explicit
operator warmup, sanitized errors and child timeout/reaping. Existing real video
editing checks used a supplied transcript; those do **not** establish fresh
speech recognition. No fresh Whisper inference or live Render transcription was
verified from this workspace. Run the commands and actual customer acceptance
flow on the configured Render service before describing transcription as ready.
