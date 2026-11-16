// Search: your library + the whole YouTube Music catalogue in one place.
import { api } from '../api.js';
import {
  albumCard, artistCard, artistLink, artUrl, catalogAlbumCard, catalogArtistCard, catalogPlaylistCard,
  emptyState, esc, html, markPlaying, mount, registerList, shelf, thumbUrl, trackTable, videoCard,
} from '../ui.js';
import { hueOf } from './home.js';

const LOCAL_TILES = [
  { href: '#/liked', label: 'Liked Songs', icon: 'heart-fill', h: 262 },
  { href: '#/downloads', label: 'Downloads', icon: 'arrow-down-circle-fill', h: 150 },
  { href: '#/videos', label: 'Music Videos', icon: 'play-btn-fill', h: 340 },
  { href: '#/library/albums', label: 'Your albums', icon: 'disc-fill', h: 215 },
];

const isUrl = (s) => /^https?:\/\//i.test(s);

function topCard(top) {
  if (top.type === 'artist') {
    return html`<a class="top-result" href="#/catalog/artist/${top.browse_id}?name=${encodeURIComponent(top.name)}">
      <img class="round" src="${thumbUrl(top.thumbnail)}" alt="">
      <div><div class="tt">${top.name}</div><div class="muted mt-1">Artist${top.subscribers ? ` • ${top.subscribers} subscribers` : ''}</div></div>
      <button class="fab-play" type="button" data-action="play-catalog-artist" data-id="${top.browse_id}" data-name="${top.name}"
              aria-label="Play ${top.name}"><i class="bi bi-play-fill"></i></button></a>`;
  }
  if (top.type === 'album') {
    return html`<a class="top-result" href="#/catalog/album/${top.browse_id}">
      <img src="${thumbUrl(top.thumbnail)}" alt="">
      <div><div class="tt">${top.title}</div><div class="muted mt-1">${top.type === 'album' ? 'Album' : top.type} • ${top.artist_name || ''}</div></div>
      <button class="fab-play" type="button" data-action="play-catalog-album" data-id="${top.browse_id}" aria-label="Play ${top.title}">
        <i class="bi bi-play-fill"></i></button></a>`;
  }
  const list = registerList([top], { type: 'search' });
  return html`<div class="top-result" data-action="play-list" data-list="${list}" data-index="0">
    <img src="${artUrl(top)}" alt="">
    <div><div class="tt">${top.title}</div><div class="muted mt-1">${top.video_type === 'video' ? 'Video' : 'Song'} • ${artistLink(top)}</div></div>
    <button class="fab-play" type="button" data-action="play-list" data-list="${list}" data-index="0" aria-label="Play ${top.title}">
      <i class="bi bi-play-fill"></i></button></div>`;
}

function catalogResults(r, local) {
  const songs = r.songs || [];
  const videosList = registerList(r.videos, { type: 'search' });
  const top = r.top || (songs[0] ? { type: 'track', ...songs[0] } : null);
  const songTable = songs.length
    ? trackTable(songs.slice(0, top ? 5 : 8), { context: { type: 'search' }, showAlbum: false, compact: true }) : '';
  return html`
    ${top || songs.length ? html`<div class="search-grid section">
      ${top ? html`<section><h2 class="section-title mb-3">Top result</h2>${topCard(top)}</section>` : ''}
      ${songs.length ? html`<section><h2 class="section-title mb-3">Songs</h2>${songTable}</section>` : ''}
    </div>` : ''}
    ${shelf('Artists', r.artists.map(catalogArtistCard))}
    ${shelf('Albums', r.albums.map(catalogAlbumCard))}
    ${shelf('Music videos', r.videos.map((t, i) => videoCard(t, videosList, i)))}
    ${shelf('Playlists', r.playlists.map(catalogPlaylistCard))}
    ${songs.length > 5 ? html`<section class="section"><h2 class="section-title mb-3">More songs</h2>
      ${trackTable(songs.slice(5), { context: { type: 'search' } })}</section>` : ''}
    ${!top && !songs.length && !r.artists.length && !r.albums.length && !local
      ? emptyState({ icon: 'search', title: 'No results', text: 'Check the spelling or try different words.' }) : ''}`;
}

function libraryResults(r) {
  const any = r.tracks.length || r.albums.length || r.artists.length;
  if (!any) return '';
  return html`
    <section class="section">
      <div class="section-head"><h2 class="section-title">In your library</h2></div>
      ${r.tracks.length ? trackTable(r.tracks.slice(0, 4), { context: { type: 'search' }, showAlbum: false }) : ''}
    </section>
    ${shelf('Your artists', r.artists.map(artistCard))}
    ${shelf('Your albums', r.albums.map(albumCard))}`;
}

async function browse(signal) {
  let moods = [];
  try { moods = await api.get('/api/catalog/moods', null, { signal }); } catch { /* offline: local tiles only */ }
  return html`
    <h1 class="page-title">Browse all</h1>
    <div class="browse-grid mb-4">${LOCAL_TILES.map((b) => html`
      <a class="browse-tile" href="${b.href}" style="--h:${b.h}">${b.label}<i class="bi bi-${b.icon}"></i></a>`)}</div>
    ${moods.map((sec) => html`
      <section class="section">
        <div class="section-head"><h2 class="section-title">${sec.title}</h2></div>
        <div class="browse-grid">${sec.items.map((m) => html`
          <a class="browse-tile" href="#/mood?p=${encodeURIComponent(m.params)}&t=${encodeURIComponent(m.title)}" style="--h:${hueOf(m.title)}">
            ${m.title}<i class="bi bi-music-note-beamed"></i></a>`)}</div>
      </section>`)}`;
}

export async function searchView({ params, signal }) {
  const q = (params[0] || '').trim();
  const input = document.getElementById('search-input');
  if (document.activeElement !== input) input.value = q;
  document.getElementById('search-clear').hidden = !input.value;

  if (!q) return { title: 'Search', html: await browse(signal) };

  if (isUrl(q)) {
    return {
      title: 'Search',
      html: html`<div class="panel d-flex align-items-center gap-3 flex-wrap mt-3">
        <i class="bi bi-link-45deg" style="font-size:28px;color:var(--accent)"></i>
        <div class="flex-grow-1" style="overflow-wrap:anywhere">${q}</div>
        <a class="pill-btn pill-btn-primary" href="#/import?q=${encodeURIComponent(q)}"><i class="bi bi-plus-lg"></i> Import this link</a></div>`,
    };
  }

  // Library results are instant; catalogue results stream in below them.
  const local = await api.get('/api/search', { q, limit: 12 }, { signal }).catch(() => ({ tracks: [], albums: [], artists: [] }));
  const hasLocal = local.tracks.length || local.albums.length || local.artists.length;
  return {
    title: `Search: ${q}`,
    keepScroll: true,
    html: html`
      <div id="catalog-results">
        <div class="d-flex align-items-center gap-2 muted mb-3"><span class="spinner-border spinner-border-sm"></span> Searching all music…</div>
        <div class="search-grid section">
          <div class="skel" style="height:230px;border-radius:22px"></div>
          <div>${Array.from({ length: 5 }, () => html`<div class="skel skel-line" style="height:46px"></div>`)}</div>
        </div>
      </div>
      <div id="library-results">${libraryResults(local)}</div>`,
    mount(root, ctx) {
      const box = root.querySelector('#catalog-results');
      api.get('/api/catalog/search', { q }, { signal: ctx.signal })
        .then((r) => {
          if (!ctx.isCurrent()) return;
          mount(box, catalogResults(r, hasLocal));
          markPlaying(box);
        })
        .catch((err) => {
          if (err.name === 'AbortError' || !ctx.isCurrent()) return;
          mount(box, hasLocal ? html`<p class="muted small mb-4"><i class="bi bi-wifi-off"></i> ${esc(err.message)}</p>`
            : emptyState({ icon: 'wifi-off', title: navigator.onLine ? "Couldn't reach YouTube Music" : "You're offline",
              text: navigator.onLine ? err.message : 'Search covers your downloads and library while offline.' }));
        });
    },
  };
}

// Live search from the top bar: debounce, and replace history entries while
// typing so Back doesn't step through every keystroke.
export function initSearchBox(navigate) {
  const form = document.getElementById('search-form');
  const input = document.getElementById('search-input');
  const clear = document.getElementById('search-clear');
  let timer = null;

  const go = (immediate) => {
    clearTimeout(timer);
    const run = () => {
      const q = input.value.trim();
      const onSearch = location.hash.startsWith('#/search');
      navigate(`/search${q ? `/${encodeURIComponent(q)}` : ''}`, { replace: onSearch });
    };
    if (immediate) run(); else timer = setTimeout(run, 450);   // catalogue search is not free: wait for a pause
  };

  input.addEventListener('input', () => { clear.hidden = !input.value; go(false); });
  input.addEventListener('focus', () => { if (!location.hash.startsWith('#/search')) go(true); });
  form.addEventListener('submit', (e) => { e.preventDefault(); go(true); input.blur(); });
  clear.addEventListener('click', () => { input.value = ''; clear.hidden = true; go(true); input.focus(); });
  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
    if ((e.key === '/' && !typing) || (e.key === 'k' && (e.ctrlKey || e.metaKey))) {
      e.preventDefault();
      input.focus();
      input.select();
    }
  });
}

