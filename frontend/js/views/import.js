// Add from YouTube: search or paste a link, then import as Song and/or Video.
import { api, coverUrl } from '../api.js';
import { navigate } from '../router.js';
import { emptyState, esc, fmtTime, html, mount, toast } from '../ui.js';

const KIND_OPTIONS = [
  ['both', 'Song + Video', ['audio', 'video']],
  ['audio', 'Song only', ['audio']],
  ['video', 'Video only', ['video']],
];
const isUrl = (s) => /^https?:\/\//i.test(s) || /^[\w-]{11}$/.test(s);

function resultCard(r) {
  const owned = r.in_library_track_id;
  return html`
    <div class="card-tile" style="cursor:default">
      <div class="card-art wide">
        <img loading="lazy" src="${r.thumbnail || 'img/placeholder.svg'}" alt="" style="object-fit:cover">
        ${r.duration_ms ? html`<span class="card-badge right">${fmtTime(r.duration_ms)}</span>` : ''}
      </div>
      <span class="card-title" title="${r.title}">${r.title}</span>
      <div class="card-sub">${r.channel || ''}</div>
      <div class="mt-2">
        ${owned
          ? html`<span class="pill-btn" style="cursor:default"><i class="bi bi-check2-circle"></i> In your library</span>`
          : html`<button class="pill-btn pill-btn-primary" type="button" data-import="${r.url}"><i class="bi bi-plus-lg"></i> Add</button>`}
      </div>
    </div>`;
}

export async function importView({ query, signal }) {
  const q = (query.get('q') || '').trim();
  const targetId = query.get('track');
  const target = targetId ? await api.get(`/api/tracks/${targetId}`, null, { signal }).catch(() => null) : null;
  const kindKey = target ? 'video' : (localStorage.getItem('wp.importKinds') || 'both');

  return {
    title: 'Add from YouTube',
    ambient: '#7f1d1d',
    html: html`
      <h1 class="page-title"><i class="bi bi-youtube" style="color:#ff3b5c"></i> Add from YouTube</h1>
      ${target ? html`<div class="banner">
          <img src="${coverUrl(target.cover_path)}" alt="">
          <div class="flex-grow-1 min-w-0"><div class="small muted">Adding a music video to</div>
            <strong>${target.title}</strong> <span class="muted">• ${target.artist_name || ''}</span></div>
          <a class="icon-btn" href="#/import" aria-label="Cancel"><i class="bi bi-x-lg"></i></a></div>` : ''}
      <form class="d-flex gap-2 flex-wrap mb-3" id="yt-form">
        <input class="form-control wp-input" style="flex:1 1 240px;width:auto;height:44px" id="yt-q" value="${q}"
               placeholder="Search YouTube or paste a link…" autocomplete="off" aria-label="Search YouTube or paste a link">
        <button class="pill-btn pill-btn-primary" style="height:44px" type="submit"><i class="bi bi-search"></i> Search</button>
      </form>
      ${target ? '' : html`<div class="chips" role="radiogroup" aria-label="What to add">
        ${KIND_OPTIONS.map(([k, label]) => html`<button type="button" class="chip ${k === kindKey ? 'active' : ''}" role="radio"
           aria-checked="${k === kindKey ? 'true' : 'false'}" data-kind="${k}">${label}</button>`)}
        <span class="muted small align-self-center ms-1">Song = ad-free audio. Video = the HD music video. Switch between them while playing.</span>
      </div>`}
      <div id="yt-results">${q ? '' : emptyState({ icon: 'youtube', title: 'Find any song or music video',
        text: 'Search by artist and title, or paste a YouTube link. Your import keeps playing ad-free, and you can save it for offline listening.' })}</div>
    `,
    mount(root, ctx) {
      let kinds = target ? ['video'] : KIND_OPTIONS.find(([k]) => k === kindKey)[2];
      const results = root.querySelector('#yt-results');

      root.querySelector('#yt-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const v = root.querySelector('#yt-q').value.trim();
        if (!v) return;
        navigate(`/import?q=${encodeURIComponent(v)}${targetId ? `&track=${targetId}` : ''}`);
      });

      root.querySelectorAll('[data-kind]').forEach((chip) => chip.addEventListener('click', () => {
        root.querySelectorAll('[data-kind]').forEach((c) => {
          c.classList.toggle('active', c === chip);
          c.setAttribute('aria-checked', c === chip ? 'true' : 'false');
        });
        kinds = KIND_OPTIONS.find(([k]) => k === chip.dataset.kind)[2];
        try { localStorage.setItem('wp.importKinds', chip.dataset.kind); } catch { /* private mode */ }
      }));

      results.addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-import]');
        if (!btn) return;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Adding…';
        try {
          const r = await api.post('/api/remote/import', { url: btn.dataset.import, kinds, track_id: target?.id ?? null });
          const t = r.track;
          btn.outerHTML = `<a class="pill-btn" href="#/album/${t.album_id}"><i class="bi bi-check2-circle"></i> Added • Open</a>`;
          toast(`Added "${t.title}"${r.lyrics_found ? ' with synced lyrics' : ''}`);
          if (target) navigate(`/album/${t.album_id}`);
        } catch (err) {
          btn.disabled = false;
          btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Retry';
          toast(err.message, 'error', 6000);
        }
      });

      if (!q) return;
      if (isUrl(q)) {
        mount(results, html`<div class="panel d-flex align-items-center gap-3 flex-wrap">
          <i class="bi bi-link-45deg" style="font-size:28px;color:var(--accent)"></i>
          <div class="flex-grow-1" style="overflow-wrap:anywhere">${q}</div>
          <button class="pill-btn pill-btn-primary" type="button" data-import="${q}"><i class="bi bi-plus-lg"></i> Add to library</button></div>`);
        return;
      }
      mount(results, html`<div class="d-flex align-items-center gap-2 muted mb-3"><span class="spinner-border spinner-border-sm"></span>
        Searching YouTube…</div><div class="grid-cards" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr))">
        ${Array.from({ length: 8 }, () => html`<div><div class="skel" style="aspect-ratio:16/9"></div><div class="skel skel-line"></div></div>`)}</div>`);
      api.get('/api/remote/search', { q, limit: 20 }, { signal: ctx.signal })
        .then((r) => {
          if (!ctx.isCurrent()) return;
          mount(results, r.results.length
            ? html`<div class="grid-cards" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr))">${r.results.map(resultCard)}</div>`
            : emptyState({ icon: 'search', title: `No YouTube results for “${q}”` }));
        })
        .catch((err) => {
          if (err.name === 'AbortError' || !ctx.isCurrent()) return;
          mount(results, emptyState({ icon: 'wifi-off', title: 'YouTube search failed', text: esc(err.message) }));
        });
    },
  };
}
