// Small shared state + pub/sub used across views.
import { api } from './api.js';

const listeners = new Map();

export const store = {
  playlists: [],
  likedCount: 0,
  currentTrackId: null,
  prefs: null,

  on(event, fn) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(fn);
    return () => listeners.get(event).delete(fn);
  },
  emit(event, detail) {
    listeners.get(event)?.forEach((fn) => fn(detail));
  },

  async refreshPlaylists() {
    const [playlists, liked] = await Promise.all([
      api.get('/api/playlists'),
      api.get('/api/tracks', { favorites: true, limit: 1 }),
    ]);
    this.playlists = playlists;
    this.likedCount = liked.total;
    this.emit('playlists', playlists);
    return playlists;
  },
};
