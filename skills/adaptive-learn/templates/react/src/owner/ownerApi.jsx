// Same-origin client for the OWNER server (`alearn studio serve`). The owner token is typed into a form and kept in
// memory plus sessionStorage (this tab only, gone when the tab closes). It is sent only as a Bearer header: never in a
// URL, a cookie, localStorage or a log line.
const KEY = 'alearn-owner-token';
let token = null;
try { token = window.sessionStorage.getItem(KEY); } catch { token = null; }

export const hasToken = () => Boolean(token);
export function setToken(value) {
  token = value || null;
  try {
    if (token) window.sessionStorage.setItem(KEY, token); else window.sessionStorage.removeItem(KEY);
  } catch { /* storage can be blocked: the in-memory token still works for this page view */ }
}

export class OwnerError extends Error {
  constructor(status, body) {
    super(body?.message || body?.detail || body?.error || `HTTP ${status}`);
    this.status = status;
    this.code = body?.error;
    this.body = body || {};
  }
}

async function request(path, body) {
  const headers = { Authorization: `Bearer ${token || ''}` };
  const init = { method: body === undefined ? 'GET' : 'POST', headers, cache: 'no-store', credentials: 'omit' };
  if (body !== undefined) { headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, init); } catch { throw new OwnerError(0, { error: 'offline', message: 'Sahib serveri cavab vermir.' }); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new OwnerError(res.status, data);
  return data;
}

export const getStatus = () => request('/api/studio');
export const getEvents = (limit = 15) => request(`/api/studio/events?limit=${encodeURIComponent(limit)}`);
export const getHandover = () => request('/api/studio/handover');
export const getNode = node => request(`/api/studio/node?node=${encodeURIComponent(node)}`);
export const control = (action, body = {}) => request(`/api/studio/${action}`, body);
