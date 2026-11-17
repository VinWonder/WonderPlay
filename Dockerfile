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
 && apt-get install -y --no-install-recommends nodejs ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && node --version

# Cloudflare WARP tools (see docker/start.sh): wgcf registers a free WARP
# account, wireproxy exposes it as a local SOCKS5 proxy without root.
# Pinned versions, verified by SHA-256.
ARG WGCF_VERSION=2.3.1
ARG WGCF_SHA256=4fb7a136aa470ece11d17949c7db07b040ec9c604742dc8680bcf1889f85d0c8
ARG WIREPROXY_VERSION=1.1.3
ARG WIREPROXY_SHA256=e88c1d090740373fc606c1bafd81d9a5eadc642cce5667616e20e9d7a444f51c
RUN set -eux; \
    curl -fsSL -o /usr/local/bin/wgcf "https://github.com/ViRb3/wgcf/releases/download/v${WGCF_VERSION}/wgcf_${WGCF_VERSION}_linux_amd64"; \
    echo "${WGCF_SHA256}  /usr/local/bin/wgcf" | sha256sum -c -; \
    curl -fsSL -o /tmp/wireproxy.tgz "https://github.com/windtf/wireproxy/releases/download/v${WIREPROXY_VERSION}/wireproxy_linux_amd64.tar.gz"; \
    echo "${WIREPROXY_SHA256}  /tmp/wireproxy.tgz" | sha256sum -c -; \
    tar -xzf /tmp/wireproxy.tgz -C /usr/local/bin wireproxy; \
    rm /tmp/wireproxy.tgz; \
    chmod 0755 /usr/local/bin/wgcf /usr/local/bin/wireproxy

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY db db
COPY frontend frontend
COPY backend backend
COPY docker/start.sh /usr/local/bin/wonderplay-start
RUN sed -i 's/\r$//' /usr/local/bin/wonderplay-start && chmod 0755 /usr/local/bin/wonderplay-start

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
    WARP_PROXY=true \
    MEDIA_ROOTS=

WORKDIR /app/backend
EXPOSE 10000
CMD ["wonderplay-start"]
