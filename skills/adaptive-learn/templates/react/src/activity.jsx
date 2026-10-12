import { postActivity } from './api.jsx';

// Active-study heuristic (documented, deliberately conservative):
//   active = tab visible AND window focused AND keyboard/pointer/scroll input in the last 60 s
//   idle   = page open but not meeting the active rule
//   paused = the learner pressed "Fasilə"
// Counters are cumulative per interval and sent with a rising `seq`; the server keeps
// only the highest seq per interval id, so retries, refreshes and restarts never
// double-count. Reading without input for >60 s is under-counted, and work done in
// the IDE is invisible here — time is never treated as evidence of learning.
const ACTIVE_WINDOW_MS = 60_000;
const FLUSH_MS = 60_000;   // one durable write per active minute (ledger replay cost grows with history)
const ROTATE_SECONDS = 3 * 3600;

function intervalId() {
  const b = new Uint8Array(12);
  crypto.getRandomValues(b);
  return 'iv_' + Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
}

export class ActivityTracker {
  constructor() {
    this.current = null;
    this.paused = false;
    this.lastInput = Date.now();
    this.lastFlush = 0;
    const touch = () => { this.lastInput = Date.now(); };
    for (const ev of ['keydown', 'pointerdown', 'pointermove', 'wheel', 'scroll', 'touchstart']) {
      window.addEventListener(ev, touch, { passive: true });
    }
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') this.flush({ beacon: true });
    });
    window.addEventListener('pagehide', () => this.end({ beacon: true }));
    this.timer = setInterval(() => this.tick(), 1000);
  }

  context(kind, nodeId = null, attemptId = null) {
    const c = this.current;
    if (c && c.kind === kind && c.node_id === nodeId && c.attempt_id === attemptId) return;
    this.end();
    const now = new Date().toISOString();
    this.current = { interval_id: intervalId(), kind, node_id: nodeId, attempt_id: attemptId, started_at: now,
                     last_at: now, active_seconds: 0, idle_seconds: 0, paused_seconds: 0, seq: 0, ended: false };
  }

  tick() {
    const c = this.current;
    if (!c) return;
    if (this.paused) c.paused_seconds += 1;
    else if (document.visibilityState === 'visible' && document.hasFocus() && Date.now() - this.lastInput < ACTIVE_WINDOW_MS) c.active_seconds += 1;
    else c.idle_seconds += 1;
    c.last_at = new Date().toISOString();
    if (Date.now() - this.lastFlush > FLUSH_MS) this.flush();
    // The server caps one interval; rotate long sessions into a fresh interval id.
    if (c.active_seconds + c.idle_seconds + c.paused_seconds >= ROTATE_SECONDS) {
      const { kind, node_id: nodeId, attempt_id: attemptId } = c;
      this.end();
      this.context(kind, nodeId, attemptId);
    }
  }

  flush({ beacon = false } = {}) {
    const c = this.current;
    if (!c || c.active_seconds + c.idle_seconds + c.paused_seconds === 0) return;
    c.seq += 1;
    this.lastFlush = Date.now();
    postActivity({ ...c }, { beacon });
  }

  end(opts = {}) {
    if (!this.current) return;
    this.current.ended = true;
    this.flush(opts);
    this.current = null;
  }

  setPaused(paused) {
    this.paused = paused;
    this.flush();
  }
}
