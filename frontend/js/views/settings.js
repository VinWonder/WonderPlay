// Settings: library folders + scanning, stats, playback preferences.
import { api } from '../api.js';
import { render } from '../router.js';
import { store } from '../store.js';
import { fmtLong, html, mount, toast } from '../ui.js';

let pollTimer = null;

function folderRow(f) {
  const status = f.status === 'error' ? 'error' : f.status;
  const last = f.last_scan_finished_at ? new Date(f.last_scan_finished_at).toLocaleString() : 'never';
  return html`
    <div class="folder-row">
      <span class="status-dot ${status}" title="${f.status}"></span>
      <div class="flex-grow-1 min-w-0">
        <div class="path">${f.path}</div>
        <div class="small ${f.status === 'error' ? 'text-danger' : 'muted'}">
          ${f.status === 'error' ? f.last_error : html`${f.last_scan_file_count ?? 0} files • last scan ${last}`}</div>
      </div>
      <div class="form-check form-switch m-0" title="Include in scans">
        <input class="form-check-input" type="checkbox" role="switch" data-folder-toggle="${f.id}" ${f.is_enabled ? 'checked' : ''}
               aria-label="Include ${f.path} in scans">
      </div>
      <button class="icon-btn" type="button" data-folder-remove="${f.id}" aria-label="Remove folder ${f.path}" title="Remove folder">
        <i class="bi bi-trash3"></i></button>
    </div>`;
}

function scanStatus(s) {
  if (!s.running && !s.finished_at) return html`<span class="muted small">No scan has run since the server started.</span>`;
  const pct = s.total ? Math.round((s.processed / s.total) * 100) : 0;
  return html`
    ${s.running ? html`<div class="wp-progress mb-2"><span style="width:${pct}%"></span></div>` : ''}
    <div class="small muted">
      ${s.running ? html`<strong class="text-light">Scanning ${s.processed}/${s.total}</strong> • ` : html`<strong class="text-light">Last scan finished</strong> • `}
      ${s.added} added • ${s.updated} updated • ${s.unchanged} unchanged • ${s.missing} missing
      ${s.errors ? html` • <span class="text-danger">${s.errors} errors</span>` : ''}
    </div>
    ${s.running && s.current_file ? html`<div class="small muted text-truncate mt-1">${s.current_file}</div>` : ''}
    ${!s.running && s.error_samples?.length ? html`<details class="small mt-2"><summary class="text-danger">Show errors</summary>
      <ul class="mt-2">${s.error_samples.map((e) => html`<li class="muted">${e}</li>`)}</ul></details>` : ''}`;
}

const switchRow = (key, label, hint, value) => html`
  <div class="setting-row">
    <div><div class="label">${label}</div><div class="hint">${hint}</div></div>
    <div class="form-check form-switch m-0"><input class="form-check-input" type="checkbox" role="switch" data-pref="${key}"
      ${value ? 'checked' : ''} aria-label="${label}"></div>
  </div>`;

const selectRow = (key, label, hint, value, options) => html`
  <div class="setting-row">
    <div><div class="label">${label}</div><div class="hint">${hint}</div></div>
    <select class="form-select wp-select" data-pref="${key}" aria-label="${label}">
      ${options.map(([v, l]) => html`<option value="${v}" ${String(v) === String(value) ? 'selected' : ''}>${l}</option>`)}
    </select>
  </div>`;

const extraSwitch = (key, label, hint, value) => html`
  <div class="setting-row">
    <div><div class="label">${label}</div><div class="hint">${hint}</div></div>
    <div class="form-check form-switch m-0"><input class="form-check-input" type="checkbox" role="switch" data-extra="${key}"
      ${value ? 'checked' : ''} aria-label="${label}"></div>
  </div>`;

const extraSelect = (key, label, hint, value, options) => html`
  <div class="setting-row">
    <div><div class="label">${label}</div><div class="hint">${hint}</div></div>
    <select class="form-select wp-select" data-extra="${key}" aria-label="${label}">
      ${options.map(([v, l]) => html`<option value="${v}" ${v === value ? 'selected' : ''}>${l}</option>`)}
    </select>
  </div>`;

function youtubePanel(yt) {
  const test = yt.last_test;
  const net = yt.network || {};
  const network = net.mode === 'warp'
    ? html`<span class="yt-status ok"><i class="bi bi-shield-check"></i> Connected through Cloudflare WARP${net.colo ? ` (${net.colo})` : ''}: no sign-in needed</span>`
    : html`<span class="yt-status"><i class="bi bi-globe"></i> Direct connection${net.error ? ' (WARP unavailable)' : ''}</span>`;
  const cookies = yt.active
    ? html`<span class="yt-status ok"><i class="bi bi-check-circle-fill"></i> Backup cookies saved${yt.updated_at ? ` · ${new Date(yt.updated_at).toLocaleDateString()}` : ''}</span>`
    : '';
  return html`
    <section class="panel" id="yt-panel">
      <h3>YouTube playback</h3>
      <div class="sub">Songs stream from YouTube. Press <b>Test</b> to check that playback works from this server.</div>
      <div class="mb-2">${network}</div>
      ${cookies ? html`<div class="mb-2">${cookies}</div>` : ''}
      ${test ? html`<div class="small mb-2 ${test.ok ? 'text-success' : 'text-danger'}">Last test: ${test.message}</div>` : ''}
      ${test && test.attempts && test.attempts.length > 1 ? html`
        <details class="yt-steps mb-3" ${test.ok ? '' : 'open'}>
          <summary>Connections tried (${test.attempts.length})</summary>
          ${test.diagnosis ? html`<div class="small muted mb-2">Details (${test.diagnosis.route}, ${test.diagnosis.format_count} formats):</div>
            <pre class="small muted" style="white-space:pre-wrap;word-break:break-word;max-height:320px;overflow:auto">${
              [...test.diagnosis.formats, '', ...test.diagnosis.log].join('\n')}</pre>` : ''}
          <ul class="small muted mb-0">${test.attempts.map((a) => html`
            <li><b class="${a.ok ? 'text-success' : 'text-danger'}">${a.ok ? '✓' : '✗'}</b> ${a.route}${a.error ? html`: ${a.error}` : ''}
              ${a.warnings && a.warnings.length ? html`<ul>${a.warnings.map((w) => html`<li>${w}</li>`)}</ul>` : ''}</li>`)}</ul>
        </details>` : ''}
      <button class="pill-btn mb-3" type="button" id="yt-test-top"><i class="bi bi-play-circle"></i> Test</button>
      <details class="yt-steps mb-3" ${test && !test.ok ? 'open' : ''}>
        <summary>Advanced: backup cookies (only if the test keeps failing)</summary>
        <p class="small muted">Some hosting networks are blocked by YouTube even through WARP. Cookies from a signed-in
          YouTube session fix that. You only need to do this once for everyone using this WonderPlay.</p>
        <ol>
          <li>Open a <b>private / incognito</b> window and sign in to <b>youtube.com</b>. Use a <b>spare Google account</b>, not your main one.</li>
          <li>Install the browser extension <b>Get cookies.txt LOCALLY</b> and allow it in private windows.</li>
          <li>While on youtube.com, open the extension and click <b>Export</b> (Netscape format). You get a <code>cookies.txt</code> file.</li>
          <li>Close the private window <b>without signing out</b>.</li>
          <li>Choose that file below (or paste its contents), then press <b>Save</b> and <b>Test</b>.</li>
        </ol>
        <p class="small muted mb-0">Cookies expire after a few weeks or months; when songs stop playing, repeat these steps.</p>
      </details>
      <textarea class="form-control wp-input mb-2" id="yt-cookies" rows="4" spellcheck="false" autocomplete="off"
        placeholder="# Netscape HTTP Cookie File&#10;.youtube.com&#9;TRUE&#9;/&#9;TRUE&#9;…" aria-label="YouTube cookies.txt contents"></textarea>
      <div class="d-flex gap-2 flex-wrap">
        <label class="pill-btn mb-0" role="button"><i class="bi bi-file-earmark-arrow-up"></i> Choose cookies.txt
          <input type="file" id="yt-file" accept=".txt,text/plain" hidden></label>
        <button class="pill-btn pill-btn-primary" type="button" id="yt-save"><i class="bi bi-check2"></i> Save</button>
        <button class="pill-btn" type="button" id="yt-test"><i class="bi bi-play-circle"></i> Test</button>
        ${yt.active ? html`<button class="pill-btn" type="button" id="yt-remove"><i class="bi bi-trash3"></i> Remove</button>` : ''}
      </div>
    </section>`;
}

function wireYoutubePanel(root) {
  const panel = root.querySelector('#yt-panel');
  if (!panel) return;
  const box = panel.querySelector('#yt-cookies');
  panel.querySelector('#yt-file').addEventListener('change', async (e) => {
    const f = e.target.files?.[0];
    if (f) box.value = await f.text();
  });
  panel.querySelector('#yt-save').addEventListener('click', async (e) => {
    if (!box.value.trim()) { toast('Choose your cookies.txt file or paste its contents first'); return; }
    e.currentTarget.disabled = true;
    try {
      await api.put('/api/settings/youtube', { cookies: box.value });
      box.value = '';
      toast('Cookies saved. Testing YouTube…');
      await runTest(panel);
    } catch (err) {
      toast(err.message, 'error', 7000);
    }
    e.currentTarget.disabled = false;
  });
  panel.querySelector('#yt-test').addEventListener('click', () => runTest(panel));
  panel.querySelector('#yt-test-top').addEventListener('click', () => runTest(panel));
  panel.querySelector('#yt-remove')?.addEventListener('click', async () => {
    if (!confirm('Remove the saved YouTube cookies?')) return;
    await api.del('/api/settings/youtube');
    toast('Cookies removed');
    render();
  });
}

async function runTest(panel) {
  const btn = panel.querySelector('#yt-test-top');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Testing…';
  try {
    const r = await api.post('/api/settings/youtube/test');
    toast(r.message, r.ok ? 'info' : 'error', 7000);
  } catch (err) {
    toast(err.message, 'error');
  }
  render();
}

export async function settingsView({ signal }) {
  const [folders, scan, stats, prefs] = await Promise.all([
    api.get('/api/library/folders', null, { signal }),
    api.get('/api/library/scan', null, { signal }),
    api.get('/api/library/stats', null, { signal }),
    api.get('/api/preferences', null, { signal }),
  ]);
  const yt = await api.get('/api/settings/youtube', null, { signal }).catch(() => null);
  const extra = prefs.extra || {};

  const statItems = [
    ['Songs', stats.tracks], ['Albums', stats.albums], ['Artists', stats.artists],
    ['Music videos', stats.tracks_with_video], ['Playlists', stats.playlists], ['Listening time', fmtLong(stats.total_duration_ms)],
  ];

  return {
    title: 'Settings',
    html: html`
      <h1 class="page-title">Settings</h1>

      ${yt ? youtubePanel(yt) : ''}

      <section class="panel">
        <h3>Library</h3>
        <div class="stat-grid mt-3">${statItems.map(([k, v]) => html`<div class="stat"><div class="v">${typeof v === 'number' ? v.toLocaleString() : v}</div><div class="k">${k}</div></div>`)}</div>
      </section>

      <section class="panel">
        <div class="d-flex justify-content-between align-items-start gap-3 flex-wrap">
          <div><h3>Music folders</h3><div class="sub">Folders on this computer that WonderPlay scans for MP3, FLAC, M4A, OGG, OPUS, WAV, MP4 and WebM files.</div></div>
          <div class="d-flex gap-2">
            <button class="pill-btn pill-btn-primary" type="button" id="scan-btn" ${scan.running ? 'disabled' : ''}><i class="bi bi-arrow-repeat"></i> Scan now</button>
            <button class="pill-btn" type="button" id="full-scan-btn" ${scan.running ? 'disabled' : ''} title="Re-read tags of every file">Full rescan</button>
          </div>
        </div>
        <div id="scan-status" class="mb-2">${scanStatus(scan)}</div>
        <div id="folders">${folders.length ? folders.map(folderRow) : html`<p class="muted my-3">No folders yet.</p>`}</div>
        <form class="d-flex gap-2 mt-3 flex-wrap" id="folder-form">
          <input class="form-control wp-input" style="flex:1 1 240px;width:auto" id="folder-path" placeholder="C:\\Users\\you\\Music" required
                 aria-label="Folder path on the server">
          <button class="pill-btn" type="submit"><i class="bi bi-folder-plus"></i> Add folder</button>
        </form>
      </section>

      <section class="panel" id="prefs">
        <h3>Playback</h3>
        <div class="sub">Saved to your profile and used on every device.</div>
        ${extraSwitch('autoplay', 'Autoplay', 'When your music ends, keep listening to similar songs', extra.autoplay !== false)}
        ${selectRow('preferred_mode', 'Default mode', 'What plays when a song has both audio and a music video', prefs.preferred_mode,
          [['audio', 'Song (audio only)'], ['video', 'Video']])}
        ${selectRow('video_max_height', 'Video quality', 'Highest resolution to stream for music videos', prefs.video_max_height,
          [[360, '360p'], [480, '480p'], [720, '720p HD'], [1080, '1080p Full HD'], [1440, '1440p'], [2160, '2160p 4K']])}
        ${switchRow('normalize_volume', 'Normalize volume', 'Use ReplayGain so every song plays at a similar loudness', prefs.normalize_volume)}
      </section>

      <section class="panel">
        <h3>Discover</h3>
        <div class="sub">Recommendations and charts on Home come from YouTube Music.</div>
        ${extraSelect('country', 'Charts region', 'Which country\'s trending music to show', extra.country || 'ZZ', [['ZZ', 'Global'], ['KE', 'Kenya'], ['NG', 'Nigeria'], ['ZA', 'South Africa'], ['TZ', 'Tanzania'], ['UG', 'Uganda'], ['GH', 'Ghana'], ['US', 'United States'], ['GB', 'United Kingdom'], ['CA', 'Canada'], ['IN', 'India'], ['BR', 'Brazil'], ['MX', 'Mexico'], ['FR', 'France'], ['DE', 'Germany'], ['JP', 'Japan'], ['KR', 'South Korea'], ['AU', 'Australia']])}
      </section>

      <section class="panel">
        <h3>App</h3>
        <div class="sub">Install WonderPlay, manage offline music, or import a single link.</div>
        <div class="setting-row"><div><div class="label">Install app</div><div class="hint">Home-screen icon, own window, lock-screen controls</div></div>
          <button class="pill-btn" type="button" data-action="install-app"><i class="bi bi-download"></i> Install</button></div>
        <div class="setting-row"><div><div class="label">Downloads</div><div class="hint">Music stored on this device for offline listening</div></div>
          <a class="pill-btn" href="#/downloads"><i class="bi bi-arrow-down-circle"></i> Manage</a></div>
        <div class="setting-row auth-only"><div><div class="label">Sign out</div><div class="hint">Sign out of WonderPlay on this device</div></div>
          <button class="pill-btn" type="button" data-action="logout"><i class="bi bi-box-arrow-right"></i> Sign out</button></div>
        <div class="setting-row"><div><div class="label">Import from a link</div><div class="hint">Paste a YouTube, SoundCloud or other media URL</div></div>
          <a class="pill-btn" href="#/import"><i class="bi bi-link-45deg"></i> Import</a></div>
      </section>

      <section class="panel">
        <h3>Look &amp; feel</h3>
        <div class="sub">How WonderPlay looks while you listen.</div>
        ${switchRow('dynamic_theme', 'Dynamic colours', 'Tint the app with the colours of the album art', prefs.dynamic_theme)}
        ${selectRow('visualizer_style', 'Visualizer', 'Real-time animation driven by the music', prefs.visualizer_style,
          [['off', 'Off'], ['bars', 'Bars'], ['wave', 'Waveform'], ['circle', 'Circular']])}
        ${switchRow('show_synced_lyrics', 'Synced lyrics', 'Show time-synced lyrics when available', prefs.show_synced_lyrics)}
      </section>
    `,
    mount(root, ctx) {
      wireYoutubePanel(root);
      const statusEl = root.querySelector('#scan-status');
      const buttons = [root.querySelector('#scan-btn'), root.querySelector('#full-scan-btn')];

      const poll = async () => {
        clearTimeout(pollTimer);
        if (!ctx.isCurrent()) return;
        const s = await api.get('/api/library/scan').catch(() => null);
        if (!s || !ctx.isCurrent()) return;
        mount(statusEl, scanStatus(s));
        buttons.forEach((b) => { b.disabled = s.running; });
        if (s.running) pollTimer = setTimeout(poll, 800);
        else if (poll.wasRunning) {
          toast(`Scan finished: ${s.added} added, ${s.updated} updated`);
          store.refreshPlaylists();
          render();
        }
        poll.wasRunning = s.running;
      };
      if (scan.running) { poll.wasRunning = true; poll(); }

      const startScan = async (full) => {
        await api.post('/api/library/scan', { full });
        poll.wasRunning = true;
        poll();
      };
      buttons[0].addEventListener('click', () => startScan(false).catch((e) => toast(e.message, 'error')));
      buttons[1].addEventListener('click', () => startScan(true).catch((e) => toast(e.message, 'error')));

      root.querySelector('#folder-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const input = root.querySelector('#folder-path');
        try {
          const f = await api.post('/api/library/folders', { path: input.value.trim() });
          toast('Folder added, scanning…');
          await api.post('/api/library/scan', { folder_ids: [f.id] });
          render();
        } catch (err) {
          toast(err.message, 'error');
        }
      });

      root.querySelector('#folders').addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-folder-remove]');
        if (!btn) return;
        if (!confirm('Remove this folder? Its songs disappear from your library (your playlists and history are kept).')) return;
        try {
          await api.del(`/api/library/folders/${btn.dataset.folderRemove}`);
          toast('Folder removed');
          render();
        } catch (err) { toast(err.message, 'error'); }
      });
      root.querySelector('#folders').addEventListener('change', async (e) => {
        const sw = e.target.closest('[data-folder-toggle]');
        if (!sw) return;
        try { await api.patch(`/api/library/folders/${sw.dataset.folderToggle}`, { is_enabled: sw.checked }); }
        catch (err) { sw.checked = !sw.checked; toast(err.message, 'error'); }
      });

      root.addEventListener('change', async (e) => {
        const ex = e.target.closest('[data-extra]');
        if (ex) {
          const value = ex.type === 'checkbox' ? ex.checked : ex.value;
          try {
            store.prefs = await api.patch('/api/preferences', { extra: { ...(store.prefs?.extra || {}), [ex.dataset.extra]: value } });
            store.emit('prefs', store.prefs);
            toast('Saved');
          } catch (err) { toast(err.message, 'error'); }
          return;
        }
        const el = e.target.closest('[data-pref]');
        if (!el) return;
        const key = el.dataset.pref;
        let value = el.type === 'checkbox' ? el.checked : el.value;
        if (['video_max_height'].includes(key)) value = Number(value);
        try {
          store.prefs = await api.patch('/api/preferences', { [key]: value });
          store.emit('prefs', store.prefs);
          toast('Saved');
        } catch (err) { toast(err.message, 'error'); }
      });
    },
  };
}
