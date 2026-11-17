/* WonderPlay service worker.
 *
 * shell  (wp-shell-<ver>) : app code, cache-first; index.html network-first
 * api    (wp-api-v1)      : GET /api/* JSON, network-first -> cache fallback (offline browsing)
 * img    (wp-img-v1)      : covers + catalogue thumbnails, cache-first
 * media  (wp-media-v1)    : downloaded songs (written by the page), served with Range support
 */
const VERSION = '2026.11.17-7';
const SHELL = `wp-shell-${VERSION}`;
const API = 'wp-api-v1';
const IMG = 'wp-img-v1';
const MEDIA = 'wp-media-v1';
const KEEP = new Set([SHELL, API, IMG, MEDIA]);

const SHELL_FILES = [
  '/', '/index.html', '/manifest.webmanifest', '/css/app.css', '/css/theme.css',
  '/vendor/bootstrap/bootstrap.min.css', '/vendor/bootstrap/bootstrap.bundle.min.js',
  '/vendor/bootstrap-icons/bootstrap-icons.min.css', '/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2',
  '/img/logo.svg', '/img/placeholder.svg', '/img/icon-192.png', '/img/icon-512.png',
  '/js/app.js', '/js/login.js', '/js/api.js', '/js/ui.js', '/js/store.js', '/js/router.js', '/js/actions.js', '/js/catalog.js',
  '/js/player/controller.js', '/js/player/np-ui.js', '/js/player/color.js', '/js/player/lyrics.js',
  '/js/player/mediasession.js', '/js/player/visualizer.js',
  '/js/offline/idb.js', '/js/offline/downloads.js', '/js/offline/sync.js',
  '/js/views/home.js', '/js/views/search.js', '/js/views/library.js', '/js/views/collection.js',
  '/js/views/catalog.js', '/js/views/settings.js', '/js/views/import.js', '/js/views/downloads.js',
];

// API responses worth keeping for offline browsing (search/catalogue results are not).
const API_CACHEABLE = /^\/api\/(home|preferences|playlists|tracks|albums|artists|library\/stats|history)(\/|$|\?)/;
const API_MAX = 400;
const IMG_MAX = 800;

// Which /api/stream/... URLs are downloaded. Lets us decide synchronously
// whether to intercept: streams that aren't downloaded go straight to the
// network (no service-worker overhead, best compatibility).
let mediaIndex = null;
const mediaReady = (async () => {
  const c = await caches.open(MEDIA);
  mediaIndex = new Set((await c.keys()).map((r) => new URL(r.url).pathname));
})();

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const c = await caches.open(SHELL);
    await c.addAll(SHELL_FILES.map((u) => new Request(u, { cache: 'reload' })));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const k of await caches.keys()) if (!KEEP.has(k)) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener('message', (event) => {
  const { type, url } = event.data || {};
  if (!mediaIndex || !url) return;
  const path = new URL(url, self.location.origin).pathname;
  if (type === 'media-added') mediaIndex.add(path);
  if (type === 'media-removed') mediaIndex.delete(path);
});

async function trim(cacheName, max) {
  const c = await caches.open(cacheName);
  const keys = await c.keys();
  for (let i = 0; i < keys.length - max; i++) await c.delete(keys[i]);
}

// ------------------------------------------------------------------ media with Range
async function rangeFromCache(request, cached) {
  const blob = await cached.blob();
  const type = cached.headers.get('content-type') || 'audio/mpeg';
  const size = blob.size;
  const range = request.headers.get('range');
  const base = { 'Content-Type': type, 'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store' };
  if (!range) return new Response(blob, { status: 200, headers: { ...base, 'Content-Length': String(size) } });
  const m = /bytes=(\d*)-(\d*)/i.exec(range);
  let start;
  let end;
  if (!m || (m[1] === '' && m[2] === '')) { start = 0; end = size - 1; }
  else if (m[1] === '') { start = Math.max(0, size - Number(m[2])); end = size - 1; }
  else { start = Number(m[1]); end = m[2] === '' ? size - 1 : Math.min(Number(m[2]), size - 1); }
  if (start >= size || end < start) {
    return new Response(null, { status: 416, headers: { ...base, 'Content-Range': `bytes */${size}` } });
  }
  return new Response(blob.slice(start, end + 1), {
    status: 206,
    headers: { ...base, 'Content-Length': String(end - start + 1), 'Content-Range': `bytes ${start}-${end}/${size}` },
  });
}

async function serveMedia(request, path) {
  await mediaReady;
  const c = await caches.open(MEDIA);
  const hit = await c.match(path);
  return hit ? rangeFromCache(request, hit) : fetch(request);
}

// ------------------------------------------------------------------ strategies
async function networkFirst(request, cacheName, { max } = {}) {
  const c = await caches.open(cacheName);
  try {
    const res = await fetch(request);
    if (res.ok && res.status === 200) {
      c.put(request, res.clone()).then(() => max && trim(cacheName, max));
    }
    return res;
  } catch (err) {
    const hit = await c.match(request, { ignoreVary: true });
    if (hit) return hit;
    throw err;
  }
}

async function cacheFirst(request, cacheName, { max } = {}) {
  const c = await caches.open(cacheName);
  const hit = await c.match(request);
  if (hit) return hit;
  const res = await fetch(request);
  if (res.ok) c.put(request, res.clone()).then(() => max && trim(cacheName, max));
  return res;
}

async function offlineApiError() {
  return new Response(JSON.stringify({ detail: "You're offline and this isn't saved on this device." }), {
    status: 503, headers: { 'Content-Type': 'application/json' },
  });
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  const path = url.pathname;

  if (path.startsWith('/api/stream/')) {
    if (mediaIndex && !mediaIndex.has(path)) return;     // not downloaded: let the browser stream it
    event.respondWith(serveMedia(request, path));
    return;
  }
  if (path.startsWith('/api/covers/') || path === '/api/catalog/thumb') {
    event.respondWith(cacheFirst(request, IMG, { max: IMG_MAX }));
    return;
  }
  if (path.startsWith('/api/')) {
    // playback manifests of downloaded songs live in the API cache too
    if (API_CACHEABLE.test(path) || path === '/api/catalog/home') {
      event.respondWith(networkFirst(request, API, { max: API_MAX }).catch(offlineApiError));
    } else {
      event.respondWith(fetch(request).catch(offlineApiError));
    }
    return;
  }
  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        const res = await fetch(request);
        const c = await caches.open(SHELL);
        c.put('/index.html', res.clone());
        return res;
      } catch {
        return (await caches.match('/index.html')) || (await caches.match('/'));
      }
    })());
    return;
  }
  // static app files: cache-first, refresh in the background
  event.respondWith((async () => {
    const c = await caches.open(SHELL);
    const hit = await c.match(request, { ignoreSearch: true });
    const fresh = fetch(request).then((res) => {
      if (res.ok) c.put(request, res.clone());
      return res;
    }).catch(() => null);
    return hit || (await fresh) || new Response('Offline', { status: 503 });
  })());
});
