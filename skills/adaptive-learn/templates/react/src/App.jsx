import React, { useCallback, useEffect, useRef, useState } from 'react';
import snapshot from './data/roadmap.json';
import Roadmap, { ApprovalPanel, NodeDetail } from './components/Roadmap.jsx';
import Lesson from './components/Lesson.jsx';
import Practice from './components/Practice.jsx';
import { Exam, MissionCard, Result } from './components/Assessment.jsx';
import { NODE_STATUS, resourceStatus } from './components/ui.jsx';
import { ApiError, getLesson, mutate, newKey, resume, UI_SCHEMA } from './api.jsx';
import { ActivityTracker } from './activity.jsx';

// The local course server is the only source of truth. The UI hydrates from it on
// every load, guards routes against the persisted phase (an active exam always wins),
// and only mutates through revision-checked, idempotent calls.
const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('alearn-course') : null;
const tracker = typeof window !== 'undefined' ? new ActivityTracker() : null;

function parseRoute(hash) {
  const [, page = '', id = ''] = (hash || '#/').replace(/^#/, '').split('/');
  return { page, id: decodeURIComponent(id) };
}

export default function App() {
  const [view, setView] = useState(null);
  const [offline, setOffline] = useState(false);
  const [route, setRoute] = useState(parseRoute(window.location.hash));
  const [toast, setToast] = useState(null);
  const [busy, setBusy] = useState(false);
  const [lesson, setLesson] = useState(null);
  const [selected, setSelected] = useState(null);
  const [paused, setPaused] = useState(false);
  const [examConfirm, setExamConfirm] = useState(null);
  const [bootError, setBootError] = useState(null);
  const mainRef = useRef(null);
  const inFlight = useRef(false);

  const go = useCallback(hash => { if (window.location.hash !== hash) window.location.hash = hash; }, []);

  const refresh = useCallback(async () => {
    // Bounded retry for transient refusals (busy server); never an endless spinner.
    for (let attempt = 0; attempt < 4; attempt += 1) {
      try {
        const v = await resume();
        if (v.ui_schema_version !== UI_SCHEMA) { setToast('Bu interfeys köhnə versiyadır. Səhifəni yenilə.'); }
        setView(v);
        setOffline(false);
        setBootError(null);
        return;
      } catch (err) {
        if (err.status === 0) { setOffline(true); return; }
        if ((err.status === 429 || err.status === 503) && attempt < 3) {
          await new Promise(r => setTimeout(r, 400 * 2 ** attempt));
          continue;
        }
        setBootError(err.message);
        setToast(err.message);
        return;
      }
    }
  }, []);

  useEffect(() => {
    refresh();
    const onHash = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener('hashchange', onHash);
    const onMsg = () => refresh();
    channel?.addEventListener('message', onMsg);
    const onFocus = () => refresh();
    window.addEventListener('focus', onFocus);
    return () => { window.removeEventListener('hashchange', onHash); channel?.removeEventListener('message', onMsg);
                   window.removeEventListener('focus', onFocus); };
  }, [refresh]);

  // Route guard: an active or pending exam owns the whole app until it is finalized.
  useEffect(() => {
    if (!view) return;
    const att = view.active_attempt;
    if (view.teaching_locked && att && !(route.page === 'exam' && route.id === att.attempt_id)) {
      if (route.page && route.page !== 'exam') setToast('İmtahan davam edir — dərslər imtahan bitənə qədər bağlıdır.');
      window.location.replace(`#/exam/${att.attempt_id}`);
      return;
    }
    if (view.phase === 'roadmap_awaiting_approval' && ['learn', 'practice'].includes(route.page)) {
      window.location.replace('#/roadmap');
    }
  }, [view, route]);

  useEffect(() => {
    // Never ask for teaching content the server would refuse (unapproved roadmap, exam lock).
    if (!view || (route.page !== 'learn' && route.page !== 'practice') || view.teaching_locked || !view.roadmap.approved) {
      setLesson(null);
      return;
    }
    let live = true;
    getLesson(route.id).then(l => { if (live) setLesson(l); }).catch(err => { if (live) setToast(err.message); });
    return () => { live = false; };
  }, [route, view?.revision, view?.teaching_locked]);

  useEffect(() => {   // engagement context follows the visible work
    if (!tracker || !view) return;
    const kind = { exam: 'exam', learn: 'teaching', practice: 'practice', result: 'review', roadmap: 'roadmap', progress: 'review' }[route.page] || 'other';
    tracker.context(kind, route.page === 'exam' ? view.active_attempt?.node_id ?? null : (route.page === 'learn' || route.page === 'practice') ? route.id : null,
                    route.page === 'exam' ? view.active_attempt?.attempt_id ?? null : null);
  }, [route, view?.active_attempt?.attempt_id]);

  useEffect(() => { mainRef.current?.querySelector('h1')?.focus(); }, [route.page, route.id]);

  const act = useCallback(async (path, payload = {}) => {
    // One mutation at a time: a fast double click must not send a second request
    // (the server would dedupe it anyway, but the learner would see a needless conflict).
    if (!view || inFlight.current) return null;
    inFlight.current = true;
    setBusy(true);
    try {
      const out = await mutate(path, view.revision, payload, newKey());
      channel?.postMessage('changed');
      await refresh();
      return out;
    } catch (err) {
      if (err instanceof ApiError && err.code === 'revision_conflict' && err.body.state) {
        setView(err.body.state);
        setToast('Vəziyyət başqa pəncərədə dəyişib; ən son vəziyyət göstərilir. Addımı yenidən et.');
      } else if (err instanceof ApiError && err.status === 0) {
        setOffline(true);
      } else {
        setToast(err.message);
        await refresh();
      }
      return null;
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, [view, refresh]);

  const confirmExam = (node, fastTrack) => setExamConfirm({ node, fastTrack });
  const startExam = async () => {
    const { node, fastTrack } = examConfirm;
    setExamConfirm(null);
    const out = await act('/api/course/exam/start', { node, fast_track: fastTrack });
    if (out?.attempt) go(`#/exam/${out.attempt.attempt_id}`);
  };

  if (offline && !view) return <OfflineSnapshot />;
  if (!view && bootError) {
    return <main className="boot"><p role="alert" className="warn">Kurs vəziyyəti alınmadı: {bootError}</p>
      <button type="button" className="primary" onClick={refresh}>Yenidən cəhd et</button></main>;
  }
  if (!view) return <main className="boot"><p role="status">Kurs vəziyyəti yüklənir…</p></main>;

  const node = view.roadmap.nodes.find(n => n.id === (selected || view.active_node_id)) || null;
  let page;
  if (route.page === 'roadmap') {
    page = <section aria-labelledby="map-title"><h1 id="map-title" tabIndex={-1}>Öyrənmə yol xəritəsi</h1>
      <p className="lead">{view.roadmap.goal}</p>
      <div className="map-layout">
        <Roadmap nodes={view.roadmap.nodes} selected={node?.id} onSelect={setSelected} />
        <NodeDetail node={node} nodes={view.roadmap.nodes} sources={view.sources.resources} />
      </div>
      {view.phase === 'roadmap_awaiting_approval'
        ? <ApprovalPanel roadmap={view.roadmap} busy={busy}
                         onApprove={async commitments => { if (await act('/api/course/approve', { roadmap_version: view.roadmap.version, commitments })) go('#/'); }} />
        : view.roadmap.approval_event && <p className="muted">Təsdiqləndi: {new Date(view.roadmap.approval_event.approved_at).toLocaleString('az-AZ')} · versiya <code>{view.roadmap.approved_version}</code></p>}
    </section>;
  } else if (route.page === 'learn') {
    page = <Lesson key={route.id} view={view} nodeId={route.id} info={lesson} act={act} go={go} confirmExam={confirmExam} />;
  } else if (route.page === 'practice') {
    page = view.phase === 'practice' && view.active_node_id === route.id
      ? <Practice key={route.id} view={view} nodeId={route.id} info={lesson} act={act} confirmExam={confirmExam} />
      : <section><h1 tabIndex={-1}>Praktika</h1><p>Praktika aktiv mərhələ üçün başlayır.</p>
          <button type="button" className="primary" onClick={async () => { if (await act('/api/course/practice/begin', { node: route.id })) refresh(); }}>Praktikaya başla</button></section>;
  } else if (route.page === 'exam') {
    page = <Exam view={view} act={act} go={go} />;
  } else if (route.page === 'result') {
    page = <Result view={view} act={act} go={go} />;
  } else if (route.page === 'progress') {
    page = <Progress view={view} />;
  } else {
    page = <Home view={view} act={act} go={go} confirmExam={confirmExam} />;
  }

  return <div className="app">
    <a className="skip" href="#main">Əsas məzmuna keç</a>
    <header>
      <a className="brand" href="#/">◈ Adaptive Learning</a>
      <nav aria-label="Bölmələr">
        <a href="#/" aria-current={route.page === '' ? 'page' : undefined}>Missiya</a>
        <a href="#/roadmap" aria-current={route.page === 'roadmap' ? 'page' : undefined}>Yol xəritəsi</a>
        <a href="#/progress" aria-current={route.page === 'progress' ? 'page' : undefined}>Proqres</a>
      </nav>
      <span className="phase" aria-label="Mərhələ">{view.phase_label_az}</span>
      <button type="button" className="pause" aria-pressed={paused} onClick={() => { tracker?.setPaused(!paused); setPaused(!paused); }}>
        {paused ? 'Davam et' : 'Fasilə'}</button>
    </header>
    {view.accountability?.open && !view.teaching_locked && <Accountability item={view.accountability.open} act={act} busy={busy} />}
    <main id="main" ref={mainRef}>{page}</main>
    {examConfirm && <div className="modal" role="dialog" aria-modal="true" aria-labelledby="exam-confirm">
      <div className="card"><h2 id="exam-confirm">Müstəqil yoxlamaya başlayaq?</h2>
        <p>Görmədiyin yeni tapşırıq veriləcək. Cəhd bitənə qədər izah və ipucu bağlı olacaq; yarımçıq çıxsan, cəhd uğur kimi sayılmayacaq.</p>
        <button type="button" className="primary" onClick={startExam} autoFocus>Başla</button>{' '}
        <button type="button" onClick={() => setExamConfirm(null)}>Hələ yox</button></div></div>}
    <div className="toast" role="status" aria-live="polite">{toast && <><span>{toast}</span> <button type="button" onClick={() => setToast(null)} aria-label="Bağla">×</button></>}</div>
    {offline && <p className="offline" role="alert">Lokal server cavab vermir. Dəyişikliklər saxlanmır; `alearn course serve` ilə yenidən başlat.</p>}
    <footer>Vəziyyət yalnız lokal runtime-da saxlanır (revision {view.revision}). {view.honest_boundaries_az[0]}</footer>
  </div>;
}

function Home({ view, act, go, confirmExam }) {
  if (view.phase === 'onboarding' || view.phase === 'source_preparation') {
    return <section><h1 tabIndex={-1}>Hazırlıq</h1>
      <p className="lead">{view.goal?.text || 'Məqsəd hələ qeyd olunmayıb.'}</p>
      <p>Agent səninlə qısa söhbətdə məqsədini, vaxtını və başlanğıc səviyyəni dəqiqləşdirir, sonra mənbələri NotebookLM-də hazırlayır. Bu profil təxminidir və sonrakı nəticələrlə yenilənir.</p>
      <Sources view={view} /></section>;
  }
  if (view.phase === 'roadmap_awaiting_approval') {
    return <section><h1 tabIndex={-1}>Yol xəritəsi hazırdır</h1>
      <p>Davam etmək üçün yol xəritəsini nəzərdən keçir və təsdiq et. Təsdiqdən əvvəl dərs yaradılmır.</p>
      <a className="button primary" href="#/roadmap">Yol xəritəsini aç</a></section>;
  }
  const active = view.roadmap.nodes.find(n => n.id === view.active_node_id);
  return <section aria-labelledby="home-title"><h1 id="home-title" tabIndex={-1}>{view.roadmap.topic}</h1>
    <p className="lead">{view.goal?.text}</p>
    {view.goal?.target && <p className="muted">Hədəf: {view.goal.target.official_name}{view.goal.target.code ? ` (${view.goal.target.code})` : ''} · yoxlanılıb {view.goal.target.verified_at?.slice(0, 10)}{view.goal.target.version ? ` · versiya ${view.goal.target.version}` : ' · imtahan versiyası rəsmi səhifədə göstərilmir'}</p>}
    {active && ['teaching', 'practice'].includes(view.phase) && <article className="card"><p className="eyebrow">Davam et</p><h2>{active.title}</h2>
      <a className="button primary" href={view.phase === 'practice' ? `#/practice/${active.id}` : `#/learn/${active.id}`}>İndi et</a></article>}
    {view.phase === 'feedback' && view.assessment_result && <article className="card"><p>Son nəticə hazırdır.</p>
      <a className="button primary" href={`#/result/${view.assessment_result.attempt_id}`}>Nəticəyə bax</a></article>}
    <MissionCard view={view} act={act} go={go} confirmExam={confirmExam} />
  </section>;
}

function Accountability({ item, act, busy }) {
  const [blocker, setBlocker] = useState('time');
  const [note, setNote] = useState('');
  const labels = { time: 'Vaxt', difficulty: 'Mövzu çətin gəldi', unclear_material: 'Material aydın deyildi', motivation: 'Motivasiya',
                   health_or_personal: 'Şəxsi səbəb', tooling: 'Alət/kompüter problemi', other: 'Başqa' };
  return <form className="accountability card" role="region" aria-label="Razılaşma yoxlaması"
               onSubmit={async e => { e.preventDefault(); await act('/api/course/accountability', { blocker, note }); }}>
    <h2>Razılaşmamıza qısa baxış</h2>
    <p>{item.message_az}</p>
    <p><strong>{item.question_az}</strong></p>
    <fieldset><legend className="sr-only">Səbəb</legend>
      {Object.entries(labels).map(([k, v]) => <label key={k} className="radio"><input type="radio" name="blocker" value={k} checked={blocker === k} onChange={() => setBlocker(k)} /> {v}</label>)}
    </fieldset>
    <label>Qeyd (istəyə bağlı) <input value={note} onChange={e => setNote(e.target.value)} maxLength={500} /></label>
    <p>Bərpa tapşırığı: {item.recovery_task.text_az}</p>
    <button type="submit" className="primary" disabled={busy}>Göndər və bərpa tapşırığını qəbul et</button>
  </form>;
}

function Sources({ view }) {
  const res = view.sources.resources || [];
  return <section className="card"><h2>Mənbələr</h2>
    {!res.length ? <p>Mənbə manifesti hələ yoxdur.</p> :
      <table><thead><tr><th scope="col">Mənbə</th><th scope="col">Vəziyyət</th><th scope="col">Qeyd</th></tr></thead>
        <tbody>{res.map(r => <tr key={r.key}><td>{r.title || r.url}</td><td>{resourceStatus(r.status)}{r.stale ? ' · köhnəlmiş ola bilər' : ''}</td>
          <td>{(r.issues || []).join(', ') || '—'}</td></tr>)}</tbody></table>}
    {view.sources.unexpected?.length > 0 && <p className="muted">Notebook-da gözlənilməyən {view.sources.unexpected.length} mənbə var; dərslər onlara əsaslanmır.</p>}
  </section>;
}

function Progress({ view }) {
  const eng = view.engagement;
  const kinds = { teaching: 'İzah', practice: 'Praktika', help: 'Kömək', retry: 'Təkrar cəhd', review: 'Nəzərdən keçirmə', exam: 'İmtahan', roadmap: 'Yol xəritəsi', other: 'Digər' };
  return <section aria-labelledby="progress-title"><h1 id="progress-title" tabIndex={-1}>Proqres və sübut</h1>
    <div className="card"><h2>Mərhələlər</h2><ul>{view.roadmap.nodes.map(n => <li key={n.id}>{n.title} — {NODE_STATUS[n.status]}</li>)}</ul></div>
    <div className="card"><h2>Müstəqil nəticələr</h2>
      {view.evidence_refs.length ? <ul>{view.evidence_refs.map(e => <li key={e.evidence_id}>{e.node_id}: {e.outcome} · <code>{e.evidence_id}</code></li>)}</ul>
        : <p>Hələ yekunlaşmış müstəqil yoxlama yoxdur.</p>}</div>
    <div className="card"><h2>Aktiv vaxt</h2>
      <ul>{Object.entries(eng.by_kind).map(([k, v]) => <li key={k}>{kinds[k] || k}: {Math.round(v.active_seconds / 60)} dəq aktiv, {Math.round(v.idle_seconds / 60)} dəq boş</li>)}</ul>
      <p className="muted">{eng.method_az}</p></div>
    <div className="card"><h2>Razılaşmalar</h2>
      <ul>{view.learning_commitments.filter(c => c.status === 'active').map(c => <li key={c.id}>{c.agreed_text || c.kind}{c.due ? ` · ${c.due}` : ''}</li>)}</ul></div>
    <Sources view={view} />
    <div className="card"><h2>Bu sistemin sərhədləri</h2><ul>{view.honest_boundaries_az.map((b, i) => <li key={i}>{b}</li>)}</ul>
      <a href="/api/course/export" target="_blank" rel="noreferrer">Bütün məlumatı ixrac et (JSON)</a></div>
  </section>;
}

function OfflineSnapshot() {
  return <div className="app"><header><span className="brand">◈ Adaptive Learning</span></header>
    <main><p role="alert" className="warn">Lokal kurs serveri işləmir. Bu yalnız yaradılma anındakı statik baxışdır — proqres, dərslər və imtahan burada işləmir. Başlatmaq üçün: <code>alearn course serve --session …</code></p>
      <h1>{snapshot.topic}</h1><p>{snapshot.goal}</p>
      <ol>{snapshot.nodes.map(n => <li key={n.id}>{n.title}</li>)}</ol></main></div>;
}
