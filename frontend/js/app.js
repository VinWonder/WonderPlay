// WonderPlay entry point.
import { api, coverUrl } from './api.js';
import './actions.js';
import { player } from './player/controller.js';
import { initNowPlaying } from './player/np-ui.js';
import { currentPath, navigate, route, startRouter } from './router.js';
import { store } from './store.js';
import { esc, toast } from './ui.js';
import { checkAuth, logout, showLogin } from './login.js';
import { registerAction } from './actions.js';
import { downloads } from './offline/downloads.js';
import { initOfflineSync } from './offline/sync.js';
import { catalogAlbumView, catalogArtistView, catalogPlaylistView, moodView } from './views/catalog.js';
import { albumView, artistView, likedView, playlistView } from './views/collection.js';
import { downloadsView } from './views/downloads.js';
import { genreView, homeView } from './views/home.js';
import { importView } from './views/import.js';
import { libraryView, videosView } from './views/library.js';
import { initSearchBox, searchView } from './views/search.js';
import { settingsView } from './views/settings.js';

// ---------------------------------------------------------------- routes
route(/^\/$/, homeView, { nav: 'home' });
route(/^\/search(?:\/(.+))?$/, searchView, { nav: 'search' });
route(/^\/library(?:\/(\w+))?$/, libraryView, { nav: (m) => (m[1] ? ['library', `library-${m[1]}`] : ['library', 'library-playlists']), skeletonKind: 'grid' });
route(/^\/album\/(\d+)$/, albumView);
route(/^\/artist\/(\d+)$/, artistView);
route(/^\/playlist\/(\d+)$/, playlistView, { nav: (m) => `playlist-${m[1]}` });
route(/^\/liked$/, likedView, { nav: 'liked' });
route(/^\/genre\/(.+)$/, genreView);
route(/^\/videos$/, videosView, { nav: 'videos', skeletonKind: 'grid' });
route(/^\/import$/, importView, { nav: 'import' });
route(/^\/settings$/, settingsView, { nav: 'settings' });
route(/^\/downloads$/, downloadsView, { nav: 'downloads' });
route(/^\/catalog\/artist\/([\w-]+)$/, catalogArtistView);
route(/^\/catalog\/album\/([\w-]+)$/, catalogAlbumView);
route(/^\/catalog\/playlist\/([\w-]+)$/, catalogPlaylistView);
route(/^\/mood$/, moodView, { nav: 'search' });

// ---------------------------------------------------------------- sidebar playlists
function renderSidebar(playlists) {
  const box = document.getElementById('sidebar-playlists');
  document.getElementById('liked-count').textContent = store.likedCount ? store.likedCount.toLocaleString() : '';
  const active = currentPath();
  box.innerHTML = playlists.length
    ? playlists.map((p) => {
      const art = p.cover_path || p.mosaic?.[0];
      return `<a href="#/playlist/${p.id}" data-nav="playlist-${p.id}" class="${active === `/playlist/${p.id}` ? 'active' : ''}" title="${esc(p.name)}">
          <span class="pl-thumb">${art ? `<img src="${coverUrl(art)}" alt="" loading="lazy">` : '<i class="bi bi-music-note-list"></i>'}</span>
          <span>${esc(p.name)}<small>${p.is_pinned ? '<i class="bi bi-pin-angle-fill pin"></i> ' : ''}${p.track_count} songs</small></span>
        </a>`;
    }).join('')
    : '<p class="muted small px-3 mb-0">Create a playlist with the + button.</p>';
}
store.on('playlists', renderSidebar);

// Broken artwork (e.g. a remote thumbnail that vanished) falls back to the placeholder.
document.addEventListener('error', (e) => {
  const img = e.target;
  if (img?.tagName === 'IMG' && !img.dataset.fallback) {
    img.dataset.fallback = '1';
    img.src = 'img/placeholder.svg';
  }
}, true);

// ---------------------------------------------------------------- top bar
const main = document.getElementById('main');
const topbar = document.getElementById('topbar');
main.addEventListener('scroll', () => topbar.classList.toggle('scrolled', main.scrollTop > 30), { passive: true });

// ---------------------------------------------------------------- boot
// ---------------------------------------------------------------- service worker (offline / installable)
function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  const hadController = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.register('/sw.js').catch((err) => console.warn('Service worker registration failed', err));
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (hadController) toast('WonderPlay was updated. Reload to use the new version.', 'info', 8000);
  });
}

window.addEventListener('wp:unauthorized', () => showLogin());
registerAction('logout', () => logout());

async function boot() {
  registerServiceWorker();
  if (!(await checkAuth())) return;   // sign-in screen is showing
  initOfflineSync();
  await downloads.init();
  initSearchBox(navigate);
  let prefs = null;
  try {
    [prefs] = await Promise.all([api.get('/api/preferences'), store.refreshPlaylists()]);
    store.prefs = prefs;
  } catch (err) {
    toast(err.message, 'error', 6000);
  }
  initNowPlaying(prefs);
  startRouter();
  player.restore(prefs);
  // keep "offline" playlists / liked songs downloaded (quietly, after startup)
  setTimeout(() => downloads.syncOffline(), 5000);
  window.addEventListener('online', () => downloads.syncOffline());
}

boot();
