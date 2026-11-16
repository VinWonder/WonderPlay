import { api, coverUrl } from '../api.js';
import { store } from '../store.js';
import {
  albumCard, catalogAlbumCard, catalogArtistCard, catalogPlaylistCard, fmtTime, greeting, hero, html, markPlaying, mount,
  playlistCard, plural, registerList, shelf, trackCard, trackTable, videoCard,
} from '../ui.js';

/** Stable hue per genre name so tiles keep their colour. */
export function hueOf(name) {
  let h = 0;
  for (const ch of String(name).toLowerCase()) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

function onboarding() {
  return html`
    <h1 class="page-title">${greeting()}</h1>
    <section class="spotlight">
      <div class="spotlight-bg" style="background:radial-gradient(circle at 30% 30%, rgba(var(--accent-rgb),.55), transparent 60%), #151826"></div>
      <div class="spotlight-text">
        <div class="eyebrow">Let's get started</div>
        <h2 class="spotlight-title">Your music, your way.</h2>
        <div class="spotlight-meta muted">No ads, no limits. Search any song, or bring your own files.</div>
        <div class="spotlight-actions"><a class="pill-btn pill-btn-primary" href="#/search"><i class="bi bi-search"></i> Search all music</a></div>
      </div>
    </section>
    <div class="onboard">
      <a href="#/settings"><i class="bi bi-folder2-open"></i><h3>Add a music folder</h3>
        <p>Point WonderPlay at the folders on this computer that hold your MP3, FLAC, M4A or MP4 files.</p></a>
      <a href="#/search"><i class="bi bi-search"></i><h3>Play anything</h3>
        <p>Search millions of songs, albums and music videos. Like a song to save it to your library.</p></a>
      <a href="#/library/playlists"><i class="bi bi-music-note-list"></i><h3>Build a playlist</h3>
        <p>Collect your favourites into playlists and keep them on hand for offline listening.</p></a>
    </div>
    ${recsPlaceholder()}`;
}

// ---------------------------------------------------------------- recommendations
function recsPlaceholder() {
  return html`<div id="recs" class="mt-4">
    ${[1, 2].map(() => html`<section class="section"><div class="skel skel-line" style="width:240px;height:22px"></div>
      <div class="shelf mt-3">${Array.from({ length: 6 }, () => html`<div><div class="skel skel-card"></div>
        <div class="skel skel-line" style="width:70%"></div></div>`)}</div></section>`)}</div>`;
}

function recSection(sec) {
  if (sec.kind === 'tracks') {
    const list = registerList(sec.tracks, { type: 'radio' });
    return shelf(sec.title, sec.tracks.map((t, i) => trackCard(t, list, i)));
  }
  if (sec.kind === 'albums') return shelf(sec.title, sec.albums.map(catalogAlbumCard));
  if (sec.kind === 'playlists') return shelf(sec.title, sec.playlists.map(catalogPlaylistCard));
  if (sec.kind === 'artists') return shelf(sec.title, sec.artists.map(catalogArtistCard));
  return '';
}

function loadRecs(root, ctx) {
  const box = root.querySelector('#recs');
  if (!box) return;
  api.get('/api/catalog/home', null, { signal: ctx.signal })
    .then((r) => {
      if (!ctx.isCurrent()) return;
      mount(box, html`${r.sections.map(recSection)}`);
      markPlaying(box);
    })
    .catch((err) => {
      if (err.name === 'AbortError' || !ctx.isCurrent()) return;
      mount(box, navigator.onLine ? html`<p class="muted small"><i class="bi bi-wifi-off"></i> Recommendations are unavailable right now.</p>` : '');
    });
}

function spotlight(t, recent, more) {
  // Queue the featured song first, then the rest of the shelf, so Next keeps playing.
  const list = registerList([t, ...more.filter((x) => x.id !== t.id)], { type: 'library' });
  const meta = [];
  if (t.album_title && t.album_title !== t.title) meta.push(html`<span class="dot"></span><a href="#/album/${t.album_id}">${t.album_title}</a>`);
  if (t.play_count) meta.push(html`<span class="dot"></span><span class="muted">${plural(t.play_count, 'play')}</span>`);
  return html`
    <section class="spotlight">
      <div class="spotlight-bg" style="background-image:url('${coverUrl(t.cover_path, null)}')"></div>
      <div class="spotlight-text">
        <div class="eyebrow">${recent ? 'Jump back in' : 'New in your library'}</div>
        <h2 class="spotlight-title" title="${t.title}">${t.title}</h2>
        <div class="spotlight-meta">
          <strong>${t.artist_id ? html`<a href="#/artist/${t.artist_id}">${t.artist_name}</a>` : t.artist_name || 'Unknown artist'}</strong>${meta}
        </div>
        <div class="spotlight-actions">
          <button class="pill-btn pill-btn-primary" type="button" data-action="play-list" data-list="${list}" data-index="0">
            <i class="bi bi-play-fill"></i> Listen now</button>
          ${t.has_video ? html`<button class="pill-btn" type="button" data-action="play-list" data-mode="video" data-list="${list}" data-index="0">
            <i class="bi bi-camera-video"></i> Watch video</button>` : ''}
          <button class="circle-btn ${t.is_favorite ? 'liked' : ''}" type="button" data-action="like" data-track-id="${t.id}" data-like-id="${t.id}"
            aria-label="${t.is_favorite ? 'Remove from Liked Songs' : 'Save to Liked Songs'}"><i class="bi ${t.is_favorite ? 'bi-heart-fill' : 'bi-heart'}"></i></button>
        </div>
      </div>
      <div class="spotlight-art d-none d-sm-block"><img src="${coverUrl(t.cover_path, null)}" alt=""></div>
    </section>`;
}

function chartList(tracks) {
  const list = registerList(tracks, { type: 'library' });
  return html`<div class="chart-list">${tracks.map((t, i) => html`
    <div class="chart-item" data-action="play-list" data-list="${list}" data-index="${i}" data-track-id="${t.id}" role="button" tabindex="0">
      <span class="rank">${String(i + 1).padStart(2, '0')}</span>
      <img src="${coverUrl(t.cover_path)}" alt="" loading="lazy">
      <div class="ti"><strong>${t.title}</strong><span>${t.artist_name || ''}</span></div>
      <span class="dur">${fmtTime(t.duration_ms)}</span>
      <button class="mini-play" type="button" data-action="play-list" data-list="${list}" data-index="${i}" aria-label="Play ${t.title}">
        <i class="bi bi-play-fill"></i></button>
    </div>`)}</div>`;
}

export async function homeView({ signal }) {
  const d = await api.get('/api/home', null, { signal });
  if (!d.recently_added.length && !d.recently_played.length) return { title: 'Home', html: onboarding(), mount: loadRecs };

  const featured = d.recently_played[0] || d.recently_added[0];
  const added = d.recently_added;

  // Quick picks: Liked Songs, playlists, then recent songs (Spotify-style tiles).
  const quick = [];
  if (store.likedCount) {
    quick.push(html`<a class="quick-tile" href="#/liked"><span class="lib-art liked-art"><i class="bi bi-heart-fill"></i></span>
      <span class="t">Liked Songs</span>
      <button class="fab-play" type="button" data-action="play-liked" aria-label="Play Liked Songs"><i class="bi bi-play-fill"></i></button></a>`);
  }
  for (const p of store.playlists.slice(0, 3)) {
    quick.push(html`<a class="quick-tile" href="#/playlist/${p.id}"><img src="${coverUrl(p.cover_path || p.mosaic?.[0])}" alt="">
      <span class="t">${p.name}</span>
      <button class="fab-play" type="button" data-action="play-playlist" data-id="${p.id}" aria-label="Play ${p.name}"><i class="bi bi-play-fill"></i></button></a>`);
  }
  const recentList = registerList(d.recently_played, { type: 'library' });
  d.recently_played.slice(0, Math.max(0, 6 - quick.length)).forEach((t, i) => {
    quick.push(html`<div class="quick-tile" role="button" tabindex="0" data-action="play-list" data-list="${recentList}" data-index="${i}">
      <img src="${coverUrl(t.cover_path)}" alt=""><span class="t">${t.title}</span>
      <button class="fab-play" type="button" data-action="play-list" data-list="${recentList}" data-index="${i}" aria-label="Play ${t.title}"><i class="bi bi-play-fill"></i></button></div>`);
  });

  const charts = (d.most_played.length >= 3 ? d.most_played : (d.recently_added.length ? d.recently_added : d.recently_played)).slice(0, 6);
  const addedList = registerList(d.recently_added);
  const videoList = registerList(d.music_videos);

  return {
    title: 'Home',
    mount: loadRecs,
    html: html`
      <h1 class="page-title">${greeting()}</h1>
      ${spotlight(featured, !!d.recently_played.length, [...d.recently_played, ...d.recently_added])}
      ${quick.length ? html`<div class="quick-grid">${quick}</div>` : ''}
      ${recsPlaceholder()}

      <div class="panel-grid">
        <section class="card-panel">
          <div class="section-head"><h2 class="section-title">${d.most_played.length >= 3 ? 'Top charts' : 'Fresh picks'}</h2>
            <a class="section-link" href="#/library/songs?sort=${d.most_played.length >= 3 ? 'plays' : 'recent'}">See all</a></div>
          ${chartList(charts)}
        </section>
        <div class="d-flex flex-column gap-3 min-w-0">
          ${d.top_artists.length ? html`<section class="card-panel">
            <div class="section-head"><h2 class="section-title">Top artists</h2><a class="section-link" href="#/library/artists">See all</a></div>
            <div class="avatar-row">${d.top_artists.map((a) => html`
              <a class="avatar" href="#/artist/${a.id}">
                <div class="avatar-img"><img src="${coverUrl(a.image_path || a.cover_path)}" alt="" loading="lazy"></div>
                <strong>${a.name}</strong><small>${a.plays ? plural(a.plays, 'play') : plural(a.track_count, 'song')}</small></a>`)}
            </div></section>` : ''}
          ${d.genres.length ? html`<section class="card-panel">
            <div class="section-head"><h2 class="section-title">Genres</h2></div>
            <div class="genre-grid">${d.genres.map((g) => html`
              <a class="genre-tile" href="#/genre/${encodeURIComponent(g.name)}" style="--h:${hueOf(g.name)}">${g.name}
                <small>${plural(g.track_count, 'song')}</small>
                ${g.cover_path ? html`<img src="${coverUrl(g.cover_path)}" alt="" loading="lazy">` : ''}</a>`)}
            </div></section>` : ''}
        </div>
      </div>

      ${shelf('Recently added to your library', added.map((t, i) => trackCard(t, addedList, i)), '#/library/songs')}
      ${shelf('Music videos', d.music_videos.map((t, i) => videoCard(t, videoList, i)), '#/videos')}
      ${shelf('Your playlists', store.playlists.slice(0, 12).map(playlistCard), '#/library/playlists')}
      ${shelf('Albums', d.new_albums.map(albumCard), '#/library/albums')}
    `,
  };
}

export async function genreView({ params, signal }) {
  const name = params[0];
  const r = await api.get('/api/tracks', { genre: name, sort: 'plays', limit: 500 }, { signal });
  const list = registerList(r.items, { type: 'library' });
  return {
    title: name,
    html: html`
      ${hero({
        art: html`<div class="w-100 h-100 d-grid" style="place-items:center;background:hsl(${hueOf(name)} 38% 34%)"><i class="bi bi-vinyl-fill" style="font-size:80px;opacity:.75"></i></div>`,
        kind: 'Genre', title: name, meta: html`<span class="muted">${plural(r.total, 'song')} in your library</span>`,
      })}
      <div class="action-row">
        <button class="big-play" type="button" data-action="play-list" data-list="${list}" data-index="0" aria-label="Play"><i class="bi bi-play-fill"></i></button>
        <button class="icon-btn" type="button" data-action="shuffle-list" data-list="${list}" aria-label="Shuffle play"><i class="bi bi-shuffle"></i></button>
      </div>
      ${trackTable(r.items, { context: { type: 'library' } })}`,
  };
}
