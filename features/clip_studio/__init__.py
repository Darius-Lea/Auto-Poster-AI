"""Clip Studio: podcast episode in, finished vertical clip out,
queued into Auto Poster's outbox for scheduling/posting.

See features/clip_studio/README.md.
"""
from features.clip_studio.clip_studio import (  # noqa: F401
    ClipStudioError,
    brand_kits,
    build_caption_text,
    queue_clip_for_publishing,
    render_and_queue,
    render_clip,
    summarize,
)
