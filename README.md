# WonderPlay

A personal, ad-free music and music-video player: a Progressive Web App with a FastAPI streaming backend.

- **Play anything.** One search covers your own library and the YouTube Music catalogue: songs, artists, albums, videos and playlists.
- **Song and Video mode.** Switch between audio and the official music video without the sound stopping (the video stays synced to the audio).
- **Spotify-style discovery.** Personal recommendations, song radio and Autoplay, charts, moods and genres.
- **Synced lyrics**, a real-time Web Audio visualizer, and lock-screen and Bluetooth controls (Media Session API).
- **Offline.** An installable PWA: download songs, albums or playlists and play them (with seeking) without a connection.
- **Your files too.** Scans local MP3, FLAC, M4A, OGG/Opus, WAV and MP4 folders, including tags, cover art and `.lrc` lyrics.

**Stack:** Vanilla JS (ES modules), Bootstrap 5, a service worker with IndexedDB · Python 3.13 with FastAPI, yt-dlp, ytmusicapi and mutagen · PostgreSQL.

## Run locally

```powershell
# 1. Database (once, as the postgres superuser)
psql -U postgres -v app_password='choose-one' -f db/00_bootstrap.sql
psql -U sonicvault -d sonicvault -f db/01_schema.sql
psql -U sonicvault -d sonicvault -f db/02_catalog.sql

# 2. Config
copy .env.example .env      # then edit DATABASE_URL and MEDIA_ROOTS

# 3. Backend + app
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000 (API docs at `/docs`). Node.js must be installed: yt-dlp uses it for YouTube.

Run the backend tests with `pytest` (from `backend/`).

## Host it

See **[DEPLOY.md](DEPLOY.md)** for step-by-step Render hosting. It covers the Docker image, the `render.yaml` blueprint, the password sign-in, and putting the app on your phone.

## Project layout

```
backend/app/        FastAPI app: routers (stream, library, catalog, playlists, me, auth)
backend/app/services/  range streaming, remote proxy, scanner, yt-dlp resolver, YouTube Music, lyrics
db/                 PostgreSQL schema + migrations (applied automatically when AUTO_MIGRATE=true)
frontend/           the PWA: index.html, css/, js/ (player/, offline/, views/), sw.js, manifest
```

For personal use. Respect the terms of the services you stream from.
