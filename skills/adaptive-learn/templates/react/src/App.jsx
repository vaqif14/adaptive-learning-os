import React, { useCallback, useEffect, useRef, useState } from 'react';
import Roadmap, { ApprovalPanel, NodeDetail } from './components/Roadmap.jsx';
import Lesson from './components/Lesson.jsx';
import Practice from './components/Practice.jsx';
import Intake from './components/Intake.jsx';
import { Exam, MissionCard, Result } from './components/Assessment.jsx';
import { NODE_STATUS, resourceStatus } from './components/ui.jsx';
import ConfirmDialog from './components/ConfirmDialog.jsx';
import { ApiError, getLesson, getState, mutate, newKey, resume, UI_SCHEMA } from './api.jsx';
import { ActivityTracker } from './activity.jsx';

// The local course server is the only source of truth. The UI hydrates from it on
// every load, guards routes against the persisted phase (an active exam always wins),
// and only mutates through revision-checked, idempotent calls.
const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('alearn-course') : null;
const tracker = typeof window !== 'undefined' ? new ActivityTracker() : null;

function parseRoute(hash) {
  const [, page = '', id = ''] = (hash || '#/').replace(/^#/, '').split('/');
  try { return { page, id: decodeURIComponent(id) }; }
  catch { return { page: '', id: '' }; }
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
  const [dialog, setDialog] = useState(null);
  const [bootError, setBootError] = useState(null);
  const mainRef = useRef(null);
  const inFlight = useRef(false);
  const opener = useRef(null);
  const requested = useRef(0);
  const applied = useRef(0);

  const go = useCallback(hash => { if (window.location.hash !== hash) window.location.hash = hash; }, []);

  const refresh = useCallback(async ({ readOnly = false } = {}) => {
    // Bounded retry for transient refusals (busy server); never an endless spinner.
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const mine = ++requested.current;
      try {
        const v = await (readOnly ? getState() : resume());
        // Navigation, focus, other tabs and actions all re-hydrate; never let an older
        // response overwrite a newer one.
        if (mine < applied.current) return;
        applied.current = mine;
        if (v.ui_schema_version !== UI_SCHEMA) { setToast('Bu interfeys köhnə versiyadır. Səhifəni yenilə.'); }
        setView(v);
        setOffline(false);
        setBootError(null);
        return;
      } catch (err) {
        if (err.status === 0) { setOffline(true); return; }
        if ((err.status === 429 || err.status === 503) && attempt < 3) {
          await new Promise(r => { setTimeout(r, 400 * 2 ** attempt); });
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
    // Navigation re-hydrates too: the agent may have authored content, re-validated sources
    // or proposed a roadmap change in the terminal since the last load.
    const onHash = () => { setRoute(parseRoute(window.location.hash)); refresh(); };
    window.addEventListener('hashchange', onHash);
    const onMsg = () => refresh();
    channel?.addEventListener('message', onMsg);
    const onFocus = () => refresh();
    window.addEventListener('focus', onFocus);
    return () => { window.removeEventListener('hashchange', onHash); channel?.removeEventListener('message', onMsg);
                   window.removeEventListener('focus', onFocus); };
  }, [refresh]);

  useEffect(() => {
    // Host preparation/judgment happens outside this tab. Poll its committed view
    // while waiting; GET does not create study events or judge learner work.
    const waiting = offline || ['onboarding', 'source_preparation', 'roadmap_awaiting_approval', 'assessment_pending'].includes(view?.phase);
    if (!waiting) return;
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible' && !inFlight.current) refresh({ readOnly: true });
    }, 5000);
    return () => clearInterval(timer);
  }, [view?.phase, offline, refresh]);

  // Route guard: an active or pending exam owns the whole app until it is finalized.
  useEffect(() => {
    if (!view) return;
    const att = view.active_attempt;
    if (view.teaching_locked && att && !(route.page === 'exam' && route.id === att.attempt_id)) {
      if (route.page && route.page !== 'exam') setToast('İmtahan davam edir — dərslər imtahan bitənə qədər bağlıdır.');
      window.location.replace(`#/exam/${att.attempt_id}`);
      return;
    }
    // An intake shell can contain old placeholder node data. Only a committed
    // roadmap version makes it a proposed course; direct bookmarks cannot bypass it.
    if (!view.roadmap?.version && ['roadmap', 'learn', 'practice', 'progress', 'result'].includes(route.page)) {
      window.location.replace('#/intake');
      return;
    }
    const generationBlocked = view.generation_readiness?.required && !view.generation_readiness.ready;
    if (!view.teaching_locked && view.roadmap?.version && (!view.roadmap.approved || generationBlocked)
        && ['learn', 'practice'].includes(route.page)) {
      window.location.replace('#/roadmap');
      return;
    }
    // A host rubric judgment may finalize while this tab is closed or unfocused.
    // Rehydrating the old exam URL must show the committed result, not a dead end.
    if (route.page === 'exam' && !view.teaching_locked && !att) {
      const result = view.assessment_result;
      window.location.replace(result?.attempt_id === route.id ? `#/result/${result.attempt_id}` : '#/');
      return;
    }
    if (route.page === 'result' && !view.teaching_locked && view.assessment_result
        && route.id !== view.assessment_result.attempt_id) {
      setToast('Bu keçid başqa cəhdə aiddir. Son yekunlaşmış nəticə göstərilir.');
      window.location.replace(`#/result/${view.assessment_result.attempt_id}`);
      return;
    }
    if (view.phase === 'roadmap_awaiting_approval' && ['learn', 'practice'].includes(route.page)) {
      window.location.replace('#/roadmap');
    } else if (view.due_reviews?.length && ['learn', 'practice'].includes(route.page)) {
      setToast('Təkrar yoxlamanın vaxtı çatıb — əvvəl yeni tapşırıqla müstəqil yoxlama.');
      window.location.replace('#/mission');
    }
  }, [view, route]);

  useEffect(() => {
    // Never ask for teaching content the server would refuse (unapproved roadmap, exam lock).
    if (!view || (route.page !== 'learn' && route.page !== 'practice') || view.teaching_locked || view.due_reviews?.length || !view.roadmap.approved
        || (view.generation_readiness?.required && !view.generation_readiness.ready)) {
      setLesson(null);
      return;
    }
    let live = true;
    getLesson(route.id).then(l => { if (live) setLesson(l); }).catch(err => { if (live) setToast(err.message); });
    return () => { live = false; };
  }, [route, view?.revision, view?.teaching_locked, view?.generation_readiness?.ready]);

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

  const confirmExam = (node, fastTrack) => { opener.current = document.activeElement; setExamConfirm({ node, fastTrack }); };
  const closeConfirm = () => setExamConfirm(null);
  // Generic confirmation (reveal, reset): {title, text, confirmLabel, cancelLabel, onConfirm}. Focus starts on the safe choice.
  const askConfirm = cfg => { opener.current = document.activeElement; setDialog(cfg); };
  const runDialog = async () => { const cfg = dialog; setDialog(null); await cfg.onConfirm(); };
  // Focus goes back to the opener only after the page behind stopped being inert.
  useEffect(() => {
    if (!examConfirm && !dialog && opener.current) { opener.current.focus?.(); opener.current = null; }
  }, [examConfirm, dialog]);
  const startExam = async () => {
    const { node, fastTrack } = examConfirm;
    opener.current = null;
    setExamConfirm(null);
    const out = await act('/api/course/exam/start', { node, fast_track: fastTrack });
    if (out?.attempt) go(`#/exam/${out.attempt.attempt_id}`);
  };

  if (offline && !view) return <OfflineSnapshot onRetry={refresh} />;
  if (!view && bootError) {
    return <main className="boot"><p role="alert" className="warn">Kurs vəziyyəti alınmadı: {bootError}</p>
      <button type="button" className="primary" onClick={refresh}>Yenidən cəhd et</button></main>;
  }
  if (!view) return <main className="boot"><p role="status">Kurs vəziyyəti yüklənir…</p></main>;

  const node = view.roadmap.nodes.find(n => n.id === (selected || view.active_node_id)) || null;
  const intakeRequired = view.intake?.required && !view.intake?.ready_to_plan;
  const generationRequired = view.generation_readiness?.required && !view.generation_readiness.ready;
  const hasRoadmap = Boolean(view.roadmap?.version);
  const intakeShell = ['onboarding', 'source_preparation'].includes(view.phase) || !hasRoadmap;
  let page;
  if (!hasRoadmap && ['roadmap', 'learn', 'practice', 'progress', 'result'].includes(route.page)) {
    page = <Home view={view} act={act} go={go} confirmExam={confirmExam} busy={busy} />;
  } else if (route.page === 'roadmap') {
    page = <section aria-labelledby="map-title"><h1 id="map-title" tabIndex={-1}>{generationRequired && view.roadmap.approved ? 'Tarixi yol xəritəsi' : 'Öyrənmə yol xəritəsi'}</h1>
      <p className="lead">{view.roadmap.goal}</p>
      {generationRequired && view.roadmap.approved && <p className="warn" role="status">Bu xəritə əvvəlki məqsəd və mənbə vəziyyətinə aiddir. Hazırkı dərs və praktika bağlıdır; yenilənmiş roadmap ayrıca təklif və təsdiq edilməlidir.</p>}
      <div className="map-layout">
        <Roadmap nodes={view.roadmap.nodes} selected={node?.id} onSelect={setSelected} />
        <NodeDetail node={node} nodes={view.roadmap.nodes} sources={view.sources.resources} />
      </div>
      {intakeRequired && <><p className="warn" role="status">Yol xəritəsini görə bilərsən. Təsdiqdən əvvəl qısa başlanğıc yoxlamasını tamamla və ya uyğun format mümkün deyilsə, bunu səbəbi ilə qeyd et.</p>
        <Intake key={`${view.intake?.action}-${view.intake?.probe?.id || ''}`} view={view} act={act} busy={busy} /></>}
      {generationRequired && <GenerationReadiness view={view} />}
      {view.phase === 'roadmap_awaiting_approval'
        ? <ApprovalPanel roadmap={view.roadmap} busy={busy || intakeRequired || generationRequired}
                         onApprove={async commitments => { if (await act('/api/course/approve', { roadmap_version: view.roadmap.version, commitments })) go('#/'); }} />
        : view.roadmap.approval_event && <p className="muted">Təsdiqləndi: {new Date(view.roadmap.approval_event.approved_at).toLocaleString('az-AZ')} · versiya <code>{view.roadmap.approved_version}</code></p>}
    </section>;
  } else if (['learn', 'practice'].includes(route.page) && (!view.roadmap.approved || generationRequired)) {
    page = <p role="status">Dərs və praktika üçün hazırkı məqsəd və mənbələr yoxlanmalı, yol xəritəsi təsdiqlənməlidir.</p>;
  } else if (route.page === 'learn') {
    page = <Lesson key={route.id} view={view} nodeId={route.id} info={lesson} act={act} go={go} confirmExam={confirmExam} busy={busy} />;
  } else if (route.page === 'practice') {
    page = view.phase === 'practice' && view.active_node_id === route.id
      ? <Practice key={route.id} view={view} nodeId={route.id} info={lesson} act={act} confirmExam={confirmExam} confirm={askConfirm} busy={busy} />
      : <section><h1 tabIndex={-1}>Praktika</h1><p>Praktika aktiv mərhələ üçün başlayır.</p>
          <button type="button" className="primary" disabled={busy} onClick={async () => { if (await act('/api/course/practice/begin', { node: route.id })) refresh(); }}>Praktikaya başla</button></section>;
  } else if (route.page === 'exam') {
    page = <Exam key={view.active_attempt?.attempt_id || 'none'} view={view} act={act} go={go} busy={busy} />;
  } else if (route.page === 'result') {
    page = view.assessment_result && route.id !== view.assessment_result.attempt_id
      ? <p role="status">Yekunlaşmış nəticəyə yönləndirilir…</p>
      : <Result view={view} act={act} go={go} busy={busy} />;
  } else if (route.page === 'progress') {
    page = <Progress view={view} />;
  } else {
    page = <Home view={view} act={act} go={go} confirmExam={confirmExam} busy={busy} />;
  }

  return <div className="app">
    <a className="skip" href="#main">Əsas məzmuna keç</a>
    <div className="app-body" inert={examConfirm || dialog ? true : undefined}>
    <header>
      <a className="brand" href={intakeShell ? '#/intake' : '#/'}>{intakeShell ? 'Tanışlıq' : '◈ Adaptive Learning'}</a>
      {!intakeShell && !generationRequired && <nav aria-label="Bölmələr">
        <a href="#/" aria-current={route.page === '' ? 'page' : undefined}>Missiya</a>
        <a href="#/roadmap" aria-current={route.page === 'roadmap' ? 'page' : undefined}>Yol xəritəsi</a>
        <a href="#/progress" aria-current={route.page === 'progress' ? 'page' : undefined}>Proqres</a>
      </nav>}
      <span className="phase" aria-label="Mərhələ">{view.phase_label_az}</span>
      <button type="button" className="pause" disabled={busy} onClick={() => refresh({ readOnly: true })}>Vəziyyəti yenilə</button>
      <button type="button" className="pause" aria-pressed={paused} onClick={() => { tracker?.setPaused(!paused); setPaused(!paused); }}>
        {paused ? 'Davam et' : 'Fasilə'}</button>
    </header>
    {view.accountability?.open && !view.teaching_locked && <Accountability item={view.accountability.open} act={act} busy={busy} />}
    <main id="main" ref={mainRef}>{page}</main>
    {offline && <p className="offline" role="alert">Lokal server cavab vermir. Dəyişikliklər saxlanmır; `alearn course serve` ilə yenidən başlat.</p>}
    <footer>{intakeShell ? 'Bu, tanışlıq ekranıdır; kurs hələ yaradılmayıb.' : 'Vəziyyət yalnız lokal runtime-da saxlanır.'} (revision {view.revision}). {view.honest_boundaries_az[0]}</footer>
    </div>
    {examConfirm && <ConfirmDialog onConfirm={startExam} onCancel={closeConfirm} />}
    {dialog && <ConfirmDialog {...dialog} onConfirm={runDialog} onCancel={() => setDialog(null)} />}
    <div className="toast" role="status" aria-live="polite">{toast && <><span>{toast}</span> <button type="button" onClick={() => setToast(null)} aria-label="Bağla">×</button></>}</div>
  </div>;
}

function Home({ view, act, go, confirmExam, busy }) {
  if (view.phase === 'onboarding' || view.phase === 'source_preparation' || !view.roadmap?.version) {
    return <section><h1 tabIndex={-1}>Tanışlıq</h1>
      <p className="lead">{view.goal?.text || 'Məqsəd hələ qeyd olunmayıb.'}</p>
      <p>Bu ekran məqsədini və başlanğıc şərtlərini toplamaq üçündür. Hələ kurs və yol xəritəsi yaradılmayıb.</p>
      <p>Əvvəl konkret məqsəd, sonra təcrübən və qısa tapşırıq. Bu ilkin müşahidə sonrakı nəticələrlə yenilənir.</p>
      <Intake key={`${view.intake?.action}-${view.intake?.probe?.id || ''}`} view={view} act={act} busy={busy} />
      <GenerationReadiness view={view} />
      <Sources view={view} /></section>;
  }
  if (view.generation_readiness?.required && !view.generation_readiness.ready && !view.teaching_locked) {
    return <section><h1 tabIndex={-1}>Kurs yenilənməlidir</h1>
      <GenerationReadiness view={view} />
      <a className="button" href="#/roadmap">Yol xəritəsinə bax (yalnız oxumaq üçün)</a>
      <Sources view={view} /></section>;
  }
  if (view.phase === 'roadmap_awaiting_approval') {
    return <section><h1 tabIndex={-1}>Yol xəritəsi hazırdır</h1>
      <p>Davam etmək üçün yol xəritəsini nəzərdən keçir və təsdiq et. Təsdiqdən əvvəl dərs yaradılmır.</p>
      {view.intake?.required && !view.intake?.ready_to_plan && <Intake key={`${view.intake.action}-${view.intake.probe?.id || ''}`} view={view} act={act} busy={busy} />}
      {view.generation_readiness?.required && !view.generation_readiness.ready && <GenerationReadiness view={view} />}
      <a className="button primary" href="#/roadmap">Yol xəritəsini aç</a></section>;
  }
  const active = view.roadmap.nodes.find(n => n.id === view.active_node_id);
  return <section aria-labelledby="home-title"><h1 id="home-title" tabIndex={-1}>{view.roadmap.topic}</h1>
    <p className="lead">{view.goal?.text}</p>
    {view.goal?.target && <p className="muted">Hədəf: {view.goal.target.official_name}{view.goal.target.code ? ` (${view.goal.target.code})` : ''} · yoxlanılıb {view.goal.target.verified_at?.slice(0, 10)}{view.goal.target.version ? ` · versiya ${view.goal.target.version}` : ' · imtahan versiyası rəsmi səhifədə göstərilmir'}</p>}
    {active && !view.due_reviews?.length && ['teaching', 'practice'].includes(view.phase) && <article className="card"><p className="eyebrow">Davam et</p><h2>{active.title}</h2>
      <a className="button primary" href={view.phase === 'practice' ? `#/practice/${active.id}` : `#/learn/${active.id}`}>İndi et</a></article>}
    {view.phase === 'feedback' && view.assessment_result && <article className="card"><p>Son nəticə hazırdır.</p>
      <a className="button primary" href={`#/result/${view.assessment_result.attempt_id}`}>Nəticəyə bax</a></article>}
    <MissionCard view={view} act={act} go={go} confirmExam={confirmExam} busy={busy} />
  </section>;
}

function GenerationReadiness({ view }) {
  const readiness = view.generation_readiness;
  if (!readiness) return <p className="muted" role="status">Agent məqsədi, hədəfi və mənbələri yoxlayır. Yol xəritəsi hazır kimi göstərilmir.</p>;
  const messages = {
    goal_required: 'Əvvəl konkret məqsədini qeyd et.',
    background_required: 'Məqsədə aid əvvəlki təcrübən qeyd olunmalıdır. “Bilmirəm” cavabı da uyğundur.',
    diagnostic_required: 'Qısa performance diagnostic və ya səbəbi qeyd olunmuş razılaşdırılmış skip lazımdır.',
    provisional_skip_confirmation_required: 'Əvvəlki diagnostic skip-i saxlanıb. Agentlə söhbətdə planın ilkin qalacağını və bacarıq sübutu olmadığını başa düşdüyünü təsdiqləməlisən.',
    target_required: 'Dəqiq hədəf və rəsmi tələblər yoxlanmalıdır. Sertifikat məqsədində imtahanın adı, kodu və versiyası dəqiqləşdirilir.',
    goal_clarification_required: 'Məqsədin mənası agentlə söhbətdə dəqiqləşdirilməlidir; plan üçün niyyətin təxmin edilməyəcək.',
    sources_required: 'Uyğun mənbələr hələ yoxlanmayıb. Import və məqsədə aid məzmun təsdiqlənməlidir.',
    sources_unreadable: 'Mənbələrdə istifadə edilə bilən oxunaqlı mətn təsdiqlənməyib; yalnız notebook-da görünmək kifayət deyil.',
    roadmap_source_refs_required: 'Yol xəritəsində hər mərhələnin hansı yoxlanılmış mənbəyə əsaslandığı göstərilməlidir.',
    roadmap_sources_invalid: 'Təklif olunan yol xəritəsinin mənbələri hazır və etibarlı deyil; agent onları yenidən yoxlamalıdır.',
    roadmap_generation_stale: 'Məqsəd və ya mənbə versiyası dəyişib; roadmap yenidən təklif və təsdiq edilməlidir.',
  };
  const needs = Array.isArray(readiness.blockers)
    ? [...new Set(readiness.blockers.map(code => messages[code] || 'Agent qalan hazırlıq şərtini dəqiqləşdirməlidir.'))]
    : [readiness.clarification_required && messages.goal_clarification_required,
      readiness.target_required && messages.target_required, !readiness.source_ready && messages.sources_required,
      !readiness.diagnostic_ready && messages.diagnostic_required].filter(Boolean);
  return <section className="card" aria-labelledby="generation-title">
    <h2 id="generation-title">Yol xəritəsindən əvvəl</h2>
    {needs.length > 0 ? <ul>{needs.map(text => <li key={text}>{text}</li>)}</ul>
      : <p>{readiness.ready ? 'Başlanğıc şərtlər yoxlanıb. Agent məqsədə uyğun yol xəritəsini hazırlayıb nəzərdən keçirmək üçün təqdim edəcək.' : 'Agent qalan hazırlıq şərtlərini yoxlayır; yol xəritəsi hələ təklif edilməyib.'}</p>}
    {view.intake?.skipped && <p className="warn">Diagnostic sənin razılığınla keçirilib. Bu, bacarıq sübutu deyil; plan ilkin qalır.</p>}
    <p className="muted">Bu ekran avtomatik dərs yaratmır. Yol xəritəsi ayrıca təklif ediləcək və dərslər yalnız sənin təsdiqindən sonra açılacaq.</p>
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
  const missing = res.filter(r => r.status !== 'validated');
  return <section className="card"><h2>Mənbələr</h2>
    <p className="muted">Kursun vəziyyəti bu kompüterdə saxlanır. NotebookLM xarici xidmətdir: agent mövcud CLI ilə seçilmiş notebook-u yoxlayır. Yeni material göndərmək və ya ödənişli material almaq üçün razılığın lazımdır.</p>
    {!res.length ? <p>Mənbə manifesti hələ yoxdur.</p> :
      <table><thead><tr><th scope="col">Mənbə</th><th scope="col">Vəziyyət</th><th scope="col">Qeyd</th></tr></thead>
        <tbody>{res.map(r => <tr key={r.key}><td>{/^https?:\/\//i.test(r.url || '') ? <a href={r.url} target="_blank" rel="noreferrer">{r.title || r.url}</a> : r.title || r.url || r.key}</td><td>{resourceStatus(r.status)}{r.stale ? ' · köhnəlmiş ola bilər' : ''}</td>
          <td>{(r.issues || []).join(', ') || '—'}</td></tr>)}</tbody></table>}
    {missing.length > 0 && <p className="warn" role="status">{missing.length} mənbə hələ hazır deyil. Agent çatışmayan materialı və səbəbi yoxlamalıdır; bu materialdan əsaslandırılmış dərs hazır sayılmır.</p>}
    {view.sources.unexpected?.length > 0 && <p className="muted">Notebook-da gözlənilməyən {view.sources.unexpected.length} mənbə var; dərslər onlara əsaslanmır.</p>}
  </section>;
}

function Progress({ view }) {
  const eng = view.engagement;
  const kinds = { teaching: 'İzah', practice: 'Praktika', help: 'Kömək', retry: 'Təkrar cəhd', review: 'Nəzərdən keçirmə', exam: 'İmtahan', roadmap: 'Yol xəritəsi', other: 'Digər' };
  return <section aria-labelledby="progress-title"><h1 id="progress-title" tabIndex={-1}>Proqres və sübut</h1>
    <div className="card"><h2>Mərhələlər</h2><ul>{view.roadmap.nodes.map(n => <li key={n.id}>{n.title} — {NODE_STATUS[n.status]}
      {n.skill_evidence && <p className="muted">{n.skill_evidence.label_az}</p>}</li>)}</ul>
      <p className="muted">Kalibrasiya olunmuş bacarıq ehtimalı hesablanmır; mərhələnin keçilməsi ümumi ustalıq zəmanəti deyil.</p></div>
    <div className="card"><h2>Təkrar yoxlamalar</h2>
      {view.review_queue?.length ? <ul>{view.review_queue.map(q => <li key={q.review_id}>
        {view.roadmap.nodes.find(n => n.id === q.node_id)?.title || q.node_id}: {({ scheduled: 'planlaşdırılıb', in_progress: 'davam edir',
          completed: 'tamamlanıb', superseded: 'yenisi ilə əvəzlənib', invalidated: 'məqsəd dəyişdiyi üçün etibarsız' })[q.status] || q.status}
        {q.status === 'scheduled' ? ` · ${new Date(q.due_at).toLocaleString('az-AZ')} tarixindən sonra` : ''}
        {q.outcome ? ` · nəticə: ${({ correct: 'keçdi', partial: 'qismən', incorrect: 'keçmədi' })[q.outcome] || q.outcome}` : ''}</li>)}</ul>
        : <p>Hələ planlaşdırılmış təkrar yoxlama yoxdur; ilk müstəqil uğurdan sonra yaranır.</p>}
      <p className="muted">Vaxt intervalları sadə qaydadır (ilk dəfə 1 gün, hər uğurda iki dəfə uzanır); elmi cəhətdən sübut olunmuş optimal interval deyil.</p></div>
    <div className="card"><h2>Müstəqil nəticələr</h2>
      {view.evidence_refs.length ? <ul>{view.evidence_refs.map(e => <li key={e.evidence_id}>
        {view.roadmap.nodes.find(n => n.id === e.node_id)?.title || e.node_id}: {({ correct: 'keçdi', partial: 'qismən', incorrect: 'keçmədi' })[e.outcome] || e.outcome} · <code>{e.evidence_id}</code></li>)}</ul>
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

function OfflineSnapshot({ onRetry }) {
  return <div className="app"><header><span className="brand">◈ Adaptive Learning</span></header>
    <main><h1>Lokal vəziyyət əlçatan deyil</h1>
      <p role="alert" className="warn">Lokal server cavab vermir. Kursun hazırkı mərhələsini yoxlamaq mümkün deyil; köhnə statik xəritə və proqres göstərilmir.</p>
      <p>Serveri uyğun session ilə başlat, sonra yenidən qoşul. Tanışlıq üçün: <code>alearn course serve --session … --intake</code></p>
      <button type="button" className="primary" onClick={onRetry}>Serverə yenidən qoşul</button></main></div>;
}
