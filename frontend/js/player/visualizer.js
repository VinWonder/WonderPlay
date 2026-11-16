// Real-time visualizer: <audio> -> MediaElementSource -> AnalyserNode -> speakers.
//
// iOS caveat: routing an <audio> element through an AudioContext makes iOS
// Safari stop playback when the screen locks. Lock-screen playback matters
// more than eye candy, so on iOS the visualizer is disabled.

const IS_IOS = /iP(hone|ad|od)/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

let ctx = null;
let analyser = null;
let freq = null;
let wave = null;
let failed = false;
const canvases = new Set();
let style = 'bars';
let raf = 0;
let playing = false;
let colour = '123, 140, 255';
const peaks = new Float32Array(64);

export const visualizerSupported = () => !IS_IOS && !failed && 'AudioContext' in window;

/** Must be called from a user gesture (e.g. the first play click). */
export function attach(audioEl) {
  if (ctx || !visualizerSupported()) return;
  try {
    ctx = new AudioContext({ latencyHint: 'playback' });
    const src = ctx.createMediaElementSource(audioEl);
    analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0.78;
    src.connect(analyser);
    analyser.connect(ctx.destination);
    freq = new Uint8Array(analyser.frequencyBinCount);
    wave = new Uint8Array(analyser.fftSize);
  } catch (err) {
    console.warn('Visualizer unavailable:', err);
    failed = true;
    ctx = null;
  }
}

/** Resume the audio graph (browsers suspend it until a gesture). */
export async function resume() {
  if (ctx && ctx.state !== 'running') {
    try { await ctx.resume(); } catch { /* ignored */ }
  }
}

export function setStyle(s) { style = s || 'bars'; kick(); }
export function setColour(rgbString) { colour = rgbString; }
export function setPlaying(p) { playing = p; kick(); }

export function register(canvas) { canvases.add(canvas); kick(); }
export function unregister(canvas) { canvases.delete(canvas); }

function visible(c) {
  return c.isConnected && c.offsetParent !== null && c.clientWidth > 0;
}

function kick() {
  if (!raf) raf = requestAnimationFrame(frame);
}

function sizeCanvas(c) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.round(c.clientWidth * dpr);
  const h = Math.round(c.clientHeight * dpr);
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  return [w, h, dpr];
}

// Log-spaced bands so bass doesn't hog the display.
function bands(n) {
  const out = new Float32Array(n);
  const nyq = (ctx?.sampleRate || 44100) / 2;
  const binHz = nyq / freq.length;
  const lo = Math.log10(35), hi = Math.log10(16000);
  for (let i = 0; i < n; i++) {
    const f0 = 10 ** (lo + ((hi - lo) * i) / n);
    const f1 = 10 ** (lo + ((hi - lo) * (i + 1)) / n);
    const b0 = Math.max(1, Math.floor(f0 / binHz));
    const b1 = Math.max(b0 + 1, Math.ceil(f1 / binHz));
    let m = 0;
    for (let b = b0; b < b1 && b < freq.length; b++) m = Math.max(m, freq[b]);
    out[i] = m / 255;
  }
  return out;
}

function drawBars(g, w, h, dpr, data) {
  const n = data.length;
  const gap = 3 * dpr;
  const bw = (w - gap * (n - 1)) / n;
  for (let i = 0; i < n; i++) {
    const v = data[i] ** 1.4;
    peaks[i] = Math.max(v, peaks[i] - 0.012);
    const bh = Math.max(2 * dpr, v * h * 0.95);
    const x = i * (bw + gap);
    const grad = g.createLinearGradient(0, h, 0, h - bh);
    grad.addColorStop(0, `rgba(${colour}, .15)`);
    grad.addColorStop(1, `rgba(${colour}, .95)`);
    g.fillStyle = grad;
    g.beginPath();
    g.roundRect(x, h - bh, bw, bh, [Math.min(bw / 2, 4 * dpr), Math.min(bw / 2, 4 * dpr), 0, 0]);
    g.fill();
    g.fillStyle = `rgba(${colour}, .9)`;
    g.fillRect(x, h - peaks[i] * h * 0.95 - 3 * dpr, bw, 2 * dpr);
  }
}

function drawWave(g, w, h, dpr) {
  analyser.getByteTimeDomainData(wave);
  g.lineWidth = 2.5 * dpr;
  g.strokeStyle = `rgba(${colour}, .95)`;
  g.shadowColor = `rgba(${colour}, .6)`;
  g.shadowBlur = 12 * dpr;
  g.beginPath();
  const step = wave.length / w;
  for (let x = 0; x < w; x += 2) {
    const v = wave[Math.floor(x * step)] / 128 - 1;
    const y = h / 2 + v * h * 0.42;
    if (x === 0) g.moveTo(x, y); else g.lineTo(x, y);
  }
  g.stroke();
  g.shadowBlur = 0;
}

function drawCircle(g, w, h, dpr, data) {
  const cx = w / 2, cy = h / 2;
  const r = Math.min(w, h) * 0.22;
  const n = data.length;
  g.lineCap = 'round';
  g.lineWidth = Math.max(2, (Math.PI * 2 * r) / n / 2.2);
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 - Math.PI / 2;
    const len = r * 0.12 + data[i] ** 1.3 * Math.min(w, h) * 0.26;
    g.strokeStyle = `rgba(${colour}, ${0.35 + data[i] * 0.65})`;
    g.beginPath();
    g.moveTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
    g.lineTo(cx + Math.cos(a) * (r + len), cy + Math.sin(a) * (r + len));
    g.stroke();
  }
}

function frame() {
  raf = 0;
  const live = [...canvases].filter(visible);
  if (!live.length || style === 'off' || !analyser) {
    live.forEach((c) => c.getContext('2d').clearRect(0, 0, c.width, c.height));
    return;
  }
  analyser.getByteFrequencyData(freq);
  for (const c of live) {
    const [w, h, dpr] = sizeCanvas(c);
    const g = c.getContext('2d');
    g.clearRect(0, 0, w, h);
    const n = c.dataset.bars ? Number(c.dataset.bars) : Math.max(24, Math.min(64, Math.floor(w / (10 * dpr))));
    const s = c.dataset.style || style;
    if (s === 'wave') drawWave(g, w, h, dpr);
    else if (s === 'circle') drawCircle(g, w, h, dpr, bands(Math.min(n * 2, 96)));
    else drawBars(g, w, h, dpr, bands(n));
  }
  // Keep animating while playing; when paused, render a few more frames so bars fall.
  if (playing) { raf = requestAnimationFrame(frame); return; }
  for (let i = 0; i < peaks.length; i++) peaks[i] *= 0.9;
  if (peaks.some((p) => p > 0.01)) raf = requestAnimationFrame(frame);
}
