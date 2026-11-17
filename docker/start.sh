#!/bin/sh
# WonderPlay container entrypoint.
#
# With WARP_PROXY=true, YouTube traffic leaves through Cloudflare WARP instead
# of the hosting provider's data-centre IP (which YouTube answers with
# "Sign in to confirm you're not a bot"). wgcf registers a free WARP account,
# wireproxy turns it into a local SOCKS5 proxy (userspace WireGuard: no root,
# no kernel module). If anything fails, the app simply starts without it.
set -u

if [ "${WARP_PROXY:-false}" = "true" ]; then
  W=/tmp/warp
  mkdir -p "$W" && cd "$W" || exit 1
  ok=0
  for attempt in 1 2 3; do
    if [ ! -s wgcf-account.toml ]; then
      timeout 40 wgcf register --accept-tos >register.log 2>&1 || { echo "[warp] register attempt $attempt failed: $(tail -n 2 register.log)"; sleep 3; continue; }
    fi
    if timeout 40 wgcf generate >generate.log 2>&1 && [ -s wgcf-profile.conf ]; then ok=1; break; fi
    echo "[warp] generate attempt $attempt failed: $(tail -n 2 generate.log)"
    rm -f wgcf-account.toml
    sleep 3
  done
  if [ "$ok" = "1" ]; then
    cat > wireproxy.conf <<EOF
WGConfig = $W/wgcf-profile.conf

[Socks5]
BindAddress = 127.0.0.1:40000
EOF
    wireproxy -c wireproxy.conf >wireproxy.log 2>&1 &
    export YTDLP_PROXY="socks5://127.0.0.1:40000"
    echo "[warp] proxy starting on 127.0.0.1:40000 (the app verifies it before use)"
  else
    echo "[warp] could not set up Cloudflare WARP; continuing without it"
  fi
  cd /app/backend || exit 1
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}" \
  --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 30
