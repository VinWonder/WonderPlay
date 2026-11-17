// Rendering helpers and reusable components.
import { coverUrl } from './api.js';
import { store } from './store.js';

// ---------------------------------------------------------------- safe HTML
// html`...` escapes every interpolated value unless it is itself html`` output.
class Html {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);

function renderValue(v) {
  if (v === null || v === undefined || v === false) return '';
  if (v instanceof Html) return v.s;
  if (Array.isArray(v)) return v.map(renderValue).join('');
  return esc(v);
}

export function html(strings, ...values) {
  let out = '';
  strings.forEach((str, i) => { out += str + (i < values.length ? renderValue(values[i]) : ''); });
  return new Html(out);
}
export const raw = (s) => new Html(s);

export function mount(el, content) {
  el.innerHTML = content instanceof Html ? content.s : esc(content ?? '');
}

// ---------------------------------------------------------------- formatting
export function fmtTime(ms) {
  if (!ms && ms !== 0) return '–';
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

export function fmtLong(ms) {
  const min = Math.round((ms || 0) / 60000);
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  return `${h} hr ${min % 60} min`;
}

export const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

export function greeting() {
  const h = new Date().getHours();
  return h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}

// ---------------------------------------------------------------- ambient colour
const DEFAULT_AMBIENT = '#404040';
export function setAmbient(color) {
  const ok = typeof color === 'string' && /^#[0-9a-f]{6}$/i.test(color);
  const c = ok ? color : DEFAULT_AMBIENT;
  document.documentElement.style.setProperty('--ambient', c);
  const n = parseInt(c.slice(1), 16);
  // header gradient colour, toned down so white text stays readable
  const rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => Math.round(v * 0.75));
  document.documentElement.style.setProperty('--hdr-rgb', rgb.join(', '));
}

// ---------------------------------------------------------------- toasts
export function toast(message, type = 'info', ms = 3200) {
  const el = document.createElement('div');
  el.className = `wp-toast ${type}`;
  el.textContent = message;
  document.getElementById('toast-stack').append(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 300); }, ms);
}

// ---------------------------------------------------------------- track lists
// Rendered lists are registered so delegated click handlers can find the
// exact array (and its play context) a row belongs to.
const lists = new Map();
let listSeq = 0;
export function registerList(tracks, context = { type: 'library' }) {
  const id = `L${++listSeq}`;
  lists.set(id, { tracks, context });
  if (lists.size > 60) lists.delete(lists.keys().next().value); // keep memory bounded
  return id;
}
export const getList = (id) => lists.get(id);

/** Remote (catalogue) images go through the same-origin proxy: enables colour
 *  extraction on canvas and lets the service worker cache them for offline. */
export function thumbUrl(url) {
  return url ? `/api/catalog/thumb?u=${encodeURIComponent(url)}` : 'img/placeholder.svg';
}
export const artUrl = (t, size = 300) => (t?.cover_path ? coverUrl(t.cover_path, size) : thumbUrl(t?.thumbnail));

export const artistHref = (t) => (t.artist_id ? `#/artist/${t.artist_id}`
  : t.artist_browse_id ? `#/catalog/artist/${t.artist_browse_id}?name=${encodeURIComponent((t.artist_name || '').split(',')[0])}` : null);
export const albumHref = (t) => (t.album_id ? `#/album/${t.album_id}`
  : t.album_browse_id ? `#/catalog/album/${t.album_browse_id}` : null);

export const artistLink = (t) => {
  if (t.artists?.length > 1 && !t.artist_id) {
    return raw(t.artists.map((a) => (a.id
      ? `<a href="#/catalog/artist/${esc(a.id)}?name=${encodeURIComponent(a.name)}">${esc(a.name)}</a>`
      : esc(a.name))).join(', '));
  }
  const href = artistHref(t);
  return href ? html`<a href="${href}">${t.artist_name}</a>` : html`<span>${t.artist_name || 'Unknown artist'}</span>`;
};

export function likeButton(t, extraClass = '', listId = null, index = null) {
  const ident = t.id ? raw(`data-track-id="${t.id}" data-like-id="${t.id}"`)
    : raw(`data-like-vid="${esc(t.video_id)}" data-list="${listId}" data-index="${index}"`);
  return html`<button class="icon-btn tr-like ${extraClass} ${t.is_favorite ? 'liked' : ''}" type="button"
      data-action="like" ${ident}
      aria-label="${t.is_favorite ? 'Remove from Liked Songs' : 'Save to Liked Songs'}" aria-pressed="${t.is_favorite ? 'true' : 'false'}">
      <i class="bi ${t.is_favorite ? 'bi-heart-fill' : 'bi-heart'}"></i></button>`;
}

/**
 * Track table.
 * opts: { context, showAlbum=true, showArt=true, numbers='index'|'track', playlistId, draggable, discs }
 */
export function trackTable(tracks, opts = {}) {
  const { context = { type: 'library' }, showAlbum = true, showArt = true, numbers = 'index',
          playlistId = null, draggable = false, discs = false, compact = false } = opts;
  const listId = registerList(tracks, context);
  const multiDisc = discs && new Set(tracks.map((t) => t.disc_number)).size > 1;
  let lastDisc = null;

  const rows = tracks.map((t, i) => {
    const discHead = multiDisc && t.disc_number !== lastDisc
      ? html`<div class="disc-head"><i class="bi bi-disc"></i> Disc ${(lastDisc = t.disc_number)}</div>` : '';
    const num = numbers === 'track' ? (t.track_number ?? '–') : i + 1;
    const playable = t.has_audio || t.has_video;
    return html`${discHead}
      <div class="track-row ${playable ? '' : 'unavailable'}" role="row" tabindex="-1"
           data-list="${listId}" data-index="${i}" ${t.id ? raw(`data-track-id="${t.id}"`) : ''} ${t.video_id || t.youtube_id ? raw(`data-video-id="${esc(t.video_id || t.youtube_id)}"`) : ''}
           ${playlistId ? raw(`data-item-id="${t.item_id}"`) : ''} ${draggable ? raw('draggable="true"') : ''} ${t.id && store.downloaded?.has(t.id) ? raw('data-dl') : ''}>
        <div class="tr-num"><span class="num">${num}</span>
          <button class="play-mini" type="button" data-action="play-list" data-list="${listId}" data-index="${i}"
                  aria-label="Play ${t.title}"><i class="bi bi-play-fill"></i></button></div>
        <div class="tr-main">
          ${showArt ? html`<img loading="lazy" src="${artUrl(t)}" alt="">` : ''}
          <div class="tr-text">
            <span class="tr-title">${t.title}</span>
            <span class="tr-sub">${t.explicit ? html`<span class="tag-e" title="Explicit">E</span> ` : ''}${artistLink(t)}</span>
          </div>
        </div>
        ${showAlbum ? html`<div class="tr-album">${albumHref(t) ? html`<a href="${albumHref(t)}">${t.album_title}</a>` : t.album_title || ''}</div>` : ''}
        <div class="tr-flags">
          ${t.has_video ? html`<i class="bi bi-play-btn-fill flag video" title="Music video available"></i>` : ''}
          ${t.has_synced_lyrics ? html`<i class="bi bi-mic flag" title="Synced lyrics"></i>` : ''}
          ${t.id && store.downloaded?.has(t.id) ? html`<i class="bi bi-arrow-down-circle-fill flag dl" title="Downloaded"></i>` : ''}
          ${likeButton(t, '', listId, i)}
        </div>
        <div class="tr-dur r">${fmtTime(t.duration_ms)}</div>
        <button class="icon-btn tr-menu" type="button" data-action="track-menu" data-list="${listId}" data-index="${i}"
                ${playlistId ? raw(`data-playlist-id="${playlistId}"`) : ''} aria-label="More options for ${t.title}">
          <i class="bi bi-three-dots"></i></button>
      </div>`;
  });

  return html`
    <div class="tracks ${showAlbum ? '' : 'no-album'} ${compact ? 'compact' : ''}" role="table" ${playlistId ? raw(`data-playlist="${playlistId}"`) : ''}>
      <div class="track-head" role="row">
        <div class="text-center">#</div><div>Title</div>${showAlbum ? html`<div class="h-album">Album</div>` : ''}
        <div></div><div class="r"><i class="bi bi-clock"></i></div><div></div>
      </div>
      ${rows}
    </div>`;
}

// ---------------------------------------------------------------- cards
export function trackCard(t, listId, index) {
  return html`
    <div class="card-tile" data-action="play-list" data-list="${listId}" data-index="${index}">
      <div class="card-art">
        <img loading="lazy" src="${artUrl(t)}" alt="">
        ${t.has_video ? html`<span class="card-badge"><i class="bi bi-play-btn-fill"></i> Video</span>` : ''}
        <button class="fab-play" type="button" data-action="play-list" data-list="${listId}" data-index="${index}"
                aria-label="Play ${t.title}"><i class="bi bi-play-fill"></i></button>
      </div>
      <span class="card-title" title="${t.title}">${t.title}</span>
      <div class="card-sub">${artistLink(t)}</div>
    </div>`;
}

export function videoCard(t, listId, index) {
  const art = artUrl(t);
  return html`
    <div class="card-tile" data-action="play-list" data-mode="video" data-list="${listId}" data-index="${index}">
      <div class="card-art wide">
        <img class="blur-bg" loading="lazy" src="${art}" alt="">
        <img class="center-art" loading="lazy" src="${art}" alt="">
        <span class="card-badge right">${fmtTime(t.duration_ms)}</span>
        <button class="fab-play" type="button" data-action="play-list" data-mode="video" data-list="${listId}"
                data-index="${index}" aria-label="Play video ${t.title}"><i class="bi bi-play-fill"></i></button>
      </div>
      <span class="card-title" title="${t.title}">${t.title}</span>
      <div class="card-sub">${artistLink(t)}</div>
    </div>`;
}

export function albumCard(a) {
  const year = a.release_year ? html`${a.release_year} • ` : '';
  return html`
    <a class="card-tile" href="#/album/${a.id}">
      <div class="card-art">
        <img loading="lazy" src="${coverUrl(a.cover_path)}" alt="">
        <button class="fab-play" type="button" data-action="play-album" data-id="${a.id}" aria-label="Play ${a.title}">
          <i class="bi bi-play-fill"></i></button>
      </div>
      <span class="card-title" title="${a.title}">${a.title}</span>
      <div class="card-sub">${year}${a.artist_name || 'Various artists'}</div>
    </a>`;
}

export function artistCard(a) {
  return html`
    <a class="card-tile" href="#/artist/${a.id}">
      <div class="card-art round">
        <img loading="lazy" src="${coverUrl(a.image_path || a.cover_path)}" alt="">
        <button class="fab-play" type="button" data-action="play-artist" data-id="${a.id}" aria-label="Play ${a.name}">
          <i class="bi bi-play-fill"></i></button>
      </div>
      <span class="card-title">${a.name}</span>
      <div class="card-sub">Artist</div>
    </a>`;
}

export function playlistArt(p, size = 300) {
  const covers = p.mosaic || [];
  if (p.cover_path) return html`<img src="${coverUrl(p.cover_path, size)}" alt="">`;
  if (covers.length >= 4) {
    return html`<div class="mosaic">${covers.slice(0, 4).map((c) => html`<img src="${coverUrl(c)}" alt="">`)}</div>`;
  }
  if (covers.length) return html`<img src="${coverUrl(covers[0], size)}" alt="">`;
  return html`<div class="w-100 h-100 d-grid" style="place-items:center;font-size:40%;color:var(--muted)">
    <i class="bi bi-music-note-list" style="font-size:42px"></i></div>`;
}

export function playlistCard(p) {
  return html`
    <a class="card-tile" href="#/playlist/${p.id}">
      <div class="card-art">${playlistArt(p)}
        <button class="fab-play" type="button" data-action="play-playlist" data-id="${p.id}" aria-label="Play ${p.name}">
          <i class="bi bi-play-fill"></i></button>
      </div>
      <span class="card-title">${p.name}</span>
      <div class="card-sub">Playlist • ${plural(p.track_count, 'song')}</div>
    </a>`;
}

export function catalogAlbumCard(a) {
  const sub = [a.type && a.type !== 'Album' ? a.type : null, a.year, a.artist_name].filter(Boolean).join(' • ');
  return html`
    <a class="card-tile" href="#/catalog/album/${a.browse_id}">
      <div class="card-art">
        <img loading="lazy" src="${thumbUrl(a.thumbnail)}" alt="">
        <button class="fab-play" type="button" data-action="play-catalog-album" data-id="${a.browse_id}" aria-label="Play ${a.title}">
          <i class="bi bi-play-fill"></i></button>
      </div>
      <span class="card-title" title="${a.title}">${a.title}</span>
      <div class="card-sub">${sub || 'Album'}</div>
    </a>`;
}

export function catalogArtistCard(a) {
  return html`
    <a class="card-tile" href="#/catalog/artist/${a.browse_id}?name=${encodeURIComponent(a.name)}">
      <div class="card-art round">
        <img loading="lazy" src="${thumbUrl(a.thumbnail)}" alt="">
        <button class="fab-play" type="button" data-action="play-catalog-artist" data-id="${a.browse_id}" data-name="${a.name}"
                aria-label="Play ${a.name}"><i class="bi bi-play-fill"></i></button>
      </div>
      <span class="card-title">${a.name}</span>
      <div class="card-sub">${a.subscribers ? `${a.subscribers} subscribers` : 'Artist'}</div>
    </a>`;
}

export function catalogPlaylistCard(p) {
  return html`
    <a class="card-tile" href="#/catalog/playlist/${p.playlist_id}">
      <div class="card-art">
        <img loading="lazy" src="${thumbUrl(p.thumbnail)}" alt="">
        <button class="fab-play" type="button" data-action="play-catalog-playlist" data-id="${p.playlist_id}" aria-label="Play ${p.title}">
          <i class="bi bi-play-fill"></i></button>
      </div>
      <span class="card-title" title="${p.title}">${p.title}</span>
      <div class="card-sub">${p.author || 'Playlist'}</div>
    </a>`;
}

export function shelf(title, items, link = null) {
  if (!items || !items.length) return '';
  return html`
    <section class="section">
      <div class="section-head">
        <h2 class="section-title">${link ? html`<a href="${link}">${title}</a>` : title}</h2>
        ${link ? html`<a class="section-link" href="${link}">Show all</a>` : ''}
      </div>
      <div class="shelf">${items}</div>
    </section>`;
}

export function hero({ art, artClass = '', kind, title, desc = '', meta = '' }) {
  const long = String(title).length > 28 ? 'long' : '';
  return html`
    <header class="hero">
      <div class="hero-art ${artClass}">${art}</div>
      <div class="hero-text">
        <div class="hero-kind">${kind}</div>
        <h1 class="hero-title ${long}" title="${title}">${title}</h1>
        ${desc ? html`<div class="hero-desc">${desc}</div>` : ''}
        <div class="hero-meta">${meta}</div>
      </div>
    </header>`;
}

export function emptyState({ icon = 'music-note-beamed', title, text = '', action = '' }) {
  return html`
    <div class="empty">
      <div class="icon"><i class="bi bi-${icon}"></i></div>
      <h2>${title}</h2>
      ${text ? html`<p>${text}</p>` : ''}
      ${action}
    </div>`;
}

export function skeleton(kind = 'page') {
  if (kind === 'grid') {
    return html`<div class="grid-cards">${Array.from({ length: 10 }, () => html`<div><div class="skel skel-card"></div>
      <div class="skel skel-line" style="width:80%"></div><div class="skel skel-line" style="width:50%"></div></div>`)}</div>`;
  }
  return html`<div class="skel skel-hero"></div>
    ${Array.from({ length: 8 }, (_, i) => html`<div class="skel skel-line" style="width:${90 - (i % 3) * 15}%;height:36px"></div>`)}`;
}

// ---------------------------------------------------------------- now-playing highlighting
export function markPlaying(root = document) {
  const id = store.currentTrackId;
  const vid = store.currentVideoId;
  root.querySelectorAll('.is-playing').forEach((r) => r.classList.remove('is-playing'));
  const sel = [];
  if (id != null) sel.push(`.track-row[data-track-id="${id}"]`, `.chart-item[data-track-id="${id}"]`);
  if (vid) sel.push(`.track-row[data-video-id="${CSS.escape(vid)}"]`, `.chart-item[data-video-id="${CSS.escape(vid)}"]`);
  if (sel.length) root.querySelectorAll(sel.join(', ')).forEach((r) => r.classList.add('is-playing'));
}

export function updateLikeButtons(trackId, liked) {
  document.querySelectorAll(`[data-like-id="${trackId}"]`).forEach((b) => {
    b.classList.toggle('liked', liked);
    b.setAttribute('aria-pressed', liked ? 'true' : 'false');
    b.setAttribute('aria-label', liked ? 'Remove from Liked Songs' : 'Save to Liked Songs');
    const i = b.querySelector('i');
    if (i) i.className = `bi ${liked ? 'bi-heart-fill' : 'bi-heart'}`;
  });
}
