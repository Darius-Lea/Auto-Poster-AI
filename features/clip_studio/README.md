# Clip Studio (Auto Poster AI feature)

Turn your own video into a captioned vertical clip.

Clip Studio turns any podcast episode — a local file or a video URL —
into a vertical clip (1080×1920): it finds or takes the
strongest moment, frames the speaker, adds the hook and word-by-word
captions, normalizes loudness to −14 LUFS, and face-checks the result
before the hosted app marks it ready. Customers can review and download
their clips. The optional CLI outbox export is a file handoff; automatic
social video publication is not connected by this feature.

## What it does

- **Moment picking** — give an explicit start/end window, or let the
  engine auto-pick the strongest ~30–60s (numbers, claims, story beats,
  questions score highest). This is transcript scoring, not observed
  social engagement or live trend analysis.
- **Layouts** — `cutaway` follows the speaker with hard cuts (one slow
  opening push-in, one punch-in on the peak line that holds); `split`
  stacks both speakers of a two-panel source in steady "own camera"
  panels with no punch-in and no extra zoom.
- **Styles** — `standard` (the studio recipe), `bar`, and `minimal`
  registers vary expression only; `random` picks one reproducibly from
  a seed that is recorded in the build log.
- **Brand kits** — `engine/brands/*.json` drives the hook/caption
  accent, progress bar, logo and end-card name per show or client. The
  Podcast Clips That Convert kit is the built-in default.
  The hosted form instead supports each customer's business name and
  chosen color using a private text-and-initials end card.
- **Editable captions** — render once, fix any misheard word in the
  transcript, and re-render with `--captions-file` plus the saved
  `--transcript-file`: the corrected words burn in on the original
  timings without another transcription. The hosted correction flow
  supplies both files automatically. A
  `--corrections` word→replacement map fixes names everywhere at once.
- **Point-and-appear overlay** — at most one per clip: an image of the
  thing being talked about flies in beside the speaker
  (`--overlay` in the engine / `overlay` in the options dict).
- **QA gate** — every render is sampled (densely around every framing
  change) and fails loudly if a speaking moment has no fully-visible
  face. A failed clip is kept for inspection but never queued silently.

## Setup

The main pipeline's dependencies are unchanged. Clip Studio adds its
own, kept separate so the roster pipeline stays lean:

```bash
pip install -r requirements.txt -r requirements-clip.txt
```

You also need `ffmpeg` on the PATH. Rendering is CPU work: a ~45s clip
takes minutes, depending on the machine. Render outputs and engine
work files land in git-ignored folders (`renders/`, `data/`,
`engine/runs/`).

## Run it

Command line, from the repo root:

```bash
# explicit window, then export the clip to the legacy outbox (not a live post)
python -m features.clip_studio.clip_studio episode.mp4 --start 600 --end 645 --queue

# auto-pick 45 seconds from an authorized URL and export the files
python -m features.clip_studio.clip_studio https://www.youtube.com/watch?v=... --queue
```

Programmatically:

```python
from features.clip_studio import render_clip, queue_clip_for_publishing

result = render_clip("episode.mp4", {"start": 600, "end": 645,
                                     "style": "standard",
                                     "brand": "podcast-clips-that-convert"})
ref = queue_clip_for_publishing(result)   # -> outbox/clip_studio/<stem>.json
# or both at once: render_and_queue(...)
```

`render_clip` returns the clip path, its `.srt`, the build-log JSON,
and a summary (hook, peak line, measured loudness, QA result).

## Hosted customer experience

Auto Poster's authenticated `/clips` page uses its existing customer
account. Customers upload a video they own or have permission to edit,
choose an automatic moment or start time, clip length, style, optional
title/hook, business name and color, then watch their private job queue.
Completed clips can be previewed/downloaded; captions can be corrected
and re-rendered while preserving the source moment. The phone layout
uses the same account and backend.

The hosted bridge accepts local video uploads, not remote URLs or server
filesystem paths. Its jobs, downloads and deletions are scoped to the
authenticated workspace. Email verification, upload/queue limits and
CSRF checks apply. Rendering is disabled by default until the operator
installs the dependencies and enables it. First-time speech recognition
also needs the official Whisper model available on the host. Full
90-minute sources can take substantial CPU time; the hosted render has
a 30-minute execution limit and can fail rather than finish instantly.

No video is automatically sent to social media. A downloaded MP4 can be
reviewed and posted by its owner. Canva, CapCut, muse.ai, live platform
engagement/trend data and automated video publishing require their own
working integrations; opening Clip Studio does not enable them.

## Standalone local demo

The original single-user UI is still available for local testing:

```bash
python features/clip_studio/app/server.py   # http://localhost:8765
```

Link or upload in, watch the job, download the clip, edit captions and
re-render — all on the local machine, no account needed. This demo binds
to `127.0.0.1`; do not expose it as a public web service. It has a shared
local library and does not supply customer isolation. The UI speaks
plain English, opens with a "Try it with a sample podcast" button (the
bundled sample episode lives in `app/sample/`, the example clip in
`app/assets/`), shows each job's progress stage while it renders, and
carries a "Make money with this" page at `/money` explaining how the
clips are sold.

## How a finished clip flows into Auto Poster

`queue_clip_for_publishing()` mirrors the queue backend
(`core/publisher.py::_queue`) exactly: it copies the media into
`outbox/clip_studio/` and writes a JSON sidecar next to it:

```json
{
  "caption": "<hook>\n\n\"<peak line>\"\n\nClip by <brand>",
  "record": { "name": "...", "charge": "<peak line>", "kind": "clip", "...": "..." },
  "image": "<stem>.mp4",
  "media_type": "video",
  "clip": { "hook": "...", "peak_line": "...", "loudness_lufs": "-14.03",
            "qa_status": "pass", "style": "standard", "brand": "...", "moment": {} }
}
```

The caption is built from the clip's hook and peak line. The export
contains a copied MP4 and JSON sidecar. Existing legacy publishers are
photo-oriented, and the customer publisher handles text. Neither
consumes this sidecar as a live scheduled video. A video-capable
publisher with explicit customer authorization and destination choice
must be implemented before this handoff can publish. Quality-check
failures and invalid workspace queue paths are rejected.

## Verified behavior and remaining checks

Fourteen adapter/engine regression tests pass, including a real FFmpeg
trim/concat/end-card join. A real local sample render and a corrected
caption re-render produced 1080×1920, 24 fps videos with audio; the
measured loudness was −14.06 LUFS and sampled framing checks passed.
These media checks supplied a test transcript, so they verify rendering
and correction reuse, not fresh speech-model transcription accuracy.
Live URL downloads, automatic transcription/model installation on
Render, video publication, native packaging and store approval remain
separate verification. See [the integration evidence](../../docs/CLIP_STUDIO_INTEGRATION.md)
and the hosted deployment guide for setup and current limits.

## Layout of this feature

```
features/clip_studio/
  clip_studio.py   adapter: render_clip() + outbox handoff + CLI
  engine/          the clip engine (clip_engine.py, assets/, brands/)
  app/             standalone local web UI (stdlib only):
                   server.py, sample/ (bundled sample episode),
                   assets/ (example clip)
  renders/         render outputs (git-ignored)
  data/            web UI jobs/outputs (git-ignored)
```
