// WonderPlay media controller.
//
// Song/Video architecture: the <audio> element is the "master clock" and
// carries the sound. In Video mode a muted <video> plays the music video and is
// continuously re-synced to the audio (video_pos = audio_pos + sync_offset_ms),
// so switching modes never interrupts the sound. Tracks that only have a video
// rendition use the <video> element as master with sound instead.
import { api } from '../api.js';
import { store } from '../store.js';
import { toast } from '../ui.js';
import { ensureTrack, radioFor } from '../catalog.js';
import * as viz from './visualizer.js';
import { setMetadata, setPlaybackState, setPosition } from './mediasession.js';

const audio = document.getElementById('audio');
const video = document.getElementById('video');

const state = {
  queue: [],            // tracks in play order
  original: null,       // un-shuffled order (to restore when shuffle is turned off)
  index: -1,
  context: null,        // {type, id} where playback started (album, playlist, ...)
  shuffle: false,
  repeat: 'off',        // off | all | one
  mode: 'audio',        // desired mode; effective video also needs a video source
  manifest: null,
  master: audio,
  volume: 0.8,
  muted: false,
  normalize: true,
  errors: 0,
  loadToken: 0,
  session: null,
  pendingSeek: null,
};

const emit = (ev, detail) => store.emit(`player:${ev}`, detail);
const manifestCache = new Map();
const MANIFEST_TTL = 5 * 60 * 1000;

async function getManifest(trackId, { fresh = false } = {}) {
  const hit = manifestCache.get(trackId);
  if (!fresh && hit && Date.now() - hit.at < MANIFEST_TTL) return hit.m;
  const m = await api.get(`/api/tracks/${trackId}/playback`);
  manifestCache.set(trackId, { m, at: Date.now() });
  if (manifestCache.size > 50) manifestCache.delete(manifestCache.keys().next().value);
  return m;
}

const videoActive = () => state.master === video || (state.mode === 'video' && !!state.manifest?.video);
const syncOffset = () => (state.manifest?.video?.sync_offset_ms || 0) / 1000;

function shuffled(arr, first) {
  const a = arr.filter((t) => t !== first);
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return first ? [first, ...a] : a;
}

// ---------------------------------------------------------------- volume & loudness
function gainFactor() {
  if (!state.normalize) return 1;
  const db = Number(state.manifest?.track?.replaygain_track_db);
  return Number.isFinite(db) ? Math.min(1, 10 ** (db / 20)) : 1;
}

function applyVolume() {
  const v = Math.max(0, Math.min(1, state.volume * gainFactor()));
  audio.volume = v;
  audio.muted = state.muted;
  if (state.master === video) { video.volume = v; video.muted = state.muted; }
  else video.muted = true;
  emit('volume', { volume: state.volume, muted: state.muted });
}

// ---------------------------------------------------------------- play history
function startSession(track) {
  state.session = {
    track_id: track.id,
    source_id: (state.master === video ? state.manifest?.video?.id : state.manifest?.audio?.id) ?? null,
    mode: videoActive() ? 'video' : 'audio',
    context_type: ['library', 'album', 'artist', 'playlist', 'search', 'queue', 'radio'].includes(state.context?.type) ? state.context.type : 'library',
    context_id: state.context?.id ?? null,
    started_at: new Date().toISOString(),
    played_ms: 0,
    lastPos: null,
  };
}

function trackPlayed() {
  const s = state.session;
  if (!s || state.master.paused) return;
  const pos = state.master.currentTime;
  if (s.lastPos != null) {
    const d = pos - s.lastPos;
    if (d > 0 && d < 1.5) s.played_ms += d * 1000;
  }
  s.lastPos = pos;
}

function endSession(reason) {
  const s = state.session;
  state.session = null;
  if (!s) return;
  const completed = reason === 'ended';
  if (!completed && s.played_ms < 1000) return;   // ignore accidental taps
  const dur = (state.master.duration || 0) * 1000;
  const skipped = !completed && reason === 'skip' && (!dur || s.played_ms < dur * 0.5);
  const body = { ...s, played_ms: Math.round(s.played_ms), ended_at: new Date().toISOString(), completed, skipped,
    device: navigator.userAgentData?.platform || navigator.platform || null };
  delete body.lastPos;
  fetch('/api/history', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), keepalive: true })
    .catch(() => store.emit('history:failed', body));   // Phase 5 queues these for offline sync
}

// ---------------------------------------------------------------- resume state
let saveTimer = 0;
function saveState({ now = false } = {}) {
  clearTimeout(saveTimer);
  const run = () => {
    const t = state.queue[state.index];
    if (!t || !navigator.onLine) return;   // offline: nothing to sync it to (avoids failed requests)
    // keep at most 200 entries around the current one
    if (!t.id) return;
    const start = Math.max(0, state.index - 50);
    const window = state.queue.slice(start, start + 200).filter((x) => x.id);
    const body = {
      last_track_id: t.id,
      last_position_ms: Math.round((state.master.currentTime || 0) * 1000),
      queue_track_ids: window.map((x) => x.id),
      queue_index: Math.max(0, window.indexOf(t)),
    };
    fetch('/api/preferences', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), keepalive: true }).catch(() => {});
  };
  if (now) run(); else saveTimer = setTimeout(run, 1500);
}

// ---------------------------------------------------------------- loading
function resetVideo() {
  video.pause();
  if (video.getAttribute('src')) { video.removeAttribute('src'); video.load(); }
}

function setVideoSrc() {
  const src = state.manifest?.video;
  if (!src || !videoActive()) { if (state.master !== video) resetVideo(); return; }
  if (video.getAttribute('src') !== src.url) {
    video.src = src.url;
    video.load();
    // Remote videos need longer to buffer after a seek than local files.
    seekLatency = src.origin === 'remote' ? 0.9 : 0.15;
  }
}

async function load(index, { autoplay = true, startMs = 0, reason = 'replaced' } = {}) {
  const t = state.queue[index];
  if (!t) return;
  // Offline: only downloaded songs can play; skip ahead to the next one that is.
  if (!navigator.onLine && !(t.id && store.downloaded?.has(t.id))) {
    const j = state.queue.findIndex((x, k) => k > index && x.id && store.downloaded?.has(x.id));
    if (j > -1) {
      toast(`Skipping "${t.title}": not downloaded`);
      load(j, { autoplay, reason });
    } else {
      endSession(reason);   // still log the song that just finished (queued for sync)
      toast("You're offline. Download songs to play them without a connection.", 'error', 5000);
    }
    return;
  }
  endSession(reason);
  const token = ++state.loadToken;
  state.index = index;
  state.manifest = null;
  store.currentTrackId = t.id ?? null;
  store.currentVideoId = t.video_id || t.youtube_id || null;
  emit('track', t);
  emit('loading', true);
  try {
    if (!t.id) {
      // Catalogue song: the server fetches it from YouTube on first play.
      emit('preparing', true);
      try { await ensureTrack(t); } finally { emit('preparing', false); }
      if (token !== state.loadToken) return;
      store.currentTrackId = t.id;
      emit('track', t);
    }
    const m = await getManifest(t.id);
    if (token !== state.loadToken) return;
    state.manifest = m;
    // keep queue entry fresh (likes, cover) for the UI
    Object.assign(t, m.track);

    const playable = m.audio || m.video;
    if (!playable) throw new Error('no playable source');
    state.master = m.audio ? audio : video;

    if (m.audio) {
      if (audio.getAttribute('src') !== m.audio.url) audio.src = m.audio.url;
      else if (audio.readyState >= 1) audio.currentTime = startMs / 1000;   // same file already loaded: restart it
    } else {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    }
    if (state.master === video) {
      if (video.getAttribute('src') !== m.video.url) { video.src = m.video.url; video.load(); }
    } else {
      setVideoSrc();
    }
    applyVolume();
    state.pendingSeek = startMs > 0 ? startMs / 1000 : null;
    if (state.pendingSeek != null && state.master.readyState >= 1) {
      state.master.currentTime = state.pendingSeek;
      state.pendingSeek = null;
    }

    setMetadata(m.track);
    emit('manifest', m);
    // A just-resolved catalogue song gets its music video + lyrics attached in
    // the background; pick them up when they land.
    if (!m.video || !m.lyrics) {
      const id = t.id;
      for (const delay of [12000, 30000]) {
        setTimeout(() => { if (player.current?.id === id && (!state.manifest?.video || !state.manifest?.lyrics)) player.reloadManifest().catch(() => {}); }, delay);
      }
    }
    emit('mode', effectiveMode());
    startSession(m.track);
    saveState();
    if (autoplay) await play();
    prefetchNext();
  } catch (err) {
    if (token !== state.loadToken) return;
    emit('loading', false);
    if (err.name === 'NotAllowedError') return;            // autoplay blocked; user presses play
    toast(`Can't play "${t.title}": ${err.message}`, 'error');
    skipAfterError();
  }
}

function prefetchNext() {
  const n = state.queue[state.index + 1];
  if (!n) return;
  // Catalogue songs take seconds to fetch from YouTube: do it now, not when the
  // current song ends, so the next one starts instantly.
  ensureTrack(n).then(() => getManifest(n.id)).catch(() => {});
}

// ---------------------------------------------------------------- autoplay (radio)
let autoplayBusy = false;
async function extendWithRadio() {
  const seed = state.queue[state.index];
  if (!seed || autoplayBusy || !navigator.onLine) return false;
  autoplayBusy = true;
  try {
    const have = new Set(state.queue.map((t) => t.video_id || t.youtube_id || `id${t.id}`));
    const more = (await radioFor(seed)).filter((t) => !have.has(t.video_id || t.youtube_id || `id${t.id}`));
    if (!more.length) return false;
    more.forEach((t) => { t.fromRadio = true; });
    state.queue.push(...more);
    state.original?.push(...more);
    emit('queue');
    return true;
  } catch {
    return false;
  } finally {
    autoplayBusy = false;
  }
}
const autoplayOn = () => store.prefs?.extra?.autoplay !== false;

function skipAfterError() {
  state.errors += 1;
  if (state.errors >= 3) {
    toast('Several songs failed in a row, so playback has stopped.', 'error', 5000);
    state.errors = 0;
    return;
  }
  setTimeout(() => player.next(), 800);
}

function effectiveMode() {
  return videoActive() ? 'video' : 'audio';
}

async function play() {
  viz.attach(audio);          // first call happens inside a user gesture
  await viz.resume();
  const p = state.master.play();
  if (videoActive() && state.master === audio && video.getAttribute('src')) {
    seekVideoTo(audio.currentTime + syncOffset());
    video.play().catch(() => {});
  }
  return p;
}

// ---------------------------------------------------------------- video sync loop
// Large drift -> one seek, aimed ahead by the measured seek latency (a remote
// video needs time to buffer at the new position). While that seek is in
// flight we never seek again: re-seeking every tick would cancel each seek and
// the video would never catch up. Small drift -> nudge playbackRate.
let syncTimer = 0;
let lastSeekAt = 0;
let seekLatency = 0.3;            // seconds, learned from 'seeked' events
video.addEventListener('seeked', () => {
  if (!lastSeekAt) return;
  const took = (performance.now() - lastSeekAt) / 1000;
  seekLatency = Math.min(2, Math.max(0.1, seekLatency * 0.5 + took * 0.5));
});

function seekVideoTo(target) {
  lastSeekAt = performance.now();
  video.playbackRate = 1;
  const ahead = audio.paused ? 0 : seekLatency;
  video.currentTime = Math.max(0, Math.min(target + ahead, (video.duration || Infinity) - 0.1));
}

function syncVideo() {
  if (state.master !== audio || !videoActive() || !video.getAttribute('src')) return;
  if (video.readyState < 1) return;
  const target = audio.currentTime + syncOffset();
  if (target < 0 || (Number.isFinite(video.duration) && target > video.duration)) {
    if (!video.paused) video.pause();
    return;
  }
  if (audio.paused) {
    if (!video.paused) video.pause();
    if (!video.seeking && Math.abs(video.currentTime - target) > 0.1) seekVideoTo(target);  // exact frame while paused
    return;
  }
  if (video.paused) video.play().catch(() => {});
  if (video.seeking || performance.now() - lastSeekAt < 1000) return;   // let the last seek land
  const drift = video.currentTime - target;
  if (Math.abs(drift) > 0.35) {
    seekVideoTo(target);
  } else if (Math.abs(drift) > 0.04) {
    video.playbackRate = Math.min(1.08, Math.max(0.92, 1 - drift * 0.6));
  } else if (video.playbackRate !== 1) {
    video.playbackRate = 1;
  }
}
function startSyncLoop() { if (!syncTimer) syncTimer = setInterval(syncVideo, 200); }
function stopSyncLoop() { clearInterval(syncTimer); syncTimer = 0; }

// ---------------------------------------------------------------- public API
export const player = {
  get media() { return state.master; },
  get current() { return state.queue[state.index] || null; },
  get manifest() { return state.manifest; },
  get queue() { return state.queue; },
  get index() { return state.index; },
  get mode() { return effectiveMode(); },
  get desiredMode() { return state.mode; },
  get shuffle() { return state.shuffle; },
  get repeat() { return state.repeat; },
  get volume() { return state.volume; },
  get muted() { return state.muted; },
  get context() { return state.context; },
  get paused() { return state.master.paused; },

  async playList(tracks, index = 0, context = null, { mode } = {}) {
    const playable = tracks.filter((t) => t.has_audio || t.has_video);
    if (!playable.length) { toast('Nothing playable here yet'); return; }
    const start = tracks[index] && (tracks[index].has_audio || tracks[index].has_video) ? tracks[index] : playable[0];
    state.context = context;
    state.original = playable;
    state.queue = state.shuffle ? shuffled(playable, start) : [...playable];
    if (mode) state.mode = mode;
    emit('queue');
    await load(state.queue.indexOf(start), { reason: 'skip' });
  },

  async play() {
    if (!state.manifest) {
      if (state.queue.length) return load(Math.max(state.index, 0));
      return undefined;
    }
    try { await play(); } catch (err) {
      if (err.name !== 'AbortError' && err.name !== 'NotAllowedError') toast(err.message, 'error');
    }
    return undefined;
  },
  pause() { state.master.pause(); },
  toggle() { return state.master.paused ? this.play() : this.pause(); },

  next(auto = false) {
    if (!state.queue.length) return;
    if (auto && state.repeat === 'one') { this.seek(0); this.play(); return; }
    let i = state.index + 1;
    if (i >= state.queue.length) {
      if (state.repeat === 'all') {
        i = 0;
      } else if (autoplayOn()) {
        extendWithRadio().then((ok) => {
          if (ok) { toast('Autoplay: similar songs'); load(i, { reason: auto ? 'ended' : 'skip' }); }
          else if (auto) { endSession('ended'); state.master.pause(); this.seek(0); startSession(this.current); }
        });
        return;
      } else {
        if (auto) { endSession('ended'); state.master.pause(); this.seek(0); startSession(this.current); }
        return;
      }
    }
    load(i, { reason: auto ? 'ended' : 'skip' });
    // Top up before the queue runs dry so Up Next always shows what's coming.
    if (autoplayOn() && state.repeat === 'off' && state.queue.length - i <= 2) extendWithRadio();
  },

  prev() {
    if (state.master.currentTime > 3 || state.index <= 0) { this.seek(0); return; }
    load(state.index - 1, { reason: 'skip' });
  },

  jumpTo(i) { if (state.queue[i]) load(i, { reason: 'skip' }); },

  seek(sec) {
    const m = state.master;
    if (!Number.isFinite(sec)) return;
    if (m.readyState < 1) { state.pendingSeek = sec; return; }
    m.currentTime = Math.max(0, Math.min(sec, (m.duration || sec) - 0.05));
    if (state.session) state.session.lastPos = null;
    if (state.master === audio && videoActive() && video.getAttribute('src')) seekVideoTo(m.currentTime + syncOffset());
    setPosition(m);
  },
  seekBy(delta) { this.seek((state.master.currentTime || 0) + delta); },

  setVolume(v) { state.volume = Math.max(0, Math.min(1, v)); state.muted = state.volume === 0 ? state.muted : false; applyVolume(); persistVolume(); },
  toggleMute() { state.muted = !state.muted; applyVolume(); persistVolume(); },

  setShuffle(on) {
    state.shuffle = on;
    const cur = this.current;
    if (cur) {
      state.queue = on ? shuffled(state.queue, cur) : [...(state.original || state.queue)];
      state.index = state.queue.indexOf(cur);
    }
    emit('queue');
    emit('shuffle', on);
    api.patch('/api/preferences', { shuffle: on }).catch(() => {});
  },
  cycleRepeat() {
    state.repeat = { off: 'all', all: 'one', one: 'off' }[state.repeat];
    emit('repeat', state.repeat);
    api.patch('/api/preferences', { repeat: state.repeat }).catch(() => {});
  },

  async setMode(mode) {
    if (mode === 'video' && !state.manifest?.video && state.manifest) {
      await this.reloadManifest().catch(() => {});
    }
    if (mode === 'video' && !state.manifest?.video) {
      toast(state.manifest ? 'This song has no music video yet' : 'Play something first');
      return;
    }
    if (state.master === video) { toast('This item only has a video'); return; }
    state.mode = mode;
    if (mode === 'video') {
      setVideoSrc();
      seekVideoTo(audio.currentTime + syncOffset());
      if (!audio.paused) video.play().catch(() => {});
      startSyncLoop();
    } else {
      // Keep the video loaded (just paused) so switching back is instant;
      // it is only discarded when the track changes.
      if (document.pictureInPictureElement === video) document.exitPictureInPicture().catch(() => {});
      video.pause();
      stopSyncLoop();
    }
    if (state.session) state.session.mode = effectiveMode();
    emit('mode', effectiveMode());
  },
  toggleMode() { this.setMode(effectiveMode() === 'video' ? 'audio' : 'video'); },

  async togglePiP() {
    if (!document.pictureInPictureEnabled) { toast('Picture-in-picture is not supported in this browser'); return; }
    try {
      if (document.pictureInPictureElement) { await document.exitPictureInPicture(); return; }
      if (effectiveMode() !== 'video') this.setMode('video');
      if (effectiveMode() !== 'video') return;
      if (video.readyState < 1) await new Promise((r) => video.addEventListener('loadedmetadata', r, { once: true }));
      await video.requestPictureInPicture();
    } catch (err) {
      toast(`Picture-in-picture failed: ${err.message}`, 'error');
    }
  },

  enqueue(tracks) {
    state.queue.push(...tracks);
    state.original?.push(...tracks);
    emit('queue');
    if (state.index < 0) load(0);
    toast(tracks.length === 1 ? 'Added to queue' : `Added ${tracks.length} songs to queue`);
  },
  playNext(tracks) {
    state.queue.splice(state.index + 1, 0, ...tracks);
    state.original?.push(...tracks);
    emit('queue');
    if (state.index < 0) load(0);
    toast('Will play next');
  },
  removeAt(i) {
    if (i === state.index || !state.queue[i]) return;
    const [t] = state.queue.splice(i, 1);
    if (i < state.index) state.index -= 1;
    if (state.original) state.original = state.original.filter((x) => x !== t);
    emit('queue');
  },
  async startRadio(track) {
    toast(`Starting radio based on "${track.title}"`);
    try {
      const more = await radioFor(track);
      await this.playList([track, ...more], 0, { type: 'radio' });
    } catch (err) {
      toast(`Couldn't start radio: ${err.message}`, 'error');
    }
  },

  clearUpcoming() {
    state.queue.splice(state.index + 1);
    emit('queue');
  },

  async reloadManifest() {
    const t = this.current;
    if (!t) return;
    const m = await getManifest(t.id, { fresh: true });
    if (this.current?.id !== t.id) return;
    state.manifest = m;
    t.has_video = !!m.video;
    emit('manifest', m);
    emit('mode', effectiveMode());
  },

  /** Restore last session (paused) from saved preferences. */
  async restore(prefs) {
    if (!prefs?.last_track_id) return;
    try {
      const ids = prefs.queue_track_ids?.length ? prefs.queue_track_ids : [prefs.last_track_id];
      const r = await api.get('/api/tracks', { ids: ids.join(',') });
      if (!r.items.length || state.index >= 0) return;
      state.queue = r.items;
      state.original = [...r.items];
      let i = r.items.findIndex((t) => t.id === prefs.last_track_id);
      if (i < 0) i = Math.min(prefs.queue_index || 0, r.items.length - 1);
      emit('queue');
      await load(i, { autoplay: false, startMs: prefs.last_position_ms || 0 });
    } catch (err) {
      console.warn('Could not restore last session', err);
    }
  },

  applyPrefs(prefs) {
    if (!prefs) return;
    state.volume = prefs.volume ?? 0.8;
    state.muted = !!prefs.muted;
    state.shuffle = !!prefs.shuffle;
    state.repeat = prefs.repeat || 'off';
    state.normalize = prefs.normalize_volume !== false;
    if (state.index < 0) state.mode = prefs.preferred_mode || 'audio';
    viz.setStyle(prefs.visualizer_style);
    applyVolume();
    emit('shuffle', state.shuffle);
    emit('repeat', state.repeat);
  },
};

let volTimer = 0;
function persistVolume() {
  clearTimeout(volTimer);
  if (!navigator.onLine) return;
  volTimer = setTimeout(() => api.patch('/api/preferences', { volume: state.volume, muted: state.muted }).catch(() => {}), 700);
}

// ---------------------------------------------------------------- media element events
function onMaster(type, fn) {
  for (const el of [audio, video]) el.addEventListener(type, (e) => { if (e.target === state.master) fn(e); });
}

onMaster('play', () => {
  document.body.classList.add('playing');
  document.body.classList.remove('paused');
  setPlaybackState('playing');
  viz.setPlaying(true);
  if (videoActive()) startSyncLoop();
  emit('state', 'playing');
});
onMaster('pause', () => {
  document.body.classList.remove('playing');
  document.body.classList.add('paused');
  setPlaybackState('paused');
  viz.setPlaying(false);
  syncVideo();
  saveState({ now: true });
  emit('state', 'paused');
});
onMaster('playing', () => { state.errors = 0; emit('loading', false); });
onMaster('waiting', () => emit('loading', true));
onMaster('canplay', () => emit('loading', false));
onMaster('ended', () => { emit('state', 'ended'); player.next(true); });
onMaster('loadedmetadata', () => {
  if (state.pendingSeek != null) {
    state.master.currentTime = state.pendingSeek;
    state.pendingSeek = null;
  }
  setPosition(state.master);
  emit('time', state.master);
});
onMaster('durationchange', () => emit('time', state.master));
onMaster('seeked', () => { syncVideo(); setPosition(state.master); emit('time', state.master); });
onMaster('ratechange', () => setPosition(state.master));

let lastSave = 0;
onMaster('timeupdate', () => {
  trackPlayed();
  emit('time', state.master);
  if (Date.now() - lastSave > 15000 && !state.master.paused) { lastSave = Date.now(); saveState({ now: true }); }
});
onMaster('error', () => {
  const err = state.master.error;
  if (!state.master.getAttribute('src')) return;
  const msg = { 1: 'aborted', 2: 'network error', 3: 'could not decode', 4: 'format not supported' }[err?.code] || 'unknown error';
  const t = player.current;
  emit('loading', false);
  toast(`Playback error${t ? ` on "${t.title}"` : ''}: ${msg}`, 'error');
  skipAfterError();
});

// Video element in Video mode: buffering spinner + leaving PiP
video.addEventListener('waiting', () => { if (videoActive()) emit('video-loading', true); });
video.addEventListener('playing', () => emit('video-loading', false));
video.addEventListener('canplay', () => emit('video-loading', false));
video.addEventListener('leavepictureinpicture', () => emit('pip', false));
video.addEventListener('enterpictureinpicture', () => emit('pip', true));
// PiP window's own play/pause buttons control the muted video: forward them to the master.
video.addEventListener('pause', () => {
  if (document.pictureInPictureElement === video && state.master === audio && !audio.paused) audio.pause();
});
video.addEventListener('play', () => {
  if (document.pictureInPictureElement === video && state.master === audio && audio.paused) audio.play().catch(() => {});
});

window.addEventListener('pagehide', () => { saveState({ now: true }); endSession('replaced'); });
document.addEventListener('visibilitychange', () => { if (document.hidden) saveState({ now: true }); });

store.on('like', ({ trackId, liked }) => {
  state.queue.forEach((t) => { if (t.id === trackId) t.is_favorite = liked; });
  if (state.manifest?.track?.id === trackId) state.manifest.track.is_favorite = liked;
});
store.on('prefs', (p) => player.applyPrefs(p));
