"""Clip Studio feature for Auto Poster AI.

Turns a podcast episode (local file or URL) into a finished vertical
clip with the bundled clip engine, then hands the clip into the same
``outbox/<workspace_key>/`` queue flow the rest of Auto Poster uses:
a media file plus a JSON sidecar carrying ``caption``, ``record`` and
the media basename — the exact shape ``core.publisher._queue`` writes.

Programmatic use::

    from features.clip_studio import render_clip, queue_clip_for_publishing
    result = render_clip("episode.mp4", {"start": 600, "end": 645})
    ref = queue_clip_for_publishing(result)   # -> outbox/clip_studio/<stem>.json

Command line (from the repo root)::

    python -m features.clip_studio.clip_studio episode.mp4 --start 600 --end 645 --queue

The engine's extra Python dependencies live in ``requirements-clip.txt``
at the repo root; the main pipeline's ``requirements.txt`` is untouched.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import date
from typing import Optional

log = logging.getLogger("features.clip_studio")

FEATURE_DIR = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.join(FEATURE_DIR, "engine")
ENGINE_PY = os.path.join(ENGINE_DIR, "clip_engine.py")
BRANDS_DIR = os.path.join(ENGINE_DIR, "brands")
REPO_ROOT = os.path.dirname(os.path.dirname(FEATURE_DIR))
DEFAULT_OUT_DIR = os.path.join(FEATURE_DIR, "renders")
DEFAULT_WORKSPACE_KEY = "clip_studio"


class ClipStudioError(RuntimeError):
    """A render failed, or the finished clip failed the engine's QA gate."""


def _resolve_brand(brand: str) -> str:
    """Accept a kit name ('podcast-clips-that-convert'), a file name, or a path."""
    if os.path.isfile(brand):
        return brand
    for cand in (brand, brand + ".json"):
        path = os.path.join(BRANDS_DIR, cand)
        if os.path.isfile(path):
            return path
    raise ClipStudioError(f"Unknown brand kit {brand!r}; kits live in {BRANDS_DIR}")


def brand_kits() -> list[dict]:
    """The brand kits bundled with the feature (the studio kit is the default)."""
    kits = []
    if os.path.isdir(BRANDS_DIR):
        for fn in sorted(os.listdir(BRANDS_DIR)):
            if fn.endswith(".json"):
                name = fn[:-5]
                try:
                    with open(os.path.join(BRANDS_DIR, fn), encoding="utf-8") as fh:
                        name = json.load(fh).get("name", name)
                except (OSError, json.JSONDecodeError):
                    pass
                kits.append({"file": fn, "name": name})
    return kits


def summarize(build: Optional[dict]) -> Optional[dict]:
    """The handful of build-log facts callers and publishers care about."""
    if not build:
        return None
    ln = build.get("loudnorm_output") or {}
    qa = build.get("qa_gate") or {}
    return {
        "hook": build.get("hook"),
        "peak_line": build.get("peak_line"),
        "loudness_lufs": ln.get("input_i"),
        "true_peak_dbtp": ln.get("input_tp"),
        "qa_status": qa.get("status"),
        "qa_checked": qa.get("checked"),
        "style": (build.get("style") or {}).get("name"),
        "brand": (build.get("brand") or {}).get("name"),
        "moment": build.get("moment"),
    }


def render_clip(source: str, options: Optional[dict] = None,
                out_dir: Optional[str] = None, out_name: str = "clip.mp4",
                python: Optional[str] = None, timeout: float = 1800,
                local_only: bool = False) -> dict:
    """Render one clip with the bundled engine.

    ``source`` is a local file path or a video URL (the engine downloads
    URLs itself). ``options`` mirrors the engine's flags: ``start`` /
    ``end`` (seconds, explicit window) or ``auto`` + ``duration``;
    ``layout`` (cutaway|split), ``style`` (standard|bar|minimal|random),
    ``seed``, ``brand`` (kit name or path), ``hook``, ``title``,
    ``corrections`` (dict of word -> replacement), ``captions_file``
    (an .srt/.json whose words are burned on the original timings) and
    ``overlay`` / ``overlay_at`` / ``overlay_dur`` / ``overlay_w`` for the
    point-and-appear visual. Set ``allow_qa_fail`` to keep a clip the
    face-check QA gate rejected (it is still returned, flagged).

    Returns ``{"clip", "srt", "build_json", "build", "summary"}``.
    Raises ClipStudioError when the engine fails or the QA gate fails.
    """
    options = dict(options or {})
    if not isinstance(source, str) or not source.strip():
        raise ClipStudioError("Choose a video file.")
    source = source.strip()
    remote = source.startswith(("http://", "https://"))
    if local_only and remote:
        raise ClipStudioError("Upload a video file; remote downloads are not enabled for hosted jobs.")
    if not remote:
        source = os.path.abspath(os.path.expanduser(source))
        if not os.path.isfile(source):
            raise ClipStudioError("The source video file is missing.")
    if (not isinstance(out_name, str) or os.path.basename(out_name) != out_name
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.mp4", out_name)):
        raise ClipStudioError("Choose an MP4 output file name without folders.")
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        raise ClipStudioError("Render timeout must be a number.")
    if not math.isfinite(timeout) or not 1 <= timeout <= 14400:
        raise ClipStudioError("Render timeout must be between 1 and 14400 seconds.")
    try:
        for key in ('start', 'end', 'duration'):
            if options.get(key) is not None:
                options[key] = float(options[key])
                if not math.isfinite(options[key]) or options[key] < 0:
                    raise ValueError
        if options.get('duration') is not None and not 0 < options['duration'] <= 120:
            raise ValueError
        if options.get('end') is not None:
            if options.get('start') is None or not 0 < options['end'] - options['start'] <= 120:
                raise ValueError
    except (TypeError, ValueError):
        raise ClipStudioError("Choose a valid start and an end up to 120 seconds later.")
    if options.get('layout', 'cutaway') not in ('cutaway', 'split'):
        raise ClipStudioError("Choose a supported clip layout.")
    if options.get('style', 'standard') not in ('standard', 'bar', 'minimal', 'random'):
        raise ClipStudioError("Choose a supported clip style.")
    out_dir = os.path.abspath(out_dir or os.path.join(
        DEFAULT_OUT_DIR, uuid.uuid4().hex))
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, out_name)

    cmd = [python or sys.executable, ENGINE_PY, source, "--out", out]
    if options.get("start") is not None:
        cmd += ["--start", str(options["start"])]
        if options.get("end") is not None:
            cmd += ["--end", str(options["end"])]
        else:
            cmd += ["--duration", str(options.get("duration") or 45)]
    else:
        cmd += ["--auto", "--duration", str(options.get("duration") or 45)]
    cmd += ["--layout", options.get("layout") or "cutaway"]
    cmd += ["--style", options.get("style") or "standard"]
    if options.get("seed") is not None:
        cmd += ["--seed", str(options["seed"])]
    if options.get("brand"):
        cmd += ["--brand", _resolve_brand(str(options["brand"]))]
    if options.get("hook"):
        cmd += ["--hook", str(options["hook"])]
    if options.get("title"):
        cmd += ["--title", str(options["title"])]
    if options.get("corrections"):
        cpath = os.path.join(out_dir, "corrections.json")
        with open(cpath, "w", encoding="utf-8") as fh:
            json.dump(options["corrections"], fh)
        cmd += ["--corrections", cpath]
    if options.get("captions_file"):
        cmd += ["--captions-file", os.path.abspath(str(options["captions_file"]))]
    if options.get("transcript_file"):
        cmd += ["--transcript-file", os.path.abspath(str(options["transcript_file"]))]
    if options.get("overlay"):
        overlay = str(options["overlay"])
        if local_only and overlay.startswith(("http://", "https://")):
            raise ClipStudioError("Hosted overlays must be uploaded local files.")
        cmd += ["--overlay", overlay if overlay.startswith(("http://", "https://")) else os.path.abspath(overlay)]
        if options.get("overlay_at") is not None:
            cmd += ["--overlay-at", str(options["overlay_at"])]
        if options.get("overlay_dur") is not None:
            cmd += ["--overlay-dur", str(options["overlay_dur"])]
        if options.get("overlay_w") is not None:
            cmd += ["--overlay-w", str(options["overlay_w"])]

    log_path = os.path.join(out_dir, "engine.log")
    log.info("Clip Studio render: %s", " ".join(cmd))
    env = dict(os.environ)
    private_work = None
    if local_only:
        env["CLIP_STUDIO_LOCAL_ONLY"] = "true"
        private_work = os.path.join(out_dir, 'work')
        os.makedirs(private_work, mode=0o700, exist_ok=True)
        os.chmod(private_work, 0o700)
        env['CLIP_STUDIO_WORK_ROOT'] = private_work
    with open(log_path, "w", encoding="utf-8") as logf:
        logf.write("$ " + " ".join(cmd) + "\n\n")
        logf.flush()
        try:
            proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                                    cwd=out_dir, env=env,
                                    start_new_session=(os.name == "posix"))
            try:
                proc.wait(timeout=float(timeout))
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait()
                raise ClipStudioError("The clip exceeded the render time limit. Try a shorter source or clip.")
        except OSError as exc:
            raise ClipStudioError("The clip engine could not start.") from exc
        finally:
            if private_work:
                # Timeout kills the engine before its finally can run.
                # Its audio/frames must still be cleaned inside this job.
                shutil.rmtree(private_work, ignore_errors=True)
    build = None
    build_json = os.path.splitext(out)[0] + ".build.json"
    if os.path.isfile(build_json):
        try:
            with open(build_json, encoding="utf-8") as fh:
                build = json.load(fh)
        except (OSError, json.JSONDecodeError):
            build = None
    qa_status = ((build or {}).get("qa_gate") or {}).get("status")
    debug_failure = bool(options.get("allow_qa_fail") and qa_status == "fail")
    if (proc.returncode != 0 and not debug_failure) or build is None:
        tail = ""
        try:
            with open(log_path, encoding="utf-8") as fh:
                tail = fh.read()[-2000:]
        except OSError:
            pass
        raise ClipStudioError(
            f"Clip engine failed (exit {proc.returncode}); see {log_path}\n{tail}")
    summary = summarize(build)
    if summary["qa_status"] != "pass" and not debug_failure:
        raise ClipStudioError(
            "The clip has not passed the face-check quality gate. "
            "A skipped or failed check cannot be treated as a successful render.")
    srt = os.path.splitext(out)[0] + ".srt"
    if not all(os.path.isfile(path) and os.path.getsize(path) > 0 for path in (out, srt)):
        raise ClipStudioError("The engine did not produce a complete video and captions file.")
    return {
        "clip": out,
        "srt": srt,
        "transcript": os.path.splitext(out)[0] + ".transcript.json",
        "build_json": build_json,
        "build": build,
        "summary": summary,
    }


def build_caption_text(build: Optional[dict] = None,
                       summary: Optional[dict] = None) -> str:
    """Caption for a finished clip, built from its hook and peak line."""
    summary = summary or summarize(build) or {}
    parts = []
    if summary.get("hook"):
        parts.append(summary["hook"])
    if summary.get("peak_line"):
        parts.append(f'"{summary["peak_line"]}"')
    if summary.get("brand"):
        parts.append(f"Clip by {summary['brand']}")
    return "\n\n".join(parts)


def queue_clip_for_publishing(result: dict, workspace_key: str = DEFAULT_WORKSPACE_KEY,
                              caption: Optional[str] = None,
                              outbox_dir: Optional[str] = None,
                              source_url: str = "") -> str:
    """Hand a finished clip into Auto Poster's outbox/queue flow.

    Mirrors ``core.publisher._queue``: writes the media file and a JSON
    sidecar ``{"caption", "record", "image"}`` into
    ``outbox/<workspace_key>/``. The clip keeps its real extension (.mp4)
    instead of the photo flow's .jpg, and the sidecar adds ``media_type``
    and the clip's build ``summary`` so a video-aware publisher can pick
    it up. Returns the sidecar JSON path (the publish reference, same as
    the queue backend returns).
    """
    if (not isinstance(workspace_key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", workspace_key)):
        raise ClipStudioError("Invalid workspace queue name.")
    build = result.get("build")
    summary = result.get("summary") or summarize(build)
    if not summary or summary.get("qa_status") != "pass":
        raise ClipStudioError("Only clips that passed quality checks can be exported to the outbox.")
    clip_path = result["clip"]
    if not os.path.isfile(clip_path) or os.path.getsize(clip_path) == 0:
        raise ClipStudioError("The finished clip file is missing or empty.")
    caption = caption if caption is not None else build_caption_text(build, summary)
    title = (result.get("title") or (build or {}).get("title")
             or (summary or {}).get("hook") or "Podcast clip")
    record = {
        "name": str(title),
        "charge": (summary or {}).get("peak_line") or "",
        "booking_date": date.today().isoformat(),
        "source_url": source_url or str((build or {}).get("input", "")),
        "image_url": "",
        "age": None,
        "kind": "clip",
    }
    try:  # keep the record in the repo's canonical Record shape
        sys.path.insert(0, REPO_ROOT) if REPO_ROOT not in sys.path else None
        from adapters.base import Record
        from dataclasses import asdict
        record = asdict(Record(**record))
    except Exception:
        pass
    folder = os.path.join(outbox_dir or os.path.join(REPO_ROOT, "outbox"),
                          workspace_key)
    os.makedirs(folder, exist_ok=True)
    stem = uuid.uuid4().hex
    ext = os.path.splitext(clip_path)[1] or ".mp4"
    media_dest = os.path.join(folder, stem + ext)
    shutil.copyfile(clip_path, media_dest)
    json_dest = os.path.join(folder, stem + ".json")
    payload = {
        "caption": caption,
        "record": record,
        "image": os.path.basename(media_dest),
        "media_type": "video",
        "clip": summary,
    }
    with open(json_dest, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    log.info("Clip Studio queued %s -> %s", clip_path, json_dest)
    return json_dest


def render_and_queue(source: str, options: Optional[dict] = None,
                     workspace_key: str = DEFAULT_WORKSPACE_KEY,
                     outbox_dir: Optional[str] = None, **render_kwargs) -> dict:
    """Render a clip and hand it straight into the outbox queue."""
    result = render_clip(source, options, **render_kwargs)
    result["queued"] = queue_clip_for_publishing(
        result, workspace_key=workspace_key, outbox_dir=outbox_dir,
        source_url=source if str(source).startswith("http") else "")
    return result


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Clip Studio: turn a podcast episode into a vertical clip")
    parser.add_argument("input", help="Local video file or video URL")
    parser.add_argument("--start", type=float, help="Start second (explicit window)")
    parser.add_argument("--end", type=float, help="End second (explicit window)")
    parser.add_argument("--duration", type=float, default=45,
                        help="Clip length for auto-pick or a start-only window")
    parser.add_argument("--layout", choices=("cutaway", "split"), default="cutaway")
    parser.add_argument("--style", choices=("standard", "bar", "minimal", "random"),
                        default="standard")
    parser.add_argument("--seed", type=int, help="Seed for --style random")
    parser.add_argument("--brand", help="Brand kit name or path (default: studio kit)")
    parser.add_argument("--hook", help="Hook text override")
    parser.add_argument("--title", help="Show name / attribution burned into the clip")
    parser.add_argument("--corrections",
                        help='JSON word fixes, e.g. {"Monocero": "Manassero"}')
    parser.add_argument("--captions-file", help="Edited .srt/.json to burn on the original timings")
    parser.add_argument("--transcript-file", help="Saved source-timeline .transcript.json to reuse instead of transcribing")
    parser.add_argument("--out-dir", help="Where the render lands (default: features/clip_studio/renders/<time>)")
    parser.add_argument("--queue", action="store_true",
                        help="Hand the finished clip into outbox/<workspace_key>/")
    parser.add_argument("--workspace-key", default=DEFAULT_WORKSPACE_KEY)
    parser.add_argument("--allow-qa-fail", action="store_true",
                        help="Keep a failed-QA clip for inspection; it cannot be exported to the outbox")
    args = parser.parse_args(argv)

    options = {
        "start": args.start, "end": args.end, "duration": args.duration,
        "layout": args.layout, "style": args.style, "seed": args.seed,
        "brand": args.brand, "hook": args.hook, "title": args.title,
        "captions_file": args.captions_file, "transcript_file": args.transcript_file,
        "allow_qa_fail": args.allow_qa_fail,
    }
    if args.corrections:
        try:
            options["corrections"] = json.loads(args.corrections)
        except json.JSONDecodeError as exc:
            parser.error(f"--corrections is not valid JSON: {exc}")
    try:
        if args.queue:
            result = render_and_queue(args.input, options,
                                      workspace_key=args.workspace_key,
                                      out_dir=args.out_dir)
        else:
            result = render_clip(args.input, options, out_dir=args.out_dir)
    except ClipStudioError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    print(json.dumps({"clip": result["clip"], "srt": result["srt"],
                      "summary": result["summary"],
                      "queued": result.get("queued")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
