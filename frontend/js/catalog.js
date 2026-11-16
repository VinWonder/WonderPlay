// Catalogue helpers: make YouTube Music items playable / saveable on demand.
import { api } from './api.js';

const inflight = new Map();

/**
 * Catalogue tracks arrive without a library id. The first time one is played,
 * liked, queued for download or added to a playlist, the server "resolves" it
 * (yt-dlp) into a stored track. The object is updated in place so every list
 * holding it sees the id.
 */
export async function ensureTrack(t) {
  if (t.id) return t;
  if (!t.video_id) throw new Error('This item cannot be played');
  let p = inflight.get(t.video_id);
  if (!p) {
    p = api.post('/api/catalog/resolve', {
      video_id: t.video_id,
      title: t.title,
      artists: t.artists || [],
      album_title: t.album_title || null,
      thumbnail: t.thumbnail || null,
      duration_ms: t.duration_ms || null,
      video_type: t.video_type || 'song',
      explicit: !!t.explicit,
    }).finally(() => inflight.delete(t.video_id));
    inflight.set(t.video_id, p);
  }
  const row = await p;
  // Keep catalogue links (artist/album browse ids) but take library identity + art.
  for (const k of ['id', 'cover_path', 'dominant_color', 'album_id', 'artist_id', 'is_favorite', 'in_library',
    'has_synced_lyrics', 'youtube_id']) {
    if (row[k] !== undefined && row[k] !== null) t[k] = row[k];
  }
  t.has_video = !!(t.has_video || row.has_video);
  t.has_audio = true;
  return t;
}

export async function ensureTracks(tracks) {
  return Promise.all(tracks.map(ensureTrack));
}

/** Similar songs ("song radio") for a library or catalogue track. */
export async function radioFor(t) {
  const params = t.video_id || t.youtube_id ? { video_id: t.video_id || t.youtube_id } : { track_id: t.id };
  const r = await api.get('/api/catalog/radio', params);
  return r.tracks;
}

export const trackKey = (t) => (t.id ? `t${t.id}` : `v${t.video_id}`);
