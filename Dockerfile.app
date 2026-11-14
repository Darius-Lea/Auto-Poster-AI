# Customer web app and Clip Studio. The legacy Dockerfile/publisher stay separate.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    AUTOPOST_WEB_HOST=0.0.0.0 \
    AUTOPOST_WEB_DB=/var/lib/autoposter/app.db \
    AUTOPOST_CLIP_DATA_DIR=/var/lib/autoposter/clip-data \
    HF_HOME=/var/lib/autoposter/models
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-app.txt requirements-clip.txt ./
RUN pip install --no-cache-dir -r requirements-app.txt -r requirements-clip.txt
# Explicit sources only: never copy local environments, customer media or .env files.
COPY *.py ./
COPY adapters/ ./adapters/
COPY core/ ./core/
COPY config/ ./config/
COPY assets/ ./assets/
COPY templates/ ./templates/
COPY static/ ./static/
COPY website/ ./website/
COPY scripts/qa/check_integrations.py ./scripts/qa/check_integrations.py
COPY features/__init__.py ./features/__init__.py
COPY features/clip_studio/__init__.py features/clip_studio/clip_studio.py ./features/clip_studio/
COPY features/clip_studio/engine/clip_engine.py ./features/clip_studio/engine/
COPY features/clip_studio/engine/assets/ ./features/clip_studio/engine/assets/
COPY features/clip_studio/engine/brands/ ./features/clip_studio/engine/brands/
COPY features/clip_studio/app/assets/example-clip.mp4 ./features/clip_studio/app/assets/example-clip.mp4
RUN mkdir -p /var/lib/autoposter
# One worker owns SQLite and the background schedulers. Render threads never
# occupy HTTP handlers. The operator must attach the persistent data directory.
CMD ["sh", "-c", "exec gunicorn app_mvp_wsgi:app --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 4 --timeout 120"]
