// Binds the controller to the UI: dock, Now Playing panel, full-screen player,
// living backdrop, dynamic accent colour and keyboard shortcuts.
import { api } from '../api.js';
import { store } from '../store.js';
import { artistHref, artUrl, esc, fmtTime, markPlaying, toast, updateLikeButtons } from '../ui.js';
import { applyAccent, extractColor, hexToRgb, resetAccent } from './color.js';
import { LyricsView } from './lyrics.js';
import { initMediaSession } from './mediasession.js';
import { player } from './controller.js';
import * as viz from './visualizer.js';

const $ = (id) => document.getElementById(id);
const all = (sel) => document.querySelectorAll(sel);
const isPhone = () => window.matchMedia('(max-width: 767.98px), (max-height: 520px) and (orientation: landscape)').matches;
const isWide = () => window.matchMedia('(min-width: 1280px)').matches;

const el = {
  dockArt: $('dock-art'), dockTitle: $('dock-title'), dockArtist: $('dock-artist'), dockLike: $('dock-like'),
  miniBar: $('dock-mini-bar'), panelBody: $('np-panel-body'), npView: $('np-view'), stage: $('np-stage'),
  stageImg: $('stage-art-img'), npvTitle: $('npv-title'), npvArtist: $('npv-artist'), npvPanel: $('np-view-panel'),
  npvContext: $('np-view-context'), spinner: $('stage-spinner'), device: $('device-status'),
  backA: $('backdrop-a'), backB: $('backdrop-b'),
};

let npTab = 'playing';
let npvTab = 'lyrics';
let seeking = false;
let panelLyrics = null;
let viewLyrics = null;

// ---------------------------------------------------------------- helpers
// Until the media element knows its length, fall back to the library duration
// so an early seek doesn't collapse to 0:00.
const mediaDuration = () => (Number.isFinite(player.media.duration) && player.media.duration > 0
  ? player.media.duration : (player.current?.duration_ms || 0) / 1000);
const setRange = (input, frac) => {
  input.style.setProperty('--p', `${Math.max(0, Math.min(1, frac)) * 100}%`);
};

function artistLinks(t) {
  const artists = t.artists?.filter((a) => a.role === 'primary' || a.role === 'featured');
  if (artists?.length) return artists.map((a) => `<a href="#/artist/${a.id}">${esc(a.name)}</a>`).join(', ');
  const href = artistHref(t);
  if (href) return `<a href="${href}">${esc(t.artist_name)}</a>`;
  return t.artist_id ? `<a href="#/artist/${t.artist_id}">${esc(t.artist_name)}</a>` : esc(t.artist_name || 'Unknown artist');
}

function contextLabel() {
  const c = player.context;
  if (!c) return 'Now playing';
  return { album: 'Playing from album', playlist: 'Playing from playlist', artist: 'Playing from artist',
    search: 'Playing from search', library: 'Playing from your library' }[c.type] || 'Now playing';
}

// ---------------------------------------------------------------- backdrop & accent
let backFront = el.backA;
function setBackdrop(url) {
  const back = backFront === el.backA ? el.backB : el.backA;
  if (!url) { el.backA.classList.remove('show'); el.backB.classList.remove('show'); return; }
  const img = new Image();
  img.onload = () => {
    back.style.backgroundImage = `url("${url}")`;
    back.classList.add('show');
    backFront.classList.remove('show');
    backFront = back;
  };
  img.src = url;
}

async function applyTheme(track) {
  const dynamic = store.prefs?.dynamic_theme !== false;
  if (!dynamic || !track) {
    resetAccent();
  } else {
    const rgb = ((track.cover_path || track.thumbnail) && await extractColor(artUrl(track, 300))) || hexToRgb(track.dominant_color);
    if (player.current !== track) return;
    applyAccent(rgb);
  }
  viz.setColour(getComputedStyle(document.documentElement).getPropertyValue('--art-rgb').trim());
  setBackdrop(track?.cover_path || track?.thumbnail ? artUrl(track, 300) : null);
}

// ---------------------------------------------------------------- track rendering
function renderTrack(t) {
  if (!t) return;
  el.dockArt.src = artUrl(t);
  el.dockTitle.textContent = t.title;
  el.dockTitle.href = t.album_id ? `#/album/${t.album_id}` : t.album_browse_id ? `#/catalog/album/${t.album_browse_id}` : '#/';
  el.dockArtist.textContent = t.artist_name || 'Unknown artist';
  el.dockArtist.href = artistHref(t) || '#/';
  el.dockLike.disabled = !t.id;
  all('[data-action="like-current"]').forEach((b) => { b.dataset.likeId = t.id; b.dataset.trackId = t.id; });
  updateLikeButtons(t.id, !!t.is_favorite);
  el.stageImg.src = artUrl(t, null);
  el.npvTitle.textContent = t.title;
  el.npvArtist.innerHTML = artistLinks(t);
  el.npvContext.textContent = contextLabel();
  document.title = `${t.title} • ${t.artist_name || 'WonderPlay'}`;
  markPlaying();
  applyTheme(t);
}

function renderModeButtons() {
  const m = player.manifest;
  const hasVideo = !!m?.video && player.media.tagName === 'AUDIO';
  const mode = player.mode;
  all('[data-ctl="mode"]').forEach((b) => {
    b.classList.toggle('active', b.dataset.mode === mode);
    if (b.dataset.mode === 'video') {
      b.disabled = !hasVideo && mode !== 'video';
      b.title = hasVideo ? 'Video mode (V)' : 'No music video for this song';
    }
  });
  all('[data-ctl="pip"]').forEach((b) => { b.disabled = !m?.video || !document.pictureInPictureEnabled; });
  el.stage.classList.toggle('video', mode === 'video');
  if (mode !== 'video') el.spinner.hidden = true;
}

// ---------------------------------------------------------------- Now Playing panel
const lyricsEmpty = (t) => `<div class="lyrics-empty"><i class="bi bi-quote" style="font-size:30px;color:var(--accent)"></i>
  <p class="mt-2 mb-3">No lyrics for this song yet.</p>
  ${t?.artist_name ? '<button class="pill-btn" type="button" data-action="fetch-lyrics"><i class="bi bi-search"></i> Find lyrics</button>' : ''}</div>`;

function queueHtml() {
  const q = player.queue;
  const i = player.index;
  if (!q.length) return '<div class="np-empty"><i class="bi bi-list-ul"></i>Your queue is empty.</div>';
  const item = (t, idx, removable) => `
    <div class="q-item ${idx === i ? 'current' : ''}" data-q-index="${idx}">
      <img src="${artUrl(t)}" alt="" loading="lazy">
      <div class="min-w-0"><strong>${esc(t.title)}</strong><span>${esc(t.artist_name || '')}</span></div>
      ${removable ? `<button class="icon-btn" type="button" data-q-remove="${idx}" aria-label="Remove ${esc(t.title)} from queue"><i class="bi bi-x-lg"></i></button>` : '<span class="eq position-relative d-inline-block" style="width:14px;height:14px"></span>'}
    </div>`;
  const upcoming = q.slice(i + 1);
  return `
    <div class="q-label">${esc(contextLabel())}</div>
    ${q[i] ? item(q[i], i, false) : ''}
    <div class="d-flex align-items-center justify-content-between"><div class="q-label">Next up · ${upcoming.length}</div>
      ${upcoming.length ? '<button class="pill-btn py-1 px-2" style="font-size:11px" type="button" data-action="clear-queue">Clear</button>' : ''}</div>
    ${upcoming.length ? upcoming.slice(0, 100).map((t, k) => {
      const first = t.fromRadio && !upcoming[k - 1]?.fromRadio;
      return (first ? '<div class="q-label"><i class="bi bi-broadcast"></i> Autoplay · similar songs</div>' : '') + item(t, i + 1 + k, true);
    }).join('') : `<p class="muted small px-2">${store.prefs?.extra?.autoplay === false ? 'Nothing queued after this song.' : 'Similar songs will play next (Autoplay).'}</p>`}`;
}

function renderPanel() {
  all('[data-np-tab]').forEach((b) => {
    b.classList.toggle('active', b.dataset.npTab === npTab);
    b.setAttribute('aria-selected', b.dataset.npTab === npTab ? 'true' : 'false');
  });
  all('[data-action="np-tab"]').forEach((b) => b.classList.toggle('panel-on', panelVisible() && b.dataset.tab === npTab));
  const t = player.current;
  const m = player.manifest;
  const body = el.panelBody;
  panelLyrics = null;

  if (!t) {
    body.innerHTML = `<div class="np-empty"><i class="bi bi-music-note-beamed"></i><strong class="d-block text-light mb-1">Nothing playing</strong>
      Pick a song, album or playlist and it will show up here with lyrics and what's next.</div>`;
    return;
  }

  if (npTab === 'lyrics') {
    body.innerHTML = '<div id="np-panel-lyrics"></div>';
    panelLyrics = new LyricsView(body.firstElementChild, body, (ms) => player.seek(ms / 1000));
    panelLyrics.set(m?.lyrics, { emptyHtml: lyricsEmpty(t) });
    panelLyrics.update((player.media.currentTime || 0) * 1000);
    return;
  }
  if (npTab === 'queue') { body.innerHTML = queueHtml(); return; }

  const next = player.queue.slice(player.index + 1, player.index + 4);
  const preview = LyricsView.preview(m?.lyrics, (player.media.currentTime || 0) * 1000);
  body.innerHTML = `
    <div class="np-art-lg" data-action="open-np-view" title="Open full-screen player">
      <img src="${artUrl(t, null)}" alt="Cover of ${esc(t.album_title || t.title)}">
      <canvas class="viz" id="viz-panel" data-bars="40" aria-hidden="true"></canvas>
    </div>
    <div class="np-title-row">
      <div class="flex-grow-1 min-w-0"><h3>${esc(t.title)}</h3><div class="artist">${artistLinks(t)}</div></div>
      <button class="icon-btn ${t.is_favorite ? 'liked' : ''}" type="button" data-action="like-current" data-like-id="${t.id}" data-track-id="${t.id}"
        aria-label="${t.is_favorite ? 'Remove from Liked Songs' : 'Save to Liked Songs'}"><i class="bi ${t.is_favorite ? 'bi-heart-fill' : 'bi-heart'}"></i></button>
      <button class="icon-btn" type="button" data-action="current-menu" aria-label="More options"><i class="bi bi-three-dots"></i></button>
    </div>
    ${preview ? `<div class="np-card accent" data-action="np-tab" data-tab="lyrics" role="button" tabindex="0">
        <div class="np-card-head"><span>Lyrics</span><button type="button" tabindex="-1">Show all</button></div>
        <div class="lyric-now" id="np-lyric-now">${esc(preview.now)}</div><div class="lyric-next" id="np-lyric-next">${esc(preview.next)}</div></div>`
      : m?.lyrics?.plain_text ? `<div class="np-card" data-action="np-tab" data-tab="lyrics" role="button" tabindex="0">
        <div class="np-card-head"><span>Lyrics</span><button type="button" tabindex="-1">Show</button></div>
        <div class="muted small" style="white-space:pre-line;max-height:4.5em;overflow:hidden">${esc(m.lyrics.plain_text)}</div></div>`
      : `<div class="np-card"><div class="np-card-head"><span>Lyrics</span>${t.artist_name ? '<button type="button" data-action="fetch-lyrics">Find lyrics</button>' : ''}</div>
        <div class="muted small">No lyrics for this song yet.</div></div>`}
    <div class="np-card">
      <div class="np-card-head"><span>Next in queue</span><button type="button" data-action="np-tab" data-tab="queue">Open queue</button></div>
      ${next.length ? next.map((x, k) => `<div class="q-item" data-q-index="${player.index + 1 + k}"><img src="${artUrl(x)}" alt="" loading="lazy">
        <div class="min-w-0"><strong>${esc(x.title)}</strong><span>${esc(x.artist_name || '')}</span></div></div>`).join('')
        : '<div class="muted small">Nothing queued after this song.</div>'}
    </div>
    ${artistHref(t) ? `<a class="np-card np-artist-card d-block" href="${artistHref(t)}">
        <img src="${artUrl(t, null)}" alt="" loading="lazy">
        <div class="over"><small>Artist</small><strong>${esc(t.artist_name)}</strong></div></a>` : ''}
    ${m && !m.video ? `<a class="np-card d-flex align-items-center gap-3" href="#/import?q=${encodeURIComponent(`${t.artist_name || ''} ${t.title} official music video`)}&track=${t.id}">
        <i class="bi bi-camera-video" style="font-size:22px;color:var(--accent)"></i>
        <div><strong class="d-block">Add the music video</strong><span class="muted small">Watch it ad-free and switch with Song mode</span></div></a>` : ''}
  `;
  const canvas = body.querySelector('#viz-panel');
  if (canvas) viz.register(canvas);
}

function panelVisible() {
  if (isWide()) return !document.body.classList.contains('np-collapsed');
  return document.body.classList.contains('np-open');
}

function openPanelTab(tab) {
  const same = npTab === tab && panelVisible();
  npTab = tab;
  if (isWide()) {
    if (same && tab !== 'playing') { npTab = 'playing'; }
    document.body.classList.remove('np-collapsed');
    try { localStorage.removeItem('wp.npCollapsed'); } catch { /* ignore */ }
  } else {
    document.body.classList.toggle('np-open', !same);
  }
  renderPanel();
}

function closePanel() {
  if (isWide()) {
    document.body.classList.add('np-collapsed');
    try { localStorage.setItem('wp.npCollapsed', '1'); } catch { /* ignore */ }
  }
  document.body.classList.remove('np-open');
  renderPanel();
}

// ---------------------------------------------------------------- full-screen player
function renderNpViewPanel() {
  all('[data-npv-tab]').forEach((b) => b.classList.toggle('active', b.dataset.npvTab === npvTab));
  const t = player.current;
  viewLyrics = null;
  if (npvTab === 'queue') { el.npvPanel.innerHTML = queueHtml(); return; }
  el.npvPanel.innerHTML = '<div></div>';
  viewLyrics = new LyricsView(el.npvPanel.firstElementChild, el.npvPanel, (ms) => player.seek(ms / 1000));
  viewLyrics.set(player.manifest?.lyrics, { emptyHtml: lyricsEmpty(t) });
  viewLyrics.update((player.media.currentTime || 0) * 1000);
}

export function openNpView() {
  if (!player.current) { toast('Play something first'); return; }
  el.npView.hidden = false;
  document.body.classList.add('np-view-open');
  viz.register($('viz-stage'));
  renderModeButtons();
  renderNpViewPanel();
  el.npView.querySelector('[data-action="close-np-view"]').focus({ preventScroll: true });
}
export function closeNpView() {
  el.npView.hidden = true;
  document.body.classList.remove('np-view-open');
}

// ---------------------------------------------------------------- time & state
function renderTime(media) {
  const dur = Number.isFinite(media.duration) ? media.duration : 0;
  const cur = media.currentTime || 0;
  if (!seeking) {
    all('[data-ctl="seek"]').forEach((r) => { r.value = dur ? Math.round((cur / dur) * 1000) : 0; setRange(r, dur ? cur / dur : 0); });
    all('[data-time="cur"]').forEach((s) => { s.textContent = fmtTime(cur * 1000); });
  }
  all('[data-time="dur"]').forEach((s) => { s.textContent = fmtTime((dur || (player.current?.duration_ms || 0) / 1000) * 1000); });
  el.miniBar.style.width = `${dur ? (cur / dur) * 100 : 0}%`;
  const ms = cur * 1000;
  panelLyrics?.update(ms);
  viewLyrics?.update(ms);
  const now = $('np-lyric-now');
  if (now) {
    const p = LyricsView.preview(player.manifest?.lyrics, ms);
    if (p && now.textContent !== p.now) {
      now.textContent = p.now;
      $('np-lyric-next').textContent = p.next;
    }
  }
}

function renderPlayState(playing) {
  all('[data-ctl="play"]').forEach((b) => {
    b.innerHTML = `<i class="bi ${playing ? 'bi-pause-fill' : 'bi-play-fill'}"></i>`;
    b.setAttribute('aria-label', playing ? 'Pause' : 'Play');
  });
  el.device.textContent = player.current ? (playing ? 'Playing now' : 'Paused') : 'Ready to play';
}

function renderVolume({ volume, muted }) {
  all('[data-ctl="volume"]').forEach((r) => { r.value = Math.round(volume * 100); setRange(r, muted ? 0 : volume); });
  const icon = muted || volume === 0 ? 'bi-volume-mute' : volume < 0.5 ? 'bi-volume-down' : 'bi-volume-up';
  all('[data-ctl="mute"]').forEach((b) => { b.innerHTML = `<i class="bi ${icon}"></i>`; b.setAttribute('aria-label', muted ? 'Unmute' : 'Mute'); });
}

function renderShuffleRepeat() {
  all('[data-ctl="shuffle"]').forEach((b) => b.setAttribute('aria-pressed', player.shuffle ? 'true' : 'false'));
  all('[data-ctl="repeat"]').forEach((b) => {
    b.dataset.mode = player.repeat;
    b.classList.toggle('on', player.repeat !== 'off');
    b.setAttribute('aria-label', `Repeat: ${player.repeat}`);
    b.innerHTML = `<i class="bi ${player.repeat === 'one' ? 'bi-repeat-1' : 'bi-repeat'}"></i>`;
  });
}

// ---------------------------------------------------------------- wiring
store.on('player:track', (t) => { renderTrack(t); renderPanel(); if (!el.npView.hidden) renderNpViewPanel(); });
store.on('player:manifest', (m) => {
  renderTrack(m.track);
  renderModeButtons();
  renderPanel();
  if (!el.npView.hidden) renderNpViewPanel();
});
store.on('player:mode', (mode) => {
  renderModeButtons();
  if (mode === 'video' && el.npView.hidden) openNpView();
});
store.on('player:state', (s) => renderPlayState(s === 'playing'));
store.on('player:time', renderTime);
store.on('player:volume', renderVolume);
store.on('player:shuffle', renderShuffleRepeat);
store.on('player:repeat', renderShuffleRepeat);
store.on('player:queue', () => { if (npTab !== 'lyrics') renderPanel(); if (npvTab === 'queue' && !el.npView.hidden) renderNpViewPanel(); });
store.on('player:preparing', (on) => {
  document.getElementById('dock').classList.toggle('preparing', on);
  if (on) el.device.textContent = 'Getting this song ready…';
});
store.on('player:loading', (on) => {
  if (on) el.device.textContent = 'Loading…';
  else renderPlayState(!player.paused);
});
store.on('player:video-loading', (on) => { el.spinner.hidden = !on; });
store.on('like', ({ trackId }) => { if (player.current?.id === trackId && npTab === 'playing') renderPanel(); });
store.on('prefs', () => applyTheme(player.current));

// Transport controls (dock + full-screen player share data-ctl attributes)
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-ctl]');
  if (!b || b.disabled || b.tagName === 'INPUT') return;
  switch (b.dataset.ctl) {
    case 'play': player.toggle(); break;
    case 'prev': player.prev(); break;
    case 'next': player.next(); break;
    case 'shuffle': player.setShuffle(!player.shuffle); break;
    case 'repeat': player.cycleRepeat(); break;
    case 'mode': player.setMode(b.dataset.mode); break;
    case 'pip': player.togglePiP(); break;
    case 'mute': player.toggleMute(); break;
    default:
  }
});

document.addEventListener('input', (e) => {
  const r = e.target.closest('[data-ctl="seek"], [data-ctl="volume"]');
  if (!r) return;
  const frac = r.value / r.max;
  if (r.dataset.ctl === 'volume') { player.setVolume(frac); return; }
  seeking = true;
  setRange(r, frac);
  const d = mediaDuration();
  all('[data-time="cur"]').forEach((s) => { s.textContent = fmtTime(frac * d * 1000); });
});
document.addEventListener('change', (e) => {
  const r = e.target.closest('[data-ctl="seek"]');
  if (!r) return;
  player.seek((r.value / r.max) * mediaDuration());
  seeking = false;
});

// Panel / view tabs and queue interactions
document.addEventListener('click', (e) => {
  const tab = e.target.closest('[data-np-tab]');
  if (tab) { npTab = tab.dataset.npTab; renderPanel(); return; }
  const vt = e.target.closest('[data-npv-tab]');
  if (vt) { npvTab = vt.dataset.npvTab; renderNpViewPanel(); return; }
  const rm = e.target.closest('[data-q-remove]');
  if (rm) { player.removeAt(Number(rm.dataset.qRemove)); return; }
  const qi = e.target.closest('[data-q-index]');
  if (qi && !e.target.closest('button')) { player.jumpTo(Number(qi.dataset.qIndex)); return; }
  // Phones: tapping the mini player opens the full-screen player.
  if (isPhone() && e.target.closest('.dock-track') && !e.target.closest('button')) {
    e.preventDefault();
    openNpView();
  }
});

// ---------------------------------------------------------------- keyboard shortcuts
document.addEventListener('keydown', (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  const key = e.key.toLowerCase();
  const handled = {
    ' ': () => player.toggle(),
    arrowright: () => (e.shiftKey ? player.next() : player.seekBy(5)),
    arrowleft: () => (e.shiftKey ? player.prev() : player.seekBy(-5)),
    arrowup: () => player.setVolume(player.volume + 0.05),
    arrowdown: () => player.setVolume(player.volume - 0.05),
    m: () => player.toggleMute(),
    s: () => player.setShuffle(!player.shuffle),
    r: () => player.cycleRepeat(),
    v: () => player.toggleMode(),
    f: () => (el.npView.hidden ? openNpView() : closeNpView()),
    l: () => document.querySelector('#dock-like:not(:disabled)')?.click(),
    escape: () => { if (!el.npView.hidden) closeNpView(); },
  }[key];
  if (!handled) return;
  if (document.activeElement?.tagName === 'BUTTON' && key === ' ') return; // let buttons handle Space
  e.preventDefault();
  handled();
});

// ---------------------------------------------------------------- actions used by markup
export const npActions = {
  'open-np-view': () => openNpView(),
  'close-np-view': () => closeNpView(),
  'np-tab': (b) => openPanelTab(b.dataset.tab),
  'close-np-panel': () => closePanel(),
  'clear-queue': () => player.clearUpcoming(),
  async 'fetch-lyrics'(b) {
    const t = player.current;
    if (!t) return;
    b.disabled = true;
    const old = b.innerHTML;
    b.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Searching…';
    try {
      await api.post(`/api/tracks/${t.id}/lyrics/fetch`);
      await player.reloadManifest();
      toast('Lyrics found');
    } catch (err) {
      toast(err.status === 404 ? 'No lyrics found for this song' : err.message, err.status === 404 ? 'info' : 'error');
      b.disabled = false;
      b.innerHTML = old;
    }
  },
};

export function initNowPlaying(prefs) {
  try { if (localStorage.getItem('wp.npCollapsed')) document.body.classList.add('np-collapsed'); } catch { /* ignore */ }
  initMediaSession(player);
  player.applyPrefs(prefs);
  renderShuffleRepeat();
  renderVolume({ volume: player.volume, muted: player.muted });
  renderPlayState(false);
  renderPanel();
  resetAccent();
  if (!viz.visualizerSupported()) document.body.classList.add('no-viz');
}
