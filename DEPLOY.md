# Hosting WonderPlay on Render

This guide puts WonderPlay online at an address like `https://wonderplay-xxxx.onrender.com`,
protected by your own password, installable on your phone, and updated automatically
every time you push code to GitHub.

The project already contains everything Render needs:

| File | What it does |
|---|---|
| `Dockerfile` | Builds the app (Python + Node.js for YouTube) |
| `render.yaml` | Tells Render to create the web service, database and storage in one go |
| `backend/app/migrate.py` | Creates the database tables automatically on first start |
| `backend/app/auth.py` | The password sign-in screen |

---

## Before you start

You need:

1. A **GitHub** account: https://github.com/signup
2. A **Render** account: https://dashboard.render.com/register (sign up with GitHub; that makes step 3 easier)
3. **Git** on your computer. Check by running `git --version` in a terminal. If it's missing: https://git-scm.com/download/win
4. A payment card on Render, for the recommended setup (see *Costs* at the end; a free option is described too).

> **Your local music folders don't go online.** Render's server can't see the files on your PC.
> Online, WonderPlay plays from the YouTube Music catalogue, songs you add by link, and anything
> you download to your phone. (To stream your PC's own files from anywhere instead, see
> *Alternative: host from your own PC* at the end.)

---

## Step 1: Choose your password

Pick a strong password for signing in to WonderPlay. You'll type it into Render in step 4.
Anyone with this password can use your account, so don't reuse one you use elsewhere.

---

## Step 2: Put the code on GitHub (private)

1. Go to https://github.com/new
   - **Repository name:** `wonderplay`
   - Select **Private** (important)
   - Don't tick "Add a README", ".gitignore" or "license"
   - Click **Create repository**
2. Open a terminal in the project folder (`C:\Users\vinwo\sonicvault`) and run:

```powershell
git init
git add .
git status
```

3. **Check the `git status` list before going on.** It must **not** contain `.env`,
   `backend/.venv` or `data/`. The `.gitignore` file already excludes them; this is just a
   safety check, because `.env` holds your database password.
4. Save and upload (replace `YOUR-USERNAME`):

```powershell
git commit -m "WonderPlay"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/wonderplay.git
git push -u origin main
```

If Git asks you to sign in, use the browser window it opens.

---

## Step 3: Create everything on Render with the Blueprint

1. Open https://dashboard.render.com, then **New +** → **Blueprint**.
2. Click **Connect GitHub** (if asked) and give Render access to the `wonderplay` repository.
3. Select the `wonderplay` repository. Render finds `render.yaml` and shows what it will create:
   - **wonderplay**: the web service (Docker, **Free** plan, as currently set in `render.yaml`)
   - **wonderplay-db**: a PostgreSQL database (**Free** plan)

   To use the always-on paid setup instead, see *Costs* below before applying.
4. Give the Blueprint a name (e.g. `wonderplay`).

## Step 4: Enter your password and launch

1. Render asks for the values marked "sync: false". For **APP_PASSWORD**, enter the password from step 1.
2. Click **Apply** (or **Deploy Blueprint**).

Render now creates the database, builds the app (the first build takes about 5–10 minutes)
and starts it.

## Step 5: Watch it start

1. Click the **wonderplay** service, then **Logs**.
2. On the first start you should see lines like:

```
applying 01_schema.sql
applying 02_catalog.sql
database schema is up to date
WonderPlay ready on http://0.0.0.0:10000
```

3. When the top of the page shows **Live** (green), it's running.

## Step 6: Open it and sign in

1. Click the service URL at the top (e.g. `https://wonderplay-xxxx.onrender.com`).
2. Enter your password.
3. Try it: search for a song, press play, then like it.

### Put it on your phone

- **Android (Chrome):** open the URL, then **⋮** menu → **Install app** (or **Add to Home screen**).
- **iPhone/iPad (Safari):** open the URL, tap **Share** → **Add to Home Screen**.

You then get a WonderPlay icon, a full-screen app, lock-screen controls, and offline downloads.

---

## Step 7 (only if songs won't play): YouTube "not a bot" check

YouTube is much stricter with cloud servers than with home connections. If searching works but
**playing fails** with an error like *"Sign in to confirm you're not a bot"*, give the server a
YouTube sign-in using a cookies file:

1. On your PC, open a **private/incognito** window and sign in to YouTube with a
   **spare Google account**. Don't use your main account: YouTube can restrict accounts used this way.
2. Install the browser extension **"Get cookies.txt LOCALLY"** and allow it in incognito.
3. While on youtube.com, use the extension to **Export** cookies in Netscape format. This saves a `cookies.txt` file.
4. Close the private window **without signing out** (signing out invalidates the cookies).
5. In Render: **wonderplay** service → **Environment** → **Secret Files** → **Add Secret File**
   - **Filename:** `cookies.txt`
   - **Contents:** paste the whole file
   - **Save**, then **Manual Deploy** → **Deploy latest commit**

`render.yaml` already points the app at `/etc/secrets/cookies.txt`. Without the file, the app
simply runs without cookies. Cookies expire every few weeks to months; repeat these steps when
playback starts failing again.

---

## Updating WonderPlay later

Make your changes, then:

```powershell
git add .
git commit -m "Describe the change"
git push
```

Render rebuilds and redeploys automatically (about 3–5 minutes). Your database, likes,
playlists and album art are kept.

**Keeping YouTube working:** YouTube changes often. When playback breaks, update the YouTube
libraries and redeploy:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pip install -U yt-dlp yt-dlp-ejs ytmusicapi
pip freeze | Select-String "yt-dlp|ytmusicapi"
```

Raise the matching minimum versions in `backend/requirements.txt` (e.g. `yt-dlp>=2026.12.1`),
then commit and push.

---

## Optional: bring your current library online

Your local playlists, likes and history can be copied to the Render database.

1. In Render: **wonderplay-db** → **Connect** → copy the **External Database URL**.
2. On your PC (PostgreSQL's tools are in `C:\Program Files\PostgreSQL\18\bin`):

```powershell
$pg = "C:\Program Files\PostgreSQL\18\bin"
& "$pg\pg_dump.exe" -h localhost -U sonicvault -d sonicvault --schema=music --data-only --no-owner -f wonderplay-data.sql
& "$pg\psql.exe" "PASTE-EXTERNAL-DATABASE-URL-HERE" -c "TRUNCATE music.user_preferences, music.schema_migrations;"
& "$pg\psql.exe" "PASTE-EXTERNAL-DATABASE-URL-HERE" -f wonderplay-data.sql
```

Run this **after** the first successful deploy (step 5), so the tables exist. Songs that came
from your PC's folders won't play online (the files aren't there), and their album art shows a
placeholder until the song is re-added online. Delete `wonderplay-data.sql` afterwards.

---

## Costs

Render's prices change, so check https://render.com/pricing. At the time of writing:

| Setup | What you get | Roughly |
|---|---|---|
| **Free** (current `render.yaml`) | Free web service + free database | $0 |
| **Always-on** | Starter web service + 1 GB disk + basic-256mb database | about US$7 + $0.25 + $6 per month |

**Switching to always-on.** In `render.yaml`:
- set the web service to `plan: starter`;
- un-comment the `disk:` lines (remove the `# `);
- set the database to `plan: basic-256mb`;
- commit and push.

Moving the database off the free plan is done in Render's dashboard (**wonderplay-db** → **Upgrade**), which keeps your data.

Free-tier limits:
- The app sleeps after 15 minutes without use; the next visit takes about a minute to wake it.
- Album art is re-fetched after every restart.
- Render's free databases are deleted after a limited period (30 days at the time of writing) unless upgraded.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Build fails at `pip install` | Check the build log for the package name; make sure `backend/requirements.txt` was pushed. |
| Logs show `password authentication failed` / can't connect to the database | The database is still being created. Wait a minute, then **Manual Deploy**. Make sure `DATABASE_URL` comes from **wonderplay-db** (Environment tab). |
| Page loads but says "Sign in required" in a loop | Clear the site's cookies, or open it in a private window and sign in again. |
| Search works, playing fails | See **step 7** (YouTube cookies), and update yt-dlp as described above. |
| "Wrong password" | Check **APP_PASSWORD** in the service's **Environment** tab. Changing it signs every device out. |
| Very slow first page load | On the free plan the app was asleep; it wakes in about a minute. |
| You see an old version after deploying | Close and reopen the app twice. The offline cache updates in the background. |

---

## Alternative: host from your own PC

To stream the music **files on your PC** from anywhere (instead of, or as well as, Render), you
can keep WonderPlay running at home and publish it with **Cloudflare Tunnel**
(`cloudflared` is already installed on your PC):

1. In `.env`, set `APP_PASSWORD=your-password`.
2. Start WonderPlay: `cd backend` then `uvicorn app.main:app --port 8000 --proxy-headers`
3. In another terminal: `cloudflared tunnel --url http://localhost:8000`
4. Open the `https://….trycloudflare.com` address it prints, on any device.

This quick tunnel's address changes each time it starts; a named tunnel on your own domain
keeps it fixed (see Cloudflare's "Create a tunnel" guide). Your PC must stay on, but YouTube
works best this way, because requests come from a home connection rather than a cloud server.
