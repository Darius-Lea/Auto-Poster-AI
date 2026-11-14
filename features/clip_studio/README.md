# Clip Studio (Auto Poster AI feature)

Podcast episodes in. Client-getting clips out.

Clip Studio turns any podcast episode — a local file or a video URL —
into a finished vertical clip (1080×1920): it finds or takes the
strongest moment, frames the speaker, adds the hook and word-by-word
captions, normalizes loudness to −14 LUFS, and face-checks the result
before it is allowed out. The finished clip is then handed into Auto
Poster's existing outbox queue, ready to be scheduled and posted like
everything else the pipeline publishes.

## What it does

- **Moment picking** — give an explicit start/end window, or let the
  engine auto-pick the strongest ~30–60s (numbers, claims, story beats,
  questions score highest).
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
- **Editable captions** — render once, fix any misheard word in the
  transcript, and re-render with `--captions-file`: the corrected words
  burn in on the original timings, no re-transcription. A
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
# explicit window, then hand the clip into the outbox queue
python -m features.clip_studio.clip_studio episode.mp4 --start 600 --end 645 --queue

# auto-pick the best 45 seconds from a URL
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

Local web UI — Auto Poster's hosted app shell does not exist yet, so
the feature ships with the standalone UI it was proven with:

```bash
python features/clip_studio/app/server.py   # http://localhost:8765
```

Link or upload in, watch the job, download the clip, edit captions and
re-render — all on the local machine, no account needed. The UI speaks
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

The caption is built from the clip's hook and peak line. From there the
clip sits in the same outbox the GitHub Actions workflow already
uploads as an artifact. The existing publishers are photo-oriented
today (the Meta backend posts photos), so a clip waits in the queue
until a video-capable publisher consumes it — scheduling/posting picks
it up from the outbox unchanged, which is the seam this feature was
built against.

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
