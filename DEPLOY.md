# Hosting WonderPlay on Render

This guide puts WonderPlay online at an address like `https://wonderplay-xxxx.onrender.com`,
protected by your own password, installable on your phone, and updated automatically
every time you push code to GitHub.

- **Render** runs the app (free plan).
- **Neon** hosts the PostgreSQL database (free plan, which doesn't expire).

The project already contains everything needed:

| File | What it does |
|---|---|
| `Dockerfile` | Builds the app (Python + Node.js for YouTube) |
| `render.yaml` | Tells Render how to create and configure the web service |
| `backend/app/migrate.py` | Creates the database tables automatically on first start |
| `backend/app/auth.py` | The password sign-in screen |

---

## Before you start

You need:

1. A **GitHub** account: https://github.com/signup
2. A **Render** account: https://dashboard.render.com/register (sign up with GitHub; that makes step 4 easier)
3. A **Neon** account: https://neon.tech (sign up with GitHub or Google; no card needed)
4. **Git** on your computer. Check by running `git --version` in a terminal. If it's missing: https://git-scm.com/download/win

> **Your local music folders don't go online.** Render's server can't see the files on your PC.
> Online, WonderPlay plays from the YouTube Music catalogue, songs you add by link, and anything
> you download to your phone. (To stream your PC's own files from anywhere instead, see
> *Alternative: host from your own PC* at the end.)

---

## Step 1: Choose your password

Pick a strong password for signing in to WonderPlay. You'll type it into Render in step 5.
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

## Step 3: Create the database on Neon

1. Sign in at https://console.neon.tech and click **New Project**.
   - **Name:** `wonderplay`
   - **Postgres version:** the default (17 or newer is fine)
   - **Region:** pick the one closest to your Render region (e.g. *AWS Europe (Frankfurt)*
     if your Render service is in Frankfurt). Same region = faster app.
   - Click **Create project**.
2. On the project dashboard, click **Connect** (the "Connection string" panel).
3. Set **Connection pooling** to **off** (WonderPlay keeps its own connection pool).
   The host in the string should **not** contain `-pooler`. (A pooled string also works;
   WonderPlay adapts to it automatically.)
4. Click **Copy snippet** / **Copy**. It looks like:

```
postgresql://neondb_owner:AbC123...@ep-cool-sun-a1b2c3.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

Keep it somewhere safe for step 5; it contains the database password. You don't need to
create any tables: WonderPlay does that itself on first start.

## Step 4: Create the web service on Render with the Blueprint

1. Open https://dashboard.render.com, then **New +** → **Blueprint**.
2. Click **Connect GitHub** (if asked) and give Render access to the repository.
3. Select the repository. Render reads `render.yaml` and shows one resource:
   **wonderplay** (web service, Docker, Free plan).
4. Give the Blueprint a name (e.g. `wonderplay`).

> **Already tried once and got "cannot have more than one active free tier database"?**
> That came from the old `render.yaml`, which also asked Render for a database. Pull the
> latest code (it no longer does), then on your existing Blueprint page press **Manual sync**.

## Step 5: Enter your settings and launch

Render asks for the values marked "sync: false":

| Key | Value |
|---|---|
| **DATABASE_URL** | the Neon connection string from step 3 (paste it exactly, including `?sslmode=require...`) |
| **APP_PASSWORD** | the password from step 1 |

Click **Apply** (or **Deploy Blueprint**). Render builds the app (the first build takes about
5–10 minutes) and starts it.

If Render didn't ask for these values (e.g. after a Manual sync), open the **wonderplay**
service → **Environment** → add `DATABASE_URL` and `APP_PASSWORD` → **Save, rebuild and deploy**.

## Step 6: Watch it start

1. Click the **wonderplay** service, then **Logs**.
2. On the first start you should see lines like:

```
applying 01_schema.sql
applying 02_catalog.sql
database schema is up to date
WonderPlay ready on http://0.0.0.0:10000
```

3. When the top of the page shows **Live** (green), it's running.

## Step 7: Open it and sign in

1. Click the service URL at the top (e.g. `https://wonderplay-xxxx.onrender.com`).
2. Enter your password.
3. Try it: search for a song, press play, then like it.

### Put it on your phone

- **Android (Chrome):** open the URL, then **⋮** menu → **Install app** (or **Add to Home screen**).
- **iPhone/iPad (Safari):** open the URL, tap **Share** → **Add to Home Screen**.

You then get a WonderPlay icon, a full-screen app, lock-screen controls, and offline downloads.

---

## Step 8: YouTube playback (automatic)

YouTube answers requests from cloud servers like Render's with *"Sign in to confirm you're not
a bot"*. WonderPlay avoids this automatically: the Docker image starts **Cloudflare WARP**
(Cloudflare's free VPN) inside the server, and YouTube traffic leaves through Cloudflare's
network instead of Render's. Nobody has to sign in or export anything, on any device.

To check it: **Settings → YouTube playback**. It should say *Connected through Cloudflare WARP*;
press **Test**, which fetches a real song and reports the result.

**If the test still fails** (some networks are blocked even through WARP), add backup cookies
once, from a computer. They then work for everyone using your WonderPlay:

1. Open a **private/incognito** window and sign in to YouTube with a **spare Google account**.
   Don't use your main account: YouTube can restrict accounts used this way.
2. Install the browser extension **"Get cookies.txt LOCALLY"** and allow it in incognito.
3. While on youtube.com, use the extension to **Export** cookies in Netscape format (`cookies.txt`).
4. Close the private window **without signing out**.
5. In WonderPlay: **Settings → YouTube playback → Advanced** → **Choose cookies.txt** → **Save**.

Cookies are stored in your database (they survive restarts) and expire after weeks to months;
repeat when the test fails again. To turn WARP off, set `WARP_PROXY=false` in the service's
**Environment** tab.

The test also lists every connection it tried (each YouTube client, through WARP and direct).
WonderPlay remembers the first one that works and uses it for every song after that.

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

Your local playlists, likes and history can be copied to the Neon database.

1. Get the Neon connection string (Neon console → your project → **Connect**).
2. On your PC (PostgreSQL's tools are in `C:\Program Files\PostgreSQL\18\bin`):

```powershell
$pg = "C:\Program Files\PostgreSQL\18\bin"
& "$pg\pg_dump.exe" -h localhost -U sonicvault -d sonicvault --schema=music --data-only --no-owner -f wonderplay-data.sql
& "$pg\psql.exe" "PASTE-NEON-CONNECTION-STRING-HERE" -c "TRUNCATE music.user_preferences, music.schema_migrations;"
& "$pg\psql.exe" "PASTE-NEON-CONNECTION-STRING-HERE" -f wonderplay-data.sql
```

Run this **after** the first successful deploy (step 6), so the tables exist. Songs that came
from your PC's folders won't play online (the files aren't there), and their album art shows a
placeholder until the song is re-added online. Delete `wonderplay-data.sql` afterwards.

---

## Costs

Prices change, so check https://render.com/pricing and https://neon.tech/pricing. At the time of writing:

| Setup | What you get | Roughly |
|---|---|---|
| **Free** (current `render.yaml`) | Render free web service + Neon free database (0.5 GB, doesn't expire) | $0 |
| **Always-on** | Render Starter web service + 1 GB disk, Neon free database | about US$7.25 per month |

**Switching to always-on.** In `render.yaml`, set `plan: starter` and add the `disk:` lines
shown in the comment at the top of the file, then commit and push.

Free-tier limits:
- The app sleeps after 15 minutes without use; the next visit takes about a minute to wake it.
- Album art is re-fetched after every restart.
- Neon pauses an idle database after a few minutes; it wakes in about a second on the next request.
- Neon's free plan includes 0.5 GB of storage, which is plenty: WonderPlay stores only metadata there, not audio.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Build fails at `pip install` | Check the build log for the package name; make sure `backend/requirements.txt` was pushed. |
| Logs show `password authentication failed` / can't connect to the database | Check **DATABASE_URL** in the service's **Environment** tab: it must be the full Neon connection string. If you reset the password in Neon, copy the new string. |
| Render says "cannot have more than one active free tier database" | You're syncing an old `render.yaml`. Make sure the latest commit is pushed (it has no `databases:` section), then **Manual sync**. |
| Page loads but says "Sign in required" in a loop | Clear the site's cookies, or open it in a private window and sign in again. |
| Search works, playing fails | See **step 8** (YouTube cookies), and update yt-dlp as described above. |
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
