// Minimal promise wrapper around IndexedDB (no dependencies).
//   downloads : one record per downloaded track   (keyPath: id)
//   pending   : plays logged while offline         (autoIncrement)
const DB_NAME = 'wonderplay';
const DB_VERSION = 1;
let dbp = null;

function open() {
  if (dbp) return dbp;
  dbp = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('downloads')) db.createObjectStore('downloads', { keyPath: 'id' });
      if (!db.objectStoreNames.contains('pending')) db.createObjectStore('pending', { autoIncrement: true });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => { dbp = null; reject(req.error); };
    req.onblocked = () => console.warn('IndexedDB upgrade blocked by another tab');
  });
  return dbp;
}

async function tx(store, mode, fn) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const s = t.objectStore(store);
    let result;
    Promise.resolve(fn(s)).then((r) => { result = r; });
    t.oncomplete = () => resolve(result);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  });
}

const reqP = (r) => new Promise((resolve, reject) => { r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });

export const idb = {
  get: (store, key) => tx(store, 'readonly', (s) => reqP(s.get(key))),
  all: (store) => tx(store, 'readonly', (s) => reqP(s.getAll())),
  allWithKeys: (store) => tx(store, 'readonly', async (s) => {
    const [keys, vals] = await Promise.all([reqP(s.getAllKeys()), reqP(s.getAll())]);
    return keys.map((k, i) => ({ key: k, value: vals[i] }));
  }),
  put: (store, value, key) => tx(store, 'readwrite', (s) => reqP(key === undefined ? s.put(value) : s.put(value, key))),
  del: (store, key) => tx(store, 'readwrite', (s) => reqP(s.delete(key))),
  clear: (store) => tx(store, 'readwrite', (s) => reqP(s.clear())),
};
