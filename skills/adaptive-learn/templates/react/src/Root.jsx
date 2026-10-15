import React, { Suspense, lazy, useEffect, useState } from 'react';

// One built bundle serves two separate processes. The learner course server and the owner server (`alearn studio
// serve`) both answer /health with their mode; the owner code is only ever loaded when the server says "owner", and the
// learner app (with its activity tracker) is never loaded there.
const App = lazy(() => import('./App.jsx'));
const Owner = lazy(() => import('./owner/Owner.jsx'));

export default function Root() {
  const [mode, setMode] = useState(null);
  useEffect(() => {
    let live = true;
    fetch('/health', { cache: 'no-store' }).then(r => r.json()).then(d => { if (live) setMode(d?.mode === 'owner' ? 'owner' : 'learner'); })
      .catch(() => { if (live) setMode('learner'); });
    return () => { live = false; };
  }, []);
  const wait = <main className="boot"><p role="status">Yüklənir…</p></main>;
  if (!mode) return wait;
  return <Suspense fallback={wait}>{mode === 'owner' ? <Owner /> : <App />}</Suspense>;
}
