# Clip Studio integration evidence — November 14, 2026

Clip Studio was added on `feature/clip-studio` and brought into the newer customer app. Its original standalone HTTP demo is a single-user local application, not a safe customer API. The hosted customer integration uses its own authenticated, workspace-scoped bridge and calls the rendering adapter directly; it must never expose or proxy the demo server.

## Rendering contract

```python
from features.clip_studio import render_clip

result = render_clip(
    owned_uploaded_video,
    {"start": 10, "end": 30, "layout": "cutaway", "style": "standard"},
    out_dir=private_workspace_job_directory,
    local_only=True,
    timeout=1800,
)
```

The result contains `clip`, `srt`, `build_json`, `transcript`, `build`, and `summary`. The generated transcript is a source-timeline JSON list of `{w, s, e}` word records. To correct captions without another speech-model run, pass that job's original `transcript_file` plus an edited clip-timeline `captions_file`, preserving the original start/end options. These files must be selected by server-side ownership checks, never accepted as arbitrary client filesystem paths.

The adapter resolves local input paths before changing the engine working directory, bounds render execution, terminates the engine process group when a POSIX timeout expires, rejects output filename traversal, verifies real nonempty video/caption artifacts, and requires an explicit passing framing check. A missing, skipped or failed quality check is not a successful clip. Debug-only `allow_qa_fail` cannot bypass the legacy outbox quality guard.

With `local_only=True`, remote video/overlay inputs are rejected. FFmpeg and FFprobe receive a `file,pipe` protocol allowlist. Reads of the original uploaded source also receive a `mov,matroska,webm` demuxer allowlist before parsing: protocol restrictions alone cannot prevent DASH manifests disguised as MP4 from following other local files. Generated intermediates retain their appropriate formats. Engine work is placed inside the private job output directory and removed by the adapter even if a timeout kills the engine before its own cleanup runs. These controls do not replace an isolated media-processing worker, upload limits, patched decoders or file validation in the hosted bridge.

## Customer branding

A private per-job kit can show a customer's own business name and accent without using another business's logo:

```json
{"name":"My Bread Business","end_card_name":"My Bread Business","accent":"#217A51","text_only":true}
```

The kit produces a name-and-initials end card. A real logo kit instead needs a server-owned image path. Relative image paths resolve against the kit's location, so a private job kit must use absolute server-owned paths or copy the assets into its own directory. Customer forms must not accept arbitrary logo/brand file paths.

## Checks actually completed

- Fourteen adapter/engine regression tests passed. They cover real child-process execution and timeout/private-file cleanup, quality failures/skips, missing artifacts, path/time validation, outbox isolation, saved transcript validation, decoder protocol/demuxer restrictions, customer branding, a real FFmpeg trim/concat/end-card join, and rejection of a real DASH manifest disguised as MP4.
- A real render from the bundled local sample succeeded with an explicitly supplied test transcript. The result was an MP4 with audio, 1080×1920 at 24 fps, 3.708 seconds including its end card, measured at −14.06 LUFS. Its sampled face-framing check passed.
- A second real render reused that saved transcript, changed the caption text, preserved the original 9.95–12.85 second source window, and passed the same framing check. The resulting SRT contained the corrected words.
- The real render exposed an FFmpeg failure: the trim/concat graph advertised an undefined frame rate to `xfade`. Explicit frame-rate restoration before the end-card transition fixed it. The small real FFmpeg regression test protects that behavior.
- The standalone demo now binds to localhost, rejects cross-site POSTs and foreign Host headers, and escapes editable text before inserting it into its jobs UI. It still has a shared local library and is not a hosted multi-user service.

The supplied test transcript deliberately avoids an initial speech-model download. These runs verify actual media editing, caption replacement and rendering, not speech-recognition accuracy.

## Remaining external verification and limits

Fresh automatic transcription needs the configured Whisper model to be installed or downloaded from its official model host. It was not downloaded or verified here. Uploads without a saved transcript will use that transcription path when the model is available. This CPU-heavy work requires sufficient server memory, disk and worker time; it is not an instant browser-only editor.

The existing strongest-moment picker scores transcript features such as claims, numbers and story beats. It does not read social-platform engagement data or guarantee viral reach. Local CLI video URL download and platform permissions were not verified with a live source.

`queue_clip_for_publishing` exports a video and JSON sidecar into the legacy outbox. It does not upload a video to Facebook, turn the newer text publisher into a video publisher, schedule a customer video, or connect Canva, CapCut or muse.ai. A real video-capable platform adapter and explicit customer destination/authorization are still required for automatic video publication. The customer UI must describe downloaded clips as ready to review and post, not already published.

No live Render URL, cloud provider account connection, customer video publication, signed app-store package or store approval was verified by these local media tests.
