// Same-origin client for the local course bridge (`alearn course serve`).
// The server is the only source of truth: every mutation carries the revision the
// UI last saw plus a per-click idempotency key, so a duplicate click, a retry
// after a dropped response, or a stale second tab cannot double-apply anything.
export const UI_SCHEMA = 2;

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || body?.error || `HTTP ${status}`);
    this.status = status;
    this.body = body || {};
    this.code = body?.error;
  }
}

async function request(path, body) {
  const init = body === undefined ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ui_schema: UI_SCHEMA, ...body }) };
  let res;
  try {
    res = await fetch(path, { ...init, cache: 'no-store', credentials: 'same-origin' });
  } catch {
    throw new ApiError(0, { error: 'offline', message: 'Lokal kurs serveri cavab vermir.' });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

export function newKey(prefix = 'ui') {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return `${prefix}-${Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')}`;
}

export const getState = () => request('/api/course/state');
export const getLesson = node => request(`/api/course/lesson?node=${encodeURIComponent(node)}`);
export const resume = () => request('/api/course/resume', {});

// One logical user action = one idempotency key, reused on network retry.
export async function mutate(path, revision, payload = {}, key = newKey()) {
  try {
    return await request(path, { ...payload, expected_revision: revision, idempotency_key: key });
  } catch (err) {
    if (err.status === 0) return request(path, { ...payload, expected_revision: revision, idempotency_key: key });
    throw err;
  }
}

export function postActivity(interval, { beacon = false } = {}) {
  const body = JSON.stringify({ ui_schema: UI_SCHEMA, interval });
  if (beacon && navigator.sendBeacon) {
    return navigator.sendBeacon('/api/course/activity', new Blob([body], { type: 'application/json' }));
  }
  return fetch('/api/course/activity', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true })
    .catch(() => null);
}
