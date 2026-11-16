// Catalogue pages (YouTube Music): artist, album, playlist, mood/genre.
import { api } from '../api.js';
import {
  catalogAlbumCard, catalogArtistCard, catalogPlaylistCard, emptyState, fmtLong, hero, html, mount, plural,
  registerList, shelf, thumbUrl, trackTable, videoCard,
} from '../ui.js';

function actions(listId, { radio = true, download = true, label = 'songs' } = {}) {
  return html`
    <div class="action-row">
      <button class="big-play" type="button" data-action="play-list" data-list="${listId}" data-index="0" aria-label="Play">
        <i class="bi bi-play-fill"></i></button>
      <button class="icon-btn" type="button" data-action="shuffle-list" data-list="${listId}" title="Shuffle play" aria-label="Shuffle play">
        <i class="bi bi-shuffle"></i></button>
      ${radio ? html`<button class="icon-btn" type="button" data-action="radio-list" data-list="${listId}" data-index="0"
          title="Start radio" aria-label="Start radio"><i class="bi bi-broadcast"></i></button>` : ''}
      ${download ? html`<button class="icon-btn" type="button" data-action="download-list" data-list="${listId}" data-label="${label}"
          title="Download for offline" aria-label="Download for offline"><i class="bi bi-arrow-down-circle"></i></button>` : ''}
    </div>`;
}

const total = (tracks) => tracks.reduce((s, t) => s + (t.duration_ms || 0), 0);

export async function catalogArtistView({ params, query, signal }) {
  const a = await api.get(`/api/catalog/artist/${params[0]}`, { name: query.get('name') }, { signal });
  const list = registerList(a.songs, { type: 'artist' });
  const videos = registerList(a.videos, { type: 'artist' });
  const showAll = a.songs.length > 5;
  return {
    title: a.name,
    html: html`
      ${hero({
        art: html`<img src="${thumbUrl(a.thumbnail)}" alt="">`, artClass: 'round',
        kind: html`<i class="bi bi-patch-check-fill" style="color:var(--accent)"></i> Artist`,
        title: a.name,
        meta: html`<span class="muted">${a.subscribers ? `${a.subscribers} subscribers` : 'On YouTube Music'}</span>`,
      })}
      ${a.songs.length ? actions(list, { label: `songs by ${a.name}` }) : ''}
      ${a.songs.length ? html`<section class="section"><h2 class="section-title mb-3">Popular</h2>
        <div id="artist-songs">${trackTable(a.songs.slice(0, 5), { context: { type: 'artist' } })}</div>
        ${showAll ? html`<button class="pill-btn mt-2" type="button" id="artist-more">Show ${a.songs.length - 5} more</button>` : ''}
      </section>` : emptyState({ icon: 'person', title: 'No songs found for this artist' })}
      ${shelf('Albums', a.albums.map(catalogAlbumCard))}
      ${shelf('Singles & EPs', a.singles.map(catalogAlbumCard))}
      ${shelf('Music videos', a.videos.map((t, i) => videoCard(t, videos, i)))}
      ${shelf('Fans also like', a.related.map(catalogArtistCard))}
      ${a.description ? html`<section class="section"><h2 class="section-title mb-3">About</h2>
        <p class="muted" style="max-width:80ch;white-space:pre-line">${a.description}</p></section>` : ''}
    `,
    mount(root) {
      root.querySelector('#artist-more')?.addEventListener('click', (e) => {
        mount(root.querySelector('#artist-songs'), trackTable(a.songs, { context: { type: 'artist' } }));
        e.currentTarget.remove();
      });
    },
  };
}

export async function catalogAlbumView({ params, signal }) {
  const a = await api.get(`/api/catalog/album/${params[0]}`, null, { signal });
  const list = registerList(a.tracks, { type: 'album' });
  const artistLink = a.artist_browse_id
    ? html`<a href="#/catalog/artist/${a.artist_browse_id}?name=${encodeURIComponent(a.artist_name || '')}">${a.artist_name}</a>`
    : a.artist_name || 'Various artists';
  return {
    title: a.title,
    html: html`
      ${hero({
        art: html`<img src="${thumbUrl(a.thumbnail)}" alt="Cover of ${a.title}">`,
        kind: a.type || 'Album', title: a.title,
        meta: html`<strong>${artistLink}</strong>${a.year ? html`<span class="dot"></span>${a.year}` : ''}
          <span class="dot"></span>${plural(a.tracks.length, 'song')}<span class="muted">, ${fmtLong(a.duration_ms || total(a.tracks))}</span>`,
      })}
      ${a.tracks.length ? actions(list, { label: `songs from ${a.title}` }) : ''}
      ${a.tracks.length ? trackTable(a.tracks, { context: { type: 'album' }, showAlbum: false, showArt: false })
        : emptyState({ icon: 'disc', title: 'This album has no playable songs' })}
      ${a.description ? html`<p class="muted small mt-4" style="max-width:80ch">${a.description}</p>` : ''}
    `,
  };
}

export async function catalogPlaylistView({ params, signal }) {
  const p = await api.get(`/api/catalog/playlist/${params[0]}`, null, { signal });
  const list = registerList(p.tracks, { type: 'playlist' });
  return {
    title: p.title,
    html: html`
      ${hero({
        art: html`<img src="${thumbUrl(p.thumbnail)}" alt="">`,
        kind: 'Playlist', title: p.title, desc: p.description,
        meta: html`<strong>${p.author || 'YouTube Music'}</strong><span class="dot"></span>${plural(p.tracks.length, 'song')}
          <span class="muted">, ${fmtLong(total(p.tracks))}</span>`,
      })}
      ${p.tracks.length ? actions(list, { label: `songs from ${p.title}` }) : ''}
      ${p.tracks.length ? trackTable(p.tracks, { context: { type: 'playlist' } })
        : emptyState({ icon: 'music-note-list', title: 'This playlist is empty' })}
    `,
  };
}

export async function moodView({ query, signal }) {
  const params = query.get('p');
  const title = query.get('t') || 'Playlists';
  const playlists = await api.get('/api/catalog/mood', { params }, { signal });
  return {
    title,
    html: html`
      <h1 class="page-title">${title}</h1>
      ${playlists.length ? html`<div class="grid-cards">${playlists.map(catalogPlaylistCard)}</div>`
        : emptyState({ icon: 'music-note-list', title: 'No playlists here yet' })}
    `,
  };
}
