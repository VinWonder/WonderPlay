// Offline downloads.
//
// A download stores, in Cache Storage (shared with the service worker):
//   wp-media  : the full audio file, keyed by its /api/stream/{id} URL
//   wp-api    : the track's playback manifest (so the player can start offline)
//   wp-img    : its artwork
// plus a metadata record in IndexedDB for the Downloads page.
// The service worker answers the <audio> element's Range requests from the
// cached file, so seeking works offline too.
import { api } from '../api.js';
import { ensureTrack } from '../catalog.js';
import { store } from '../store.js';
import { artUrl, toast } from '../ui.js';
import { idb } from './idb.js';

export const CACHES = { media: 'wp-media-v1', api: 'wp-api-v1', img: 'wp-img-v1' };
const supported = 'caches' in window && 'indexedDB' in window;

const records = new Map();          // id -> record
const active = new Map();           // id -> {received, total}
store.downloaded = new Set();

function publish() {
  store.downloaded = new Set(records.keys());
  store.emit('downloads', { records, active });
}

function tellServiceWorker(msg) {
  navigator.serviceWorker?.controller?.postMessage(msg);
}

async function fetchWithProgress(url, onProgress) {
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok || !res.body) throw new Error(`download failed (HTTP ${res.status})`);
  const total = Number(res.headers.get('content-length')) || 0;
  const reader = res.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onProgress(received, total);
  }
  const type = res.headers.get('content-type') || 'application/octet-stream';
  const blob = new Blob(chunks, { type });
  return new Response(blob, { headers: { 'Content-Type': type, 'Content-Length': String(blob.size), 'Accept-Ranges': 'bytes' } });
}

export const downloads = {
  supported,
  get records() { return records; },
  get active() { return active; },
  has: (id) => records.has(id),
  isActive: (id) => active.has(id),

  async init() {
    if (!supported) return;
    try {
      (await idb.all('downloads')).forEach((r) => records.set(r.id, r));
    } catch (err) {
      console.warn('downloads index unavailable', err);
    }
    publish();
  },

  /** Download one track (library or catalogue) for offline playback. */
  async download(track, { quiet = false, withVideo = store.prefs?.extra?.download_video === true } = {}) {
    if (!supported) { toast('This browser cannot store music offline', 'error'); return false; }
    if (!navigator.onLine) { toast("You're offline", 'error'); return false; }
    await ensureTrack(track);
    const id = track.id;
    if (records.has(id) || active.has(id)) return true;
    active.set(id, { received: 0, total: 0, title: track.title });
    publish();
    try {
      const m = await api.get(`/api/tracks/${id}/playback`);
      const src = m.audio || m.video;
      if (!src) throw new Error('nothing to download');
      const media = await caches.open(CACHES.media);
      const res = await fetchWithProgress(src.url, (received, total) => {
        active.set(id, { received, total, title: track.title });
        store.emit('downloads', { records, active });
      });
      const size = Number(res.headers.get('content-length')) || 0;
      await media.put(src.url, res);
      let videoUrl = null;
      if (withVideo && m.video && m.audio) {
        const vres = await fetchWithProgress(m.video.url, () => {});
        await media.put(m.video.url, vres);
        videoUrl = m.video.url;
      }
      // the manifest + artwork the player needs to start offline
      const apiCache = await caches.open(CACHES.api);
      await apiCache.put(`/api/tracks/${id}/playback`, new Response(JSON.stringify(m), { headers: { 'Content-Type': 'application/json' } }));
      const img = await caches.open(CACHES.img);
      await Promise.all([artUrl(m.track, 300), artUrl(m.track, null)].map((u) => img.add(u).catch(() => {})));

      const t = { ...track, ...m.track };
      const record = {
        id, title: t.title, artist_name: t.artist_name, album_title: t.album_title,
        size, media_url: src.url, video_url: videoUrl, added_at: new Date().toISOString(),
        track: {
          id, title: t.title, artist_name: t.artist_name, artist_id: t.artist_id, album_id: t.album_id,
          album_title: t.album_title, cover_path: t.cover_path, thumbnail: t.thumbnail, duration_ms: t.duration_ms,
          has_audio: !!m.audio, has_video: !!m.video, explicit: t.explicit, is_favorite: t.is_favorite,
          has_synced_lyrics: !!m.lyrics?.synced_lines, video_id: t.video_id, youtube_id: t.youtube_id,
          artist_browse_id: t.artist_browse_id, album_browse_id: t.album_browse_id,
        },
      };
      await idb.put('downloads', record);
      records.set(id, record);
      tellServiceWorker({ type: 'media-added', url: src.url });
      if (!quiet) toast(`Downloaded "${t.title}"`);
      return true;
    } catch (err) {
      if (!quiet) toast(`Download failed: ${err.message}`, 'error');
      return false;
    } finally {
      active.delete(id);
      publish();
    }
  },

  async downloadMany(tracks, label = 'songs') {
    const todo = tracks.filter((t) => !(t.id && records.has(t.id)));
    if (!todo.length) return 0;
    toast(`Downloading ${todo.length} ${label}…`);
    let ok = 0;
    const queue = [...todo];
    const worker = async () => {
      while (queue.length) {
        const t = queue.shift();
        if (await this.download(t, { quiet: true })) ok += 1;
      }
    };
    await Promise.all([worker(), worker()]);   // two at a time
    toast(`${ok} of ${todo.length} ${label} available offline`);
    return ok;
  },

  async remove(id, { quiet = false } = {}) {
    const r = records.get(id);
    if (!r) return;
    const media = await caches.open(CACHES.media);
    await media.delete(r.media_url);
    if (r.video_url) await media.delete(r.video_url);
    await idb.del('downloads', id);
    records.delete(id);
    tellServiceWorker({ type: 'media-removed', url: r.media_url });
    publish();
    if (!quiet) toast(`Removed "${r.title}" from downloads`);
  },

  async removeAll() {
    for (const id of [...records.keys()]) await this.remove(id, { quiet: true });
    toast('All downloads removed');
  },

  totalBytes() {
    let n = 0;
    records.forEach((r) => { n += r.size || 0; });
    return n;
  },

  async storage() {
    const est = navigator.storage?.estimate ? await navigator.storage.estimate() : null;
    const persisted = navigator.storage?.persisted ? await navigator.storage.persisted() : false;
    return { usage: est?.usage || 0, quota: est?.quota || 0, persisted };
  },

  async persist() {
    if (!navigator.storage?.persist) return false;
    return navigator.storage.persist();
  },

  /** Keep "offline" playlists (and Liked Songs, if enabled) fully downloaded. */
  async syncOffline() {
    if (!supported || !navigator.onLine) return;
    try {
      const lists = (store.playlists || []).filter((p) => p.keep_offline);
      for (const p of lists) {
        const pl = await api.get(`/api/playlists/${p.id}`);
        await this.downloadMany(pl.items, `songs from ${p.name}`);
      }
      if (store.prefs?.extra?.offline_liked) {
        const r = await api.get('/api/tracks', { favorites: true, sort: 'liked', limit: 500 });
        await this.downloadMany(r.items, 'liked songs');
      }
    } catch (err) {
      console.warn('offline sync failed', err);
    }
  },
};

export function fmtBytes(n) {
  if (!n) return '0 MB';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(i >= 2 ? 1 : 0)} ${units[i]}`;
}
