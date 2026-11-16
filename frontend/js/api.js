// Thin fetch wrapper around the WonderPlay FastAPI backend.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, { params, body, signal } = {}) {
  const url = new URL(path, location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
    }
  }
  const init = { method, signal, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, init);
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new ApiError(0, 'Cannot reach the WonderPlay server. Is it running?');
  }
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    window.dispatchEvent(new CustomEvent('wp:unauthorized'));
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    let detail = data?.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join('; ');
    throw new ApiError(res.status, detail || `${res.status} ${res.statusText}`);
  }
  return data;
}

export const api = {
  get: (path, params, opts) => request('GET', path, { params, ...opts }),
  post: (path, body, opts) => request('POST', path, { body: body ?? {}, ...opts }),
  patch: (path, body) => request('PATCH', path, { body }),
  put: (path, body) => request('PUT', path, { body }),
  del: (path) => request('DELETE', path),
};

export function coverUrl(path, size = 300) {
  if (!path) return 'img/placeholder.svg';
  return `/api/covers/${path}${size ? `?size=${size}` : ''}`;
}
