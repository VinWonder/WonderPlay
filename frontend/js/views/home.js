// Home: Spotify-style — filter chips, quick tiles, Recents, "More like…",
// radio stations, mixes and recommendations.
import { api, coverUrl } from '../api.js';
import { store } from '../store.js';
import {
  albumCard, artUrl, catalogAlbumCard, catalogArtistCard, catalogPlaylistCard, greeting, hero, html, markPlaying,
  mount, playlistCard, plural, registerList, shelf, trackCard, trackTable, videoCard,
} from '../ui.js';

/** Stable hue per name so tiles/stations keep their colour. */
export function hueOf(name) {
  let h = 0;
  for (const ch of String(name).toLowerCase()) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

const CHIPS = [['all', 'All'], ['music', 'Music'], ['videos', 'Videos']];

function homeHead() {
  return html`
    <div class="home-head">
      <a class="home-avatar" href="#/settings" aria-label="Settings">W</a>
      <div class="chips" role="tablist" aria-label="Filter">
        ${CHIPS.map(([k, label], i) => html`<button type="button" class="chip ${i === 0 ? 'active' : ''}" data-filter="${k}"
          role="tab" aria-selected="${i === 0 ? 'true' : 'false'}">${label}</button>`)}
        <a class="chip" href="#/downloads">Downloaded</a>
      </div>
    </div>`;
}

const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

function installCard() {
  let dismissed = false;
  try { dismissed = !!localStorage.getItem('wp.installDismissed'); } catch { /* private mode */ }
  if (isStandalone() || dismissed) return '';
  return html`
    <div class="install-promo">
      <img src="img/icon-192.png" alt="" width="44" height="44">
      <div class="min-w-0"><strong>Get the WonderPlay app</strong><span>Full screen, lock-screen controls, offline music</span></div>
      <button class="pill-btn pill-btn-primary" type="button" data-action="install-app">Install</button>
      <button class="icon-btn" type="button" data-action="dismiss-install" aria-label="Dismiss"><i class="bi bi-x-lg"></i></button>
    </div>`;
}

// ---------------------------------------------------------------- recommendations (YouTube Music)
function recsPlaceholder() {
  return html`<div id="recs" data-kind="music">
    ${[1, 2].map(() => html`<section class="section"><div class="skel skel-line" style="width:220px;height:22px"></div>
      <div class="shelf mt-3">${Array.from({ length: 6 }, () => html`<div><div class="skel skel-card"></div>
        <div class="skel skel-line" style="width:70%"></div></div>`)}</div></section>`)}</div>`;
}

function shelfWithHead(head, items) {
  if (!items?.length) return '';
  return html`<section class="section">${head}<div class="shelf">${items}</div></section>`;
}

function recSection(sec) {
  if (sec.kind === 'tracks') {
    const list = registerList(sec.tracks, { type: 'radio' });
    const cards = sec.tracks.map((t, i) => trackCard(t, list, i));
    // "More from X" / "Because you listened to X": Spotify-style header with a picture
    const m = /^(More from|Because you listened to) (.+)$/.exec(sec.title);
    if (m) {
      const pic = sec.tracks[0];
      return shelfWithHead(html`<div class="shelf-artist-head">
          <img src="${artUrl(pic)}" alt="" loading="lazy">
          <div><small>${m[1] === 'More from' ? 'More like' : 'Because you listened to'}</small><strong>${m[2]}</strong></div></div>`, cards);
    }
    return shelf(sec.title, cards);
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

// ---------------------------------------------------------------- radio stations (one per recent artist)
function stations(tracks) {
  const seen = new Set();
  const seeds = [];
  for (const t of tracks) {
    const key = (t.artist_name || '').toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    seeds.push(t);
    if (seeds.length >= 8) break;
  }
  if (!seeds.length) return '';
  const list = registerList(seeds, { type: 'radio' });
  const byArtist = (name) => tracks.filter((x) => x.artist_name === name && x.cover_path);
  const cards = seeds.map((t, i) => {
    const pics = [t, ...byArtist(t.artist_name).filter((x) => x.id !== t.id)].slice(0, 2);
    return html`
      <div class="card-tile" data-action="radio-list" data-list="${list}" data-index="${i}" role="button" tabindex="0"
           aria-label="Start ${t.artist_name} radio">
        <div class="station-art" style="--h:${hueOf(t.artist_name)}">
          <svg class="logo" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="16" fill="currentColor"/>
            <path d="M8.5 11.5v9M12.5 9v14M16.5 13v6M20.5 10v12M24.5 12.5v7" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/></svg>
          <span class="tag">RADIO</span>
          <div class="faces">${pics.map((p) => html`<img src="${artUrl(p)}" alt="" loading="lazy">`)}</div>
          <span class="name">${t.artist_name}</span>
        </div>
        <span class="card-title">${t.artist_name} Radio</span>
        <div class="card-sub">Based on ${t.title}</div>
      </div>`;
  });
  return shelf('Recommended stations', cards);
}

// ---------------------------------------------------------------- page
export async function homeView({ signal }) {
  const d = await api.get('/api/home', null, { signal });
  const played = d.recently_played;
  const added = d.recently_added;

  // Quick tiles: Liked Songs, playlists, recent songs (2 x 4 like Spotify)
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
  const recentList = registerList(played, { type: 'library' });
  played.slice(0, Math.max(0, 8 - quick.length)).forEach((t, i) => {
    quick.push(html`<div class="quick-tile" role="button" tabindex="0" data-action="play-list" data-list="${recentList}" data-index="${i}">
      <img src="${coverUrl(t.cover_path)}" alt=""><span class="t">${t.title}</span>
      <button class="fab-play" type="button" data-action="play-list" data-list="${recentList}" data-index="${i}" aria-label="Play ${t.title}"><i class="bi bi-play-fill"></i></button></div>`);
  });

  const recentCards = registerList(played, { type: 'library' });
  const addedList = registerList(added, { type: 'library' });
  const videoList = registerList(d.music_videos, { type: 'library' });
  const seedTracks = [...played, ...added];

  return {
    title: 'Home',
    mount(root, ctx) {
      loadRecs(root, ctx);
      // filter chips: All / Music / Videos
      root.querySelector('.home-head .chips')?.addEventListener('click', (e) => {
        const chip = e.target.closest('[data-filter]');
        if (!chip) return;
        const f = chip.dataset.filter;
        root.querySelectorAll('[data-filter]').forEach((c) => {
          c.classList.toggle('active', c === chip);
          c.setAttribute('aria-selected', c === chip ? 'true' : 'false');
        });
        root.querySelectorAll('[data-kind]').forEach((sec) => {
          sec.hidden = f !== 'all' && sec.dataset.kind !== f;
        });
      });
    },
    html: html`
      ${homeHead()}
      <h1 class="page-title home-greeting">${greeting()}</h1>
      ${installCard()}
      <div data-kind="music">
        ${quick.length ? html`<div class="quick-grid">${quick}</div>` : ''}
        ${shelf('Recents', played.map((t, i) => trackCard(t, recentCards, i)))}
      </div>
      ${recsPlaceholder()}
      <div data-kind="music">
        ${stations(seedTracks)}
        ${shelf('Jump back in', d.new_albums.map(albumCard), '#/library/albums')}
        ${d.top_artists.length ? shelf('Your top artists', d.top_artists.map((a) => html`
          <a class="card-tile" href="#/artist/${a.id}">
            <div class="card-art round"><img loading="lazy" src="${coverUrl(a.image_path || a.cover_path)}" alt=""></div>
            <span class="card-title">${a.name}</span><div class="card-sub">Artist</div></a>`), '#/library/artists') : ''}
        ${shelf('Recently added to your library', added.map((t, i) => trackCard(t, addedList, i)), '#/library/songs')}
        ${shelf('Your playlists', store.playlists.slice(0, 12).map(playlistCard), '#/library/playlists')}
      </div>
      <div data-kind="videos">
        ${shelf('Music videos', d.music_videos.map((t, i) => videoCard(t, videoList, i)), '#/videos')}
      </div>
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

