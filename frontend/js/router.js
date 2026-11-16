// Hash router: #/album/12, #/search/daft%20punk, #/import?q=...&track=5
// Hash URLs work with plain static hosting and offline (Phase 5) without
// any server-side fallback route.
import { markPlaying, mount, setAmbient, skeleton, emptyState, html } from './ui.js';

const routes = [];
let renderToken = 0;
let controller = null;

export function route(pattern, loader, { nav, skeletonKind } = {}) {
  routes.push({ pattern, loader, nav, skeletonKind });
}

export function currentPath() {
  return decodeURI(location.hash.slice(1)) || '/';
}

export function navigate(path, { replace = false } = {}) {
  const target = `#${path}`;
  if (replace) history.replaceState(null, '', target);
  else if (location.hash !== target) location.hash = target;
  if (replace) render();
}

function setActiveNav(nav) {
  const navs = Array.isArray(nav) ? nav : [nav];
  document.querySelectorAll('[data-nav]').forEach((a) => {
    a.classList.toggle('active', navs.includes(a.dataset.nav));
  });
}

export async function render() {
  const full = currentPath();
  const [path, qs = ''] = full.split('?');
  const view = document.getElementById('view');
  const main = document.getElementById('main');

  let match = null;
  let entry = null;
  for (const r of routes) {
    match = path.match(r.pattern);
    if (match) { entry = r; break; }
  }

  controller?.abort();
  controller = new AbortController();
  const token = ++renderToken;

  if (!entry) {
    setActiveNav(null);
    setAmbient(null);
    mount(view, emptyState({ icon: 'compass', title: 'Page not found',
      text: "We couldn't find that page.", action: html`<a class="pill-btn" href="#/">Go home</a>` }));
    return;
  }

  setActiveNav(typeof entry.nav === 'function' ? entry.nav(match) : entry.nav);
  const ctx = {
    params: match.slice(1).map((p) => (p === undefined ? undefined : decodeURIComponent(p))),
    query: new URLSearchParams(qs),
    signal: controller.signal,
    isCurrent: () => token === renderToken,
  };

  // Show a skeleton only if loading takes long enough to notice.
  const skelTimer = setTimeout(() => {
    if (token === renderToken) mount(view, skeleton(entry.skeletonKind));
  }, 120);

  try {
    const out = await entry.loader(ctx);
    clearTimeout(skelTimer);
    if (token !== renderToken) return;   // a newer navigation won
    setAmbient(out.ambient);
    document.title = out.title ? `${out.title} – WonderPlay` : 'WonderPlay';
    mount(view, out.html);
    if (!out.keepScroll) main.scrollTo({ top: 0 });
    markPlaying(view);
    out.mount?.(view, ctx);
  } catch (err) {
    clearTimeout(skelTimer);
    if (err.name === 'AbortError' || token !== renderToken) return;
    console.error(err);
    mount(view, emptyState({
      icon: err.status === 404 ? 'question-circle' : 'exclamation-triangle',
      title: err.status === 404 ? 'Not found' : 'Something went wrong',
      text: err.message,
      action: html`<button class="pill-btn" type="button" data-action="retry">Try again</button>`,
    }));
  }
}

export function startRouter() {
  window.addEventListener('hashchange', render);
  render();
}
