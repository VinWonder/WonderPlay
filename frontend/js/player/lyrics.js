// Synced lyrics renderer. One LyricsView per container; all are driven by
// the controller's time updates.
import { esc } from '../ui.js';

function activeIndex(lines, ms) {
  // binary search: last line with t <= ms
  let lo = 0, hi = lines.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].t <= ms) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

export class LyricsView {
  /**
   * @param {HTMLElement} scroller element that scrolls (for auto-centering)
   * @param {(ms:number)=>void} onSeek
   */
  constructor(container, scroller, onSeek) {
    this.el = container;
    this.scroller = scroller;
    this.onSeek = onSeek;
    this.lines = null;
    this.offset = 0;
    this.current = -2;
    this.userScrollUntil = 0;
    this.el.addEventListener('click', (e) => {
      const line = e.target.closest('[data-t]');
      if (line && this.lines) this.onSeek(Number(line.dataset.t) - this.offset);
    });
    const markUser = () => { this.userScrollUntil = performance.now() + 3500; };
    this.scroller.addEventListener('wheel', markUser, { passive: true });
    this.scroller.addEventListener('touchmove', markUser, { passive: true });
  }

  /** lyrics = {synced_lines, plain_text, offset_ms, source} | null */
  set(lyrics, { emptyHtml = '' } = {}) {
    this.current = -2;
    this.lines = null;
    if (lyrics?.synced_lines?.length) {
      this.lines = lyrics.synced_lines;
      this.offset = lyrics.offset_ms || 0;
      this.el.innerHTML = `<div class="lyrics">${this.lines.map((l, i) => `<p class="lyric-line ${l.text ? '' : 'gap'}" data-i="${i}" data-t="${l.t}">${l.text ? esc(l.text) : '• • •'}</p>`).join('')}
        ${lyrics.source === 'remote' ? '<div class="lyrics-source">Lyrics from LRCLIB</div>' : ''}</div>`;
    } else if (lyrics?.plain_text) {
      this.el.innerHTML = `<div class="lyrics plain">${lyrics.plain_text.split('\n').map((l) => `<p class="lyric-line">${esc(l) || '&nbsp;'}</p>`).join('')}
        <div class="lyrics-source">Unsynced lyrics</div></div>`;
    } else {
      this.el.innerHTML = emptyHtml;
    }
  }

  update(ms) {
    if (!this.lines) return;
    const i = activeIndex(this.lines, ms + this.offset);
    if (i === this.current) return;
    this.current = i;
    const nodes = this.el.querySelectorAll('.lyric-line');
    nodes.forEach((n, k) => {
      n.classList.toggle('active', k === i);
      n.classList.toggle('past', k < i);
    });
    if (i >= 0 && performance.now() > this.userScrollUntil && this.scroller.offsetParent !== null) {
      const node = nodes[i];
      const top = node.offsetTop - this.scroller.clientHeight * 0.35;
      this.scroller.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    }
  }

  /** {now, next} lines for compact previews */
  static preview(lyrics, ms) {
    const lines = lyrics?.synced_lines;
    if (!lines?.length) return null;
    const i = activeIndex(lines, ms + (lyrics.offset_ms || 0));
    const now = i >= 0 ? lines[i].text : lines[0].text;
    const next = lines[i + 1]?.text || '';
    return { now: now || '• • •', next };
  }
}
