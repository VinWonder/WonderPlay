// Dynamic theme: pull a vibrant colour out of the album art with <canvas>,
// then normalise its lightness so it always reads well on the dark glass UI.

const cache = new Map();

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return [h * 60, s, l];
}

function hslToRgb(h, s, l) {
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [Math.round(f(0) * 255), Math.round(f(8) * 255), Math.round(f(4) * 255)];
}

const hex = ([r, g, b]) => `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`;

export function hexToRgb(h) {
  const m = /^#?([0-9a-f]{6})$/i.exec(h || '');
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Turn any colour into a readable accent: keep hue, clamp saturation & lightness. */
export function toAccent(rgb) {
  let [h, s, l] = rgbToHsl(...rgb);
  if (s < 0.12) {           // near-grey covers: fall back to a soft neutral-cool accent
    return [168, 176, 210];
  }
  s = Math.min(Math.max(s, 0.45), 0.85);
  l = Math.min(Math.max(l, 0.62), 0.74);
  return hslToRgb(h, s, l);
}

/** Load an image (same-origin cover URL) and return its most vibrant colour. */
export function extractColor(url) {
  if (cache.has(url)) return cache.get(url);
  const p = new Promise((resolve) => {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => {
      try {
        const size = 40;
        const c = document.createElement('canvas');
        c.width = size; c.height = size;
        const ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, size, size);
        const { data } = ctx.getImageData(0, 0, size, size);
        // Bucket by hue (12 buckets); weight = saturation * mid-lightness.
        const buckets = Array.from({ length: 12 }, () => ({ w: 0, r: 0, g: 0, b: 0 }));
        let total = { r: 0, g: 0, b: 0, n: 0 };
        for (let i = 0; i < data.length; i += 4) {
          const r = data[i], g = data[i + 1], b = data[i + 2];
          total.r += r; total.g += g; total.b += b; total.n += 1;
          const [hh, s, l] = rgbToHsl(r, g, b);
          if (l < 0.12 || l > 0.92 || s < 0.18) continue;
          const w = s * (1 - Math.abs(l - 0.5) * 1.6);
          const bk = buckets[Math.floor(hh / 30) % 12];
          bk.w += w; bk.r += r * w; bk.g += g * w; bk.b += b * w;
        }
        const best = buckets.reduce((a, bkt) => (bkt.w > a.w ? bkt : a), { w: 0 });
        const rgb = best.w > 2
          ? [best.r / best.w, best.g / best.w, best.b / best.w].map(Math.round)
          : [total.r / total.n, total.g / total.n, total.b / total.n].map(Math.round);
        resolve(rgb);
      } catch {
        resolve(null); // tainted canvas or decode failure
      }
    };
    img.onerror = () => resolve(null);
    img.src = url;
  });
  cache.set(url, p);
  if (cache.size > 200) cache.delete(cache.keys().next().value);
  return p;
}

const DEFAULT = [123, 140, 255];

export function applyAccent(rgb) {
  const a = rgb ? toAccent(rgb) : DEFAULT;
  const root = document.documentElement.style;
  root.setProperty('--accent', hex(a));
  root.setProperty('--accent-rgb', a.join(', '));
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', hex(a.map((v) => Math.round(v * 0.18))));
  return hex(a);
}

export function resetAccent() { applyAccent(null); }
