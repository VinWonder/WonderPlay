// Your Library (tabs: playlists / albums / artists / songs) and Music Videos.
import { api } from '../api.js';
import { navigate } from '../router.js';
import { store } from '../store.js';
import {
  albumCard, artistCard, emptyState, html, mount, plural, playlistCard, registerList, trackTable, videoCard,
} from '../ui.js';

const TABS = [
  ['playlists', 'Playlists'], ['albums', 'Albums'], ['artists', 'Artists'], ['songs', 'Songs'],
];
const SORTS = {
  albums: [['recent', 'Recently added'], ['title', 'Alphabetical'], ['artist', 'Artist'], ['year', 'Release year']],
  songs: [['recent', 'Recently added'], ['title', 'Title'], ['artist', 'Artist'], ['album', 'Album'],
          ['plays', 'Most played'], ['last_played', 'Recently played']],
};
const PAGE = 100;

function sortSelect(tab, current) {
  const opts = SORTS[tab];
  if (!opts) return '';
  return html`<select class="form-select wp-select" id="lib-sort" aria-label="Sort by">
    ${opts.map(([v, label]) => html`<option value="${v}" ${v === current ? 'selected' : ''}>${label}</option>`)}
  </select>`;
}

export async function libraryView({ params, query, signal }) {
  const tab = TABS.some(([k]) => k === params[0]) ? params[0] : 'playlists';
  const sort = query.get('sort') || 'recent';
  let body;
  let total = 0;
  let songItems = [];

  if (tab === 'playlists') {
    const pls = await store.refreshPlaylists();
    const liked = html`<a class="card-tile" href="#/liked">
      <div class="card-art liked-art d-grid" style="place-items:center"><i class="bi bi-heart-fill" style="font-size:56px"></i>
        <button class="fab-play" type="button" data-action="play-liked" aria-label="Play Liked Songs"><i class="bi bi-play-fill"></i></button></div>
      <span class="card-title">Liked Songs</span><div class="card-sub">${plural(store.likedCount, 'song')}</div></a>`;
    const create = html`<button class="card-tile text-start border-0" type="button" data-action="create-playlist">
      <div class="card-art d-grid" style="place-items:center;background:var(--surface-strong)"><i class="bi bi-plus-lg" style="font-size:48px"></i></div>
      <span class="card-title">New playlist</span><div class="card-sub">Build your own mix</div></button>`;
    body = html`<div class="grid-cards">${liked}${pls.map(playlistCard)}${create}</div>`;
  } else if (tab === 'albums') {
    const r = await api.get('/api/albums', { sort, limit: 500 }, { signal });
    total = r.total;
    body = r.items.length ? html`<div class="grid-cards">${r.items.map(albumCard)}</div>`
      : emptyState({ icon: 'disc', title: 'No albums yet', text: 'Scan a music folder to fill your library.' });
  } else if (tab === 'artists') {
    const r = await api.get('/api/artists', { limit: 500 }, { signal });
    total = r.total;
    body = r.items.length ? html`<div class="grid-cards">${r.items.map(artistCard)}</div>`
      : emptyState({ icon: 'person', title: 'No artists yet' });
  } else {
    const r = await api.get('/api/tracks', { sort, limit: PAGE }, { signal });
    total = r.total;
    body = r.items.length
      ? html`<div id="songs-table">${trackTable(r.items, { context: { type: 'library' } })}</div>
        ${r.total > PAGE ? html`<div class="text-center mt-3"><button class="pill-btn" id="load-more" type="button" data-offset="${PAGE}">
          Show more (${(r.total - PAGE).toLocaleString()} left)</button></div>` : ''}`
      : emptyState({ icon: 'music-note', title: 'No songs yet', action: html`<a class="pill-btn" href="#/settings">Add a folder</a>` });
    songItems = r.items;
  }

  return {
    title: 'Your Library',
    html: html`
      <h1 class="page-title">Your Library</h1>
      <div class="toolbar">
        <div class="chips mb-0" role="tablist">
          ${TABS.map(([k, label]) => html`<a class="chip ${k === tab ? 'active' : ''}" role="tab" href="#/library/${k}"
              aria-selected="${k === tab ? 'true' : 'false'}">${label}</a>`)}
        </div>
        <div class="d-flex align-items-center gap-3">
          ${total ? html`<span class="muted small">${total.toLocaleString()} total</span>` : ''}
          ${sortSelect(tab, sort)}
        </div>
      </div>
      ${body}`,
    mount(root) {
      root.querySelector('#lib-sort')?.addEventListener('change', (e) => navigate(`/library/${tab}?sort=${e.target.value}`));
      const more = root.querySelector('#load-more');
      if (!more) return;
      const all = [...songItems];
      more.addEventListener('click', async () => {
        more.disabled = true;
        const offset = Number(more.dataset.offset);
        const r = await api.get('/api/tracks', { sort, limit: PAGE, offset });
        all.push(...r.items);
        // Re-render as one list so numbering and play-from-here stay consistent.
        mount(root.querySelector('#songs-table'), trackTable(all, { context: { type: 'library' } }));
        const left = r.total - (offset + r.items.length);
        if (left > 0) {
          more.dataset.offset = offset + PAGE;
          more.textContent = `Show more (${left.toLocaleString()} left)`;
          more.disabled = false;
        } else more.remove();
      });
    },
  };
}

export async function videosView({ signal }) {
  const r = await api.get('/api/tracks', { has_video: true, sort: 'recent', limit: 500 }, { signal });
  const listId = registerList(r.items, { type: 'library' });
  return {
    title: 'Music Videos',
    ambient: '#7a1f4b',
    html: html`
      <h1 class="page-title">Music Videos</h1>
      ${r.items.length
        ? html`<p class="muted mb-4">${plural(r.total, 'video')}. Every one plays ad-free, and you can switch between Song and Video mode at any time.</p>
          <div class="grid-cards" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr))">
            ${r.items.map((t, i) => videoCard(t, listId, i))}</div>`
        : emptyState({ icon: 'play-btn', title: 'No music videos yet',
            text: 'Add official videos from YouTube, or put MP4 files in a library folder.',
            action: html`<a class="pill-btn pill-btn-primary" href="#/import"><i class="bi bi-youtube"></i> Add from YouTube</a>` })}
    `,
  };
}
