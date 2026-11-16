// Media Session API: lock screen, notification shade, Bluetooth / car stereo
// and hardware media keys.
import { artUrl } from '../ui.js';

const ms = 'mediaSession' in navigator ? navigator.mediaSession : null;

export function initMediaSession(player) {
  if (!ms) return;
  const set = (action, fn) => {
    try { ms.setActionHandler(action, fn); } catch { /* action unsupported in this browser */ }
  };
  set('play', () => player.play());
  set('pause', () => player.pause());
  set('stop', () => player.pause());
  set('previoustrack', () => player.prev());
  set('nexttrack', () => player.next());
  set('seekbackward', (d) => player.seekBy(-(d.seekOffset || 10)));
  set('seekforward', (d) => player.seekBy(d.seekOffset || 10));
  set('seekto', (d) => {
    if (d.fastSeek && 'fastSeek' in player.media) player.media.fastSeek(d.seekTime);
    else player.seek(d.seekTime);
  });
}

export function setMetadata(track) {
  if (!ms || !track) return;
  const abs = (u) => new URL(u, location.href).href;
  const artwork = track.cover_path || track.thumbnail
    ? [
        { src: abs(artUrl(track, 300)), sizes: '300x300', type: 'image/jpeg' },
        { src: abs(artUrl(track, null)), sizes: '1200x1200', type: 'image/jpeg' },
      ]
    : [{ src: abs('img/logo.svg'), sizes: '512x512', type: 'image/svg+xml' }];
  ms.metadata = new MediaMetadata({
    title: track.title,
    artist: track.artist_name || 'Unknown artist',
    album: track.album_title || '',
    artwork,
  });
}

export function setPlaybackState(state) {
  if (ms) ms.playbackState = state; // 'playing' | 'paused' | 'none'
}

export function setPosition(media) {
  if (!ms?.setPositionState) return;
  const d = media.duration;
  if (!Number.isFinite(d) || d <= 0) return;
  try {
    ms.setPositionState({
      duration: d,
      playbackRate: media.playbackRate || 1,
      position: Math.min(Math.max(media.currentTime, 0), d),
    });
  } catch { /* position briefly > duration while loading */ }
}
