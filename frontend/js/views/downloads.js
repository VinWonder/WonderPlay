// Downloads: everything stored on this device for offline listening.
import { api } from '../api.js';
import { downloads, fmtBytes } from '../offline/downloads.js';
import { render } from '../router.js';
import { store } from '../store.js';
import { emptyState, html, mount, plural, registerList, toast, trackTable } from '../ui.js';

async function setExtra(key, value) {
  const extra = { ...(store.prefs?.extra || {}), [key]: value };
  store.prefs = await api.patch('/api/preferences', { extra });
  store.emit('prefs', store.prefs);
}

function activeHtml() {
  const rows = [...downloads.active.values()];
  if (!rows.length) return '';
  return html`<div class="panel">
    <h3>Downloading ${rows.length}</h3>
    ${rows.map((r) => {
      const pct = r.total ? Math.round((r.received / r.total) * 100) : 0;
      return html`<div class="mt-3"><div class="d-flex justify-content-between small mb-1"><span>${r.title}</span>
        <span class="muted">${r.total ? `${pct}%` : fmtBytes(r.received)}</span></div>
        <div class="wp-progress"><span style="width:${pct}%"></span></div></div>`;
    })}</div>`;
}

export async function downloadsView() {
  if (!downloads.supported) {
    return { title: 'Downloads', html: emptyState({ icon: 'cloud-slash', title: 'Downloads need a modern browser',
      text: 'This browser cannot store music for offline listening.' }) };
  }
  const recs = [...downloads.records.values()].sort((a, b) => b.added_at.localeCompare(a.added_at));
  const tracks = recs.map((r) => r.track);
  const list = registerList(tracks, { type: 'library' });
  const st = await downloads.storage();
  const extra = store.prefs?.extra || {};
  const usedPct = st.quota ? Math.min(100, (st.usage / st.quota) * 100) : 0;

  return {
    title: 'Downloads',
    html: html`
      <h1 class="page-title">Downloads</h1>
      <section class="panel">
        <div class="d-flex justify-content-between align-items-start gap-3 flex-wrap">
          <div>
            <h3>${plural(recs.length, 'song')} • ${fmtBytes(downloads.totalBytes())}</h3>
            <div class="sub mb-2">Available without an internet connection. Seeking works offline too.</div>
          </div>
          ${st.persisted ? html`<span class="pill-btn" style="cursor:default"><i class="bi bi-shield-check"></i> Protected from cleanup</span>`
            : html`<button class="pill-btn" type="button" id="persist-btn" title="Ask the browser not to delete downloads when space runs low">
                <i class="bi bi-shield"></i> Keep downloads safe</button>`}
        </div>
        ${st.quota ? html`<div class="wp-progress mt-2"><span style="width:${usedPct}%"></span></div>
          <div class="small muted mt-1">${fmtBytes(st.usage)} used of ${fmtBytes(st.quota)} available to WonderPlay</div>` : ''}
        <div class="setting-row mt-3">
          <div><div class="label">Download Liked Songs</div><div class="hint">Keep every liked song on this device automatically</div></div>
          <div class="form-check form-switch m-0"><input class="form-check-input" type="checkbox" role="switch" id="dl-liked"
            ${extra.offline_liked ? 'checked' : ''} aria-label="Download Liked Songs"></div>
        </div>
        <div class="setting-row">
          <div><div class="label">Include music videos</div><div class="hint">Also save the video (uses a lot more space)</div></div>
          <div class="form-check form-switch m-0"><input class="form-check-input" type="checkbox" role="switch" id="dl-video"
            ${extra.download_video ? 'checked' : ''} aria-label="Include music videos"></div>
        </div>
      </section>
      <div id="dl-active">${activeHtml()}</div>
      ${recs.length ? html`
        <div class="action-row">
          <button class="big-play" type="button" data-action="play-list" data-list="${list}" data-index="0" aria-label="Play downloads">
            <i class="bi bi-play-fill"></i></button>
          <button class="icon-btn" type="button" data-action="shuffle-list" data-list="${list}" aria-label="Shuffle"><i class="bi bi-shuffle"></i></button>
          <button class="pill-btn ms-auto" type="button" id="dl-clear"><i class="bi bi-trash3"></i> Remove all</button>
        </div>
        ${trackTable(tracks, { context: { type: 'library' } })}`
      : emptyState({ icon: 'arrow-down-circle', title: 'Nothing downloaded yet',
          text: 'Use the ••• menu on any song and choose Download, or press the download button on an album or playlist.',
          action: html`<a class="pill-btn" href="#/search">Find music</a>` })}
    `,
    mount(root, ctx) {
      root.querySelector('#persist-btn')?.addEventListener('click', async () => {
        toast((await downloads.persist()) ? 'Downloads are protected' : 'The browser declined; downloads may be cleared if space runs low');
        render();
      });
      root.querySelector('#dl-liked').addEventListener('change', async (e) => {
        await setExtra('offline_liked', e.target.checked);
        if (e.target.checked) downloads.syncOffline();
      });
      root.querySelector('#dl-video').addEventListener('change', (e) => setExtra('download_video', e.target.checked));
      root.querySelector('#dl-clear')?.addEventListener('click', async () => {
        if (!confirm('Remove all downloaded music from this device?')) return;
        await downloads.removeAll();
        render();
      });
      let count = downloads.records.size;
      const off = store.on('downloads', () => {
        if (!ctx.isCurrent()) { off(); return; }
        if (downloads.records.size !== count) { count = downloads.records.size; off(); render(); return; }
        mount(root.querySelector('#dl-active'), activeHtml());
      });
    },
  };
}
