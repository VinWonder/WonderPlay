// Offline play history: plays that couldn't be sent are kept in IndexedDB
// and uploaded with was_offline=true when the connection returns.
import { store } from '../store.js';
import { idb } from './idb.js';

let flushing = false;

export async function flushPendingPlays() {
  if (flushing || !navigator.onLine) return;
  flushing = true;
  try {
    const rows = await idb.allWithKeys('pending');
    if (!rows.length) return;
    const body = rows.map((r) => ({ ...r.value, was_offline: true }));
    const res = await fetch('/api/history/batch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (res.ok) {
      for (const r of rows) await idb.del('pending', r.key);
    }
  } catch {
    /* still offline / server down: try again later */
  } finally {
    flushing = false;
  }
}

export function initOfflineSync() {
  store.on('history:failed', (play) => {
    idb.put('pending', play).catch(() => {});
  });
  window.addEventListener('online', () => {
    document.body.classList.remove('offline');
    store.emit('connectivity', true);
    flushPendingPlays();
  });
  window.addEventListener('offline', () => {
    document.body.classList.add('offline');
    store.emit('connectivity', false);
  });
  document.body.classList.toggle('offline', !navigator.onLine);
  flushPendingPlays();
}
