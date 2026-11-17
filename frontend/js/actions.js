// Delegated UI actions: anything with data-action="..." anywhere in the page.
import { api } from './api.js';
import { ensureTrack } from './catalog.js';
import { downloads } from './offline/downloads.js';
import { npActions } from './player/np-ui.js';
import { player } from './player/controller.js';
import { navigate, render } from './router.js';
import { store } from './store.js';
import { albumHref, artistHref, esc, getList, toast, updateLikeButtons } from './ui.js';

// ---------------------------------------------------------------- helpers
async function tracksFor(kind, id, extra = {}) {
  if (kind === 'album') return (await api.get(`/api/albums/${id}`)).tracks;
  if (kind === 'artist') return (await api.get(`/api/artists/${id}`)).top_tracks;
  if (kind === 'playlist') return (await api.get(`/api/playlists/${id}`)).items;
  if (kind === 'liked') return (await api.get('/api/tracks', { favorites: true, sort: 'liked', limit: 500 })).items;
  if (kind === 'catalog-album') return (await api.get(`/api/catalog/album/${id}`)).tracks;
  if (kind === 'catalog-playlist') return (await api.get(`/api/catalog/playlist/${id}`)).tracks;
  if (kind === 'catalog-artist') return (await api.get(`/api/catalog/artist/${id}`, { name: extra.name })).songs;
  return [];
}

function setLikeState(track, liked) {
  track.is_favorite = liked;
  updateLikeButtons(track.id, liked);
  if (track.video_id) {
    document.querySelectorAll(`[data-like-vid="${CSS.escape(track.video_id)}"]`).forEach((b) => {
      b.classList.toggle('liked', liked);
      b.querySelector('i').className = `bi ${liked ? 'bi-heart-fill' : 'bi-heart'}`;
    });
  }
}

/** Like / unlike. Catalogue songs are fetched first; liking saves them to Your Library. */
export async function toggleLike(trackOrId) {
  const track = typeof trackOrId === 'object' ? trackOrId : { id: trackOrId };
  const btn = track.id ? document.querySelector(`[data-like-id="${track.id}"]`) : null;
  const liked = track.is_favorite !== undefined && typeof trackOrId === 'object'
    ? !track.is_favorite : !(btn && btn.classList.contains('liked'));
  try {
    if (!track.id) {
      toast('Saving to your library…');
      await ensureTrack(track);
    }
    setLikeState(track, liked);                 // optimistic
    await api.patch(`/api/tracks/${track.id}`, { is_favorite: liked });
    store.emit('like', { trackId: track.id, liked });
    store.likedCount += liked ? 1 : -1;
    store.emit('playlists', store.playlists);
    toast(liked ? 'Added to Liked Songs' : 'Removed from Liked Songs');
  } catch (err) {
    if (track.id) setLikeState(track, !liked);
    toast(err.message, 'error');
  }
}

export async function addToPlaylist(playlistId, tracks) {
  const list = await Promise.all(tracks.map((t) => (typeof t === 'object' ? ensureTrack(t) : { id: t })));
  await api.post(`/api/playlists/${playlistId}/tracks`, { track_ids: list.map((t) => t.id) });
  const p = store.playlists.find((x) => x.id === Number(playlistId));
  toast(`Added to ${p ? p.name : 'playlist'}`);
  store.refreshPlaylists();
}

// ---------------------------------------------------------------- context menu
const menu = document.getElementById('ctx-menu');
let menuTrack = null;
let menuOrigin = null;

function closeMenu() {
  menu.hidden = true;
  menuTrack = null;
  menuOrigin?.focus?.();
  menuOrigin = null;
}

function positionMenu(x, y) {
  menu.hidden = false;
  const { innerWidth: w, innerHeight: h } = window;
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(x, w - r.width - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, h - r.height - 8))}px`;
}

export function openTrackMenu(track, { x, y, playlistId = null, itemId = null, origin = null }) {
  menuTrack = track;
  menuOrigin = origin;
  const pls = store.playlists.filter((p) => !p.smart_rules);
  const artist = artistHref(track);
  const album = albumHref(track);
  const downloaded = track.id && downloads.has(track.id);
  const q = encodeURIComponent(`${track.artist_name || ''} ${track.title} official music video`.trim());
  menu.innerHTML = `
    <button role="menuitem" data-menu="play-next"><i class="bi bi-arrow-return-right"></i>Play next</button>
    <button role="menuitem" data-menu="queue"><i class="bi bi-list-plus"></i>Add to queue</button>
    <button role="menuitem" data-menu="radio"><i class="bi bi-broadcast"></i>Go to song radio</button>
    <hr>
    <div class="ctx-label">Add to playlist</div>
    <button role="menuitem" data-menu="new-playlist"><i class="bi bi-plus-lg"></i>New playlist</button>
    <div class="ctx-sub">${pls.map((p) => `
      <button role="menuitem" data-menu="add-to" data-playlist="${p.id}"><i class="bi bi-music-note-list"></i>${esc(p.name)}</button>`).join('')}
    </div>
    ${playlistId ? `<button role="menuitem" data-menu="remove" data-playlist="${playlistId}" data-item="${itemId}">
        <i class="bi bi-trash3"></i>Remove from this playlist</button>` : ''}
    <hr>
    <button role="menuitem" data-menu="like"><i class="bi ${track.is_favorite ? 'bi-heart-fill' : 'bi-heart'}"></i>
      ${track.is_favorite ? 'Remove from Liked Songs' : 'Save to Liked Songs'}</button>
    ${downloads.supported ? `<button role="menuitem" data-menu="${downloaded ? 'undownload' : 'download'}">
        <i class="bi ${downloaded ? 'bi-check-circle-fill' : 'bi-arrow-down-circle'}"></i>${downloaded ? 'Remove download' : 'Download'}</button>` : ''}
    ${album ? `<a role="menuitem" href="${album}"><i class="bi bi-disc"></i>Go to album</a>` : ''}
    ${artist ? `<a role="menuitem" href="${artist}"><i class="bi bi-person"></i>Go to artist</a>` : ''}
    ${track.has_video || !track.id ? '' : `<a role="menuitem" href="#/import?q=${q}&track=${track.id}"><i class="bi bi-camera-video"></i>Find music video</a>`}
  `;
  positionMenu(x, y);
  menu.querySelector('button, a')?.focus();
}

menu.addEventListener('click', async (e) => {
  const item = e.target.closest('[data-menu], a');
  if (!item || !menuTrack) return;
  const t = menuTrack;
  const action = item.dataset.menu;
  closeMenu();
  try {
    if (action === 'play-next') player.playNext([t]);
    else if (action === 'queue') player.enqueue([t]);
    else if (action === 'radio') await player.startRadio(t);
    else if (action === 'like') await toggleLike(t);
    else if (action === 'add-to') await addToPlaylist(item.dataset.playlist, [t]);
    else if (action === 'new-playlist') { await ensureTrack(t); openPlaylistModal(null, [t.id]); }
    else if (action === 'download') { toast(`Downloading "${t.title}"…`); await downloads.download(t); }
    else if (action === 'undownload') await downloads.remove(t.id);
    else if (action === 'remove') {
      await api.del(`/api/playlists/${item.dataset.playlist}/items/${item.dataset.item}`);
      toast('Removed from playlist');
      store.refreshPlaylists();
      render();
    }
  } catch (err) {
    toast(err.message, 'error');
  }
});

document.addEventListener('pointerdown', (e) => { if (!menu.hidden && !menu.contains(e.target)) closeMenu(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !menu.hidden) closeMenu(); });
window.addEventListener('hashchange', () => { if (!menu.hidden) closeMenu(); });
document.getElementById('main').addEventListener('scroll', () => { if (!menu.hidden) closeMenu(); }, { passive: true });

// Right-click on a track row opens the same menu.
document.addEventListener('contextmenu', (e) => {
  const row = e.target.closest('.track-row');
  if (!row) return;
  const entry = getList(row.dataset.list);
  if (!entry) return;
  e.preventDefault();
  const playlistId = row.closest('[data-playlist]')?.dataset.playlist;
  openTrackMenu(entry.tracks[row.dataset.index], { x: e.clientX, y: e.clientY, playlistId, itemId: row.dataset.itemId });
});

// ---------------------------------------------------------------- playlist modal
const modalEl = document.getElementById('playlist-modal');
let modal = null;
let pendingTrackIds = [];

export function openPlaylistModal(playlist = null, trackIds = []) {
  modal ??= new bootstrap.Modal(modalEl);
  pendingTrackIds = trackIds;
  document.getElementById('playlist-modal-title').textContent = playlist ? 'Edit details' : 'Create playlist';
  document.getElementById('pl-id').value = playlist?.id ?? '';
  document.getElementById('pl-name').value = playlist?.name ?? `My Playlist #${store.playlists.length + 1}`;
  document.getElementById('pl-desc').value = playlist?.description ?? '';
  modal.show();
  modalEl.addEventListener('shown.bs.modal', () => document.getElementById('pl-name').select(), { once: true });
}

document.getElementById('playlist-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const id = document.getElementById('pl-id').value;
  const body = {
    name: document.getElementById('pl-name').value.trim(),
    description: document.getElementById('pl-desc').value.trim() || null,
  };
  if (!body.name) return;
  try {
    if (id) {
      await api.patch(`/api/playlists/${id}`, body);
      toast('Playlist updated');
      modal.hide();
      await store.refreshPlaylists();
      render();
    } else {
      const p = await api.post('/api/playlists', { ...body, track_ids: pendingTrackIds });
      modal.hide();
      await store.refreshPlaylists();
      toast(pendingTrackIds.length ? `Created ${p.name} and added the song` : `Created ${p.name}`);
      navigate(`/playlist/${p.id}`);
    }
  } catch (err) {
    toast(err.message, 'error');
  }
});

// ---------------------------------------------------------------- install (PWA)
let installPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  installPrompt = e;
  document.body.classList.add('can-install');
});
window.addEventListener('appinstalled', () => {
  installPrompt = null;
  document.body.classList.remove('can-install');
  toast('WonderPlay is installed');
});

function installSteps() {
  const ua = navigator.userAgent;
  if (window.matchMedia('(display-mode: standalone)').matches || navigator.standalone) {
    return '<p>WonderPlay is already installed and running as an app.</p>';
  }
  if (/iPhone|iPad|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) {
    if (/CriOS|FxiOS|EdgiOS/.test(ua)) {
      return '<ol><li>On iPhone and iPad, apps can only be installed from <b>Safari</b>. Copy this page\'s address and open it in Safari.</li>'
        + '<li>Then tap <b>Share</b> <i class="bi bi-box-arrow-up"></i> → <b>Add to Home Screen</b> → <b>Add</b>.</li></ol>';
    }
    return '<ol><li>Tap the <b>Share</b> button <i class="bi bi-box-arrow-up"></i> in Safari\'s toolbar.</li>'
      + '<li>Scroll down and tap <b>Add to Home Screen</b>.</li><li>Tap <b>Add</b>. WonderPlay appears on your home screen.</li></ol>';
  }
  if (/SamsungBrowser/.test(ua)) {
    return '<ol><li>Tap the <b>install icon</b> <i class="bi bi-download"></i> in the address bar (next to the reload button), then <b>Install</b>.</li>'
      + '<li>No icon? Tap the menu <i class="bi bi-list"></i> → <b>Add page to</b> → <b>Home screen</b>.</li></ol>';
  }
  if (/Firefox/.test(ua) && /Android/.test(ua)) {
    return '<ol><li>Tap the menu <i class="bi bi-three-dots-vertical"></i>.</li><li>Tap <b>Install</b> (or <b>Add to Home screen</b>).</li></ol>';
  }
  if (/Android/.test(ua)) {
    return '<ol><li>Tap the menu <i class="bi bi-three-dots-vertical"></i> at the top right of Chrome.</li>'
      + '<li>Tap <b>Install app</b> (or <b>Add to Home screen</b> → <b>Install</b>).</li></ol>';
  }
  return '<ol><li>Click the <b>install icon</b> <i class="bi bi-download"></i> at the right end of the address bar.</li>'
    + '<li>Or open the browser menu → <b>Cast, save and share</b> / <b>Apps</b> → <b>Install WonderPlay</b>.</li></ol>';
}

document.getElementById('install-sheet')?.addEventListener('click', (e) => {
  if (e.target.id === 'install-sheet') e.currentTarget.hidden = true;   // tap outside closes
});

// ---------------------------------------------------------------- delegated click handler
const handlers = {
  async 'play-list'(el) {
    const entry = getList(el.dataset.list);
    if (entry) await player.playList(entry.tracks, Number(el.dataset.index), entry.context, { mode: el.dataset.mode });
  },
  async 'play-album'(el) { await player.playList(await tracksFor('album', el.dataset.id), 0, { type: 'album', id: +el.dataset.id }); },
  async 'play-artist'(el) { await player.playList(await tracksFor('artist', el.dataset.id), 0, { type: 'artist', id: +el.dataset.id }); },
  async 'play-playlist'(el) { await player.playList(await tracksFor('playlist', el.dataset.id), 0, { type: 'playlist', id: +el.dataset.id }); },
  async 'play-liked'() { await player.playList(await tracksFor('liked'), 0, { type: 'library' }); },
  async 'play-catalog-album'(el) { await player.playList(await tracksFor('catalog-album', el.dataset.id), 0, { type: 'album' }); },
  async 'play-catalog-playlist'(el) { await player.playList(await tracksFor('catalog-playlist', el.dataset.id), 0, { type: 'playlist' }); },
  async 'play-catalog-artist'(el) {
    await player.playList(await tracksFor('catalog-artist', el.dataset.id, { name: el.dataset.name }), 0, { type: 'artist' });
  },
  async 'shuffle-list'(el) {
    const entry = getList(el.dataset.list);
    if (!entry?.tracks.length) return;
    player.setShuffle(true);
    await player.playList(entry.tracks, Math.floor(Math.random() * entry.tracks.length), entry.context);
  },
  async 'radio-list'(el) {
    const entry = getList(el.dataset.list);
    const t = entry?.tracks[Number(el.dataset.index || 0)];
    if (t) await player.startRadio(t);
  },
  like(el) {
    if (el.dataset.trackId) return toggleLike(Number(el.dataset.trackId));
    const entry = getList(el.dataset.list);
    const t = entry?.tracks[Number(el.dataset.index)];
    return t ? toggleLike(t) : undefined;
  },
  'track-menu'(el) {
    const entry = getList(el.dataset.list);
    if (!entry) return;
    const r = el.getBoundingClientRect();
    const row = el.closest('.track-row');
    openTrackMenu(entry.tracks[el.dataset.index], {
      x: r.left - 200, y: r.bottom + 4, playlistId: el.dataset.playlistId, itemId: row?.dataset.itemId, origin: el,
    });
  },
  async 'download-list'(el) {
    const entry = getList(el.dataset.list);
    if (entry?.tracks.length) await downloads.downloadMany(entry.tracks, el.dataset.label || 'songs');
  },
  async 'install-app'() {
    if (installPrompt) {
      try {
        await installPrompt.prompt();          // rejects if the browser refuses to show it
        await installPrompt.userChoice;
        installPrompt = null;
        document.body.classList.remove('can-install');
        return;
      } catch {
        installPrompt = null;   // prompt refused or already used: show the manual steps instead
      }
    }
    // No prompt available (iOS never offers one; Samsung Internet uses its own button): show the steps.
    document.getElementById('install-steps').innerHTML = installSteps();
    document.getElementById('install-sheet').hidden = false;
  },
  'close-install'() { document.getElementById('install-sheet').hidden = true; },
  'dismiss-install'(el) {
    try { localStorage.setItem('wp.installDismissed', '1'); } catch { /* private mode */ }
    el.closest('.install-promo')?.remove();
  },
  'create-playlist'() { openPlaylistModal(); },
  'nav-back'() { history.back(); },
  'nav-forward'() { history.forward(); },
  retry() { render(); },
  'like-current'() { const t = player.current; return t ? toggleLike(t) : undefined; },
  'current-menu'(el) {
    const t = player.current;
    if (!t) return;
    const r = el.getBoundingClientRect();
    openTrackMenu(t, { x: r.left - 200, y: r.bottom + 4, origin: el });
  },
  ...npActions,
};

export function registerAction(name, fn) { handlers[name] = fn; }

document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-action]');
  if (!el || el.disabled) return;
  const fn = handlers[el.dataset.action];
  if (!fn) return;
  // A link nested inside a clickable card (e.g. the artist name) wins.
  const link = e.target.closest('a');
  if (link && link !== el && el.contains(link)) return;
  // Buttons inside links (e.g. play on an album card) must not navigate.
  if (el.tagName === 'BUTTON' || el.closest('a')) { e.preventDefault(); e.stopPropagation(); }
  try {
    await fn(el, e);
  } catch (err) {
    console.error(err);
    toast(err.message || 'Something went wrong', 'error');
  }
});

// Double-click a row to play it (desktop convention).
document.addEventListener('dblclick', (e) => {
  const row = e.target.closest('.track-row');
  if (!row || e.target.closest('button, a')) return;
  const entry = getList(row.dataset.list);
  if (entry) player.playList(entry.tracks, Number(row.dataset.index), entry.context);
});

// Single click selects a row; on phones a tap plays it.
document.addEventListener('click', (e) => {
  const row = e.target.closest('.track-row');
  document.querySelectorAll('.track-row.selected').forEach((r) => r !== row && r.classList.remove('selected'));
  if (!row || e.target.closest('button, a')) return;
  row.classList.add('selected');
  if (window.matchMedia('(max-width: 767.98px), (pointer: coarse)').matches) {
    const entry = getList(row.dataset.list);
    if (entry) player.playList(entry.tracks, Number(row.dataset.index), entry.context);
  }
});
