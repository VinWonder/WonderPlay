// Album, artist, playlist and Liked Songs pages.
import { api, coverUrl } from '../api.js';
import { openPlaylistModal } from '../actions.js';
import { downloads } from '../offline/downloads.js';
import { navigate, render } from '../router.js';
import { store } from '../store.js';
import {
  albumCard, emptyState, fmtLong, html, hero, plural, playlistArt, registerList, shelf, toast, trackTable,
} from '../ui.js';

function actionRow(listId, extra = '') {
  return html`
    <div class="action-row">
      <button class="big-play" type="button" data-action="play-list" data-list="${listId}" data-index="0" aria-label="Play">
        <i class="bi bi-play-fill"></i></button>
      <button class="icon-btn" type="button" data-action="shuffle-list" data-list="${listId}" aria-label="Shuffle play" title="Shuffle play">
        <i class="bi bi-shuffle"></i></button>
      ${extra}
    </div>`;
}

const totalMs = (tracks) => tracks.reduce((s, t) => s + (t.duration_ms || 0), 0);

// ---------------------------------------------------------------- album
export async function albumView({ params, signal }) {
  const a = await api.get(`/api/albums/${params[0]}`, null, { signal });
  const listId = registerList(a.tracks, { type: 'album', id: a.id });
  const kind = { album: 'Album', single: 'Single', ep: 'EP', compilation: 'Compilation', live: 'Live album' }[a.album_type] || 'Album';
  return {
    title: a.title,
    ambient: a.dominant_color,
    html: html`
      ${hero({
        art: html`<img src="${coverUrl(a.cover_path, null)}" alt="Cover of ${a.title}">`,
        kind, title: a.title,
        meta: html`${a.artist_id ? html`<strong><a href="#/artist/${a.artist_id}">${a.artist_name}</a></strong>` : html`<strong>Various artists</strong>`}
          ${a.release_year ? html`<span class="dot"></span>${a.release_year}` : ''}
          <span class="dot"></span>${plural(a.tracks.length, 'song')}<span class="muted">, ${fmtLong(totalMs(a.tracks))}</span>`,
      })}
      ${actionRow(listId)}
      ${trackTable(a.tracks, { context: { type: 'album', id: a.id }, showAlbum: false, showArt: false, numbers: 'track', discs: true })}
      ${a.genre ? html`<p class="muted mt-4 small">${a.genre}</p>` : ''}
    `,
  };
}

// ---------------------------------------------------------------- artist
export async function artistView({ params, signal }) {
  const a = await api.get(`/api/artists/${params[0]}`, null, { signal });
  const cover = a.image_path || a.albums.find((x) => x.cover_path)?.cover_path || a.top_tracks.find((t) => t.cover_path)?.cover_path;
  const color = a.albums.find((x) => x.dominant_color)?.dominant_color || a.top_tracks[0]?.dominant_color;
  const listId = registerList(a.top_tracks, { type: 'artist', id: a.id });
  const songCount = a.top_tracks.length;
  return {
    title: a.name,
    ambient: color,
    html: html`
      ${hero({
        art: html`<img src="${coverUrl(cover, null)}" alt="">`, artClass: 'round',
        kind: html`<i class="bi bi-patch-check-fill" style="color:var(--accent)"></i> Artist`,
        title: a.name,
        meta: html`<span class="muted">${a.albums.length ? plural(a.albums.length, 'release') : plural(songCount, 'song')} in your library</span>`,
      })}
      ${songCount ? actionRow(listId) : ''}
      ${songCount ? html`<section class="section"><h2 class="section-title mb-3">Popular</h2>
        ${trackTable(a.top_tracks, { context: { type: 'artist', id: a.id } })}</section>` : ''}
      ${shelf('Discography', a.albums.map(albumCard))}
      ${shelf('Appears on', a.appears_on.map(albumCard))}
      ${!songCount && !a.albums.length ? emptyState({ icon: 'person', title: 'No songs yet' }) : ''}
    `,
  };
}

// ---------------------------------------------------------------- playlist
export async function playlistView({ params, signal }) {
  const p = await api.get(`/api/playlists/${params[0]}`, null, { signal });
  const listId = registerList(p.items, { type: 'playlist', id: p.id });
  const color = p.dominant_color || p.items.find((t) => t.dominant_color)?.dominant_color;
  const extra = html`
    <button class="icon-btn ${p.keep_offline ? 'active' : ''}" type="button" data-pl-action="offline"
            aria-pressed="${p.keep_offline ? 'true' : 'false'}" title="${p.keep_offline ? 'Available offline' : 'Download for offline'}">
      <i class="bi ${p.keep_offline ? 'bi-arrow-down-circle-fill' : 'bi-arrow-down-circle'}"></i></button>
    <button class="icon-btn ${p.is_pinned ? 'active' : ''}" type="button" data-pl-action="pin" title="${p.is_pinned ? 'Unpin' : 'Pin to top'}">
      <i class="bi ${p.is_pinned ? 'bi-pin-angle-fill' : 'bi-pin-angle'}"></i></button>
    <button class="icon-btn" type="button" data-pl-action="edit" title="Edit details"><i class="bi bi-pencil"></i></button>
    <button class="icon-btn" type="button" data-pl-action="delete" title="Delete playlist"><i class="bi bi-trash3"></i></button>`;

  return {
    title: p.name,
    ambient: color,
    html: html`
      ${hero({
        art: playlistArt({ ...p, mosaic: [...new Set(p.items.map((t) => t.cover_path).filter(Boolean))].slice(0, 4) }, null),
        kind: 'Playlist', title: p.name, desc: p.description,
        meta: html`<strong>You</strong><span class="dot"></span>${plural(p.track_count, 'song')}
          ${p.track_count ? html`<span class="muted">, ${fmtLong(p.total_duration_ms)}</span>` : ''}`,
      })}
      ${actionRow(listId, extra)}
      ${p.items.length
        ? trackTable(p.items, { context: { type: 'playlist', id: p.id }, playlistId: p.id, draggable: true })
        : emptyState({ icon: 'music-note-list', title: "Let's find something for your playlist",
            text: 'Use the ••• menu on any song, or right-click it, and choose this playlist.',
            action: html`<a class="pill-btn" href="#/search">Find songs</a>` })}
    `,
    mount(root) {
      root.querySelector('.action-row').addEventListener('click', (e) => onPlaylistAction(e, p));
      enableDragReorder(root, p.id);
    },
  };
}

async function onPlaylistAction(e, p) {
  const btn = e.target.closest('[data-pl-action]');
  if (!btn) return;
  try {
    switch (btn.dataset.plAction) {
      case 'edit':
        openPlaylistModal(p);
        return;
      case 'delete':
        if (!confirm(`Delete "${p.name}"? This can't be undone.`)) return;
        await api.del(`/api/playlists/${p.id}`);
        toast(`Deleted ${p.name}`);
        await store.refreshPlaylists();
        navigate('/library/playlists');
        return;
      case 'pin':
        await api.patch(`/api/playlists/${p.id}`, { is_pinned: !p.is_pinned });
        toast(p.is_pinned ? 'Unpinned' : 'Pinned to the top of your library');
        break;
      case 'offline':
        await api.patch(`/api/playlists/${p.id}`, { keep_offline: !p.keep_offline });
        if (p.keep_offline) {
          toast('This playlist will no longer download new songs (existing downloads are kept in Downloads)');
        } else {
          downloads.downloadMany(p.items, `songs from ${p.name}`);
        }
        break;
      default:
        return;
    }
    await store.refreshPlaylists();
    render();
  } catch (err) {
    toast(err.message, 'error');
  }
}

// HTML5 drag & drop: drop a row before/after another; persisted with one move call.
function enableDragReorder(root, playlistId) {
  const table = root.querySelector('.tracks[data-playlist]');
  if (!table) return;
  let dragged = null;
  const rows = () => [...table.querySelectorAll('.track-row')];
  const clearMarks = () => rows().forEach((r) => r.classList.remove('drop-before', 'drop-after'));

  table.addEventListener('dragstart', (e) => {
    dragged = e.target.closest('.track-row');
    if (!dragged) return;
    dragged.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', dragged.dataset.itemId);
  });
  table.addEventListener('dragover', (e) => {
    const row = e.target.closest('.track-row');
    if (!dragged || !row || row === dragged) return;
    e.preventDefault();
    clearMarks();
    const r = row.getBoundingClientRect();
    row.classList.add(e.clientY < r.top + r.height / 2 ? 'drop-before' : 'drop-after');
  });
  table.addEventListener('dragleave', (e) => { if (!table.contains(e.relatedTarget)) clearMarks(); });
  table.addEventListener('dragend', () => { dragged?.classList.remove('dragging'); dragged = null; clearMarks(); });
  table.addEventListener('drop', async (e) => {
    const row = e.target.closest('.track-row');
    if (!dragged || !row || row === dragged) return;
    e.preventDefault();
    const list = rows();
    const from = list.indexOf(dragged);
    let to = list.indexOf(row) + (row.classList.contains('drop-after') ? 1 : 0);
    if (from < to) to -= 1;
    const itemId = dragged.dataset.itemId;
    dragged.classList.remove('dragging');
    dragged = null;
    clearMarks();
    if (from === to) return;
    try {
      await api.post(`/api/playlists/${playlistId}/items/${itemId}/move`, { position: to });
      render();
    } catch (err) {
      toast(err.message, 'error');
    }
  });
}

// ---------------------------------------------------------------- liked songs
export async function likedView({ signal }) {
  const r = await api.get('/api/tracks', { favorites: true, sort: 'liked', limit: 500 }, { signal });
  const listId = registerList(r.items, { type: 'library' });
  return {
    title: 'Liked Songs',
    ambient: '#5b3bd6',
    html: html`
      ${hero({
        art: html`<i class="bi bi-heart-fill"></i>`, artClass: 'icon liked-art',
        kind: 'Playlist', title: 'Liked Songs',
        meta: html`<strong>You</strong><span class="dot"></span>${plural(r.total, 'song')}`,
      })}
      ${r.items.length ? html`${actionRow(listId)}${trackTable(r.items, { context: { type: 'library' } })}`
        : emptyState({ icon: 'heart', title: 'Songs you like will appear here',
            text: 'Save songs by tapping the heart icon.', action: html`<a class="pill-btn" href="#/search">Find songs</a>` })}
    `,
  };
}
