# WonderPlay — production image (used by Render; works on any Docker host).
#
# Debian trixie: its Node.js (20.x) is new enough for yt-dlp's YouTube
# challenge solver, which needs a JavaScript runtime next to Python.
FROM python:3.13-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && node --version

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY db db
COPY frontend frontend
COPY backend backend

# Non-root user; /var/data is where a Render persistent disk gets mounted.
RUN useradd --create-home --uid 10001 wonderplay \
 && mkdir -p /var/data/covers /var/data/downloads \
 && chown -R wonderplay:wonderplay /var/data /app
USER wonderplay

ENV HOST=0.0.0.0 \
    PORT=10000 \
    COVER_CACHE_DIR=/var/data/covers \
    DOWNLOAD_DIR=/var/data/downloads \
    SCAN_ON_STARTUP=false \
    AUTO_MIGRATE=true \
    MEDIA_ROOTS=

WORKDIR /app/backend
EXPOSE 10000
# --proxy-headers: Render terminates HTTPS in front of the app; this lets the
# app see https:// so the sign-in cookie is marked Secure.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 30"]
