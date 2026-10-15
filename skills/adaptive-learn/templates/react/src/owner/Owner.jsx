import React, { useCallback, useEffect, useRef, useState } from 'react';
import ConfirmDialog from '../components/ConfirmDialog.jsx';
import { LadderRung } from '../components/Ladder.jsx';
import Walkthrough from '../components/Walkthrough.jsx';
import { Text } from '../components/ui.jsx';
import * as api from './ownerApi.jsx';

// Owner surface of Course Studio. Served only by `alearn studio serve` (own process, own token). It shows progress and
// the handover dossier and sends the owner's decisions; no screen here accepts or edits authored content.
// Azerbaijani labels are plain UI wording; terms such as "təhvil" (handover), "qəbz" (receipt) and "yığma" (build)
// should be reviewed by a native speaker.
const JOB_STATUS = {
  planning: 'planlaşdırılır', authoring: 'yazılır', validating: 'yoxlanılır', checking: 'qayda yoxlaması gedir',
  remediation: 'düzəliş gözləyir', ready_for_review: 'təhvilə hazırdır', accepted: 'qəbul edilib',
  awaiting_learner_approval: 'öyrənənin təsdiqini gözləyir', promoted: 'kursa keçirilib', blocked: 'bloklanıb',
  needs_learner: 'öyrənən addımı lazımdır', paused: 'dayandırılıb', cancelled: 'ləğv edilib', superseded: 'köhnəlib',
};
const UNIT_STATUS = { pending: 'gözləyir', ordered: 'sifariş verilib', submitted: 'təqdim olunub', staged: 'hazırdır', invalid: 'düzəliş lazımdır' };
const RULE_STATUS_CLASS = { pass: 'ok', fail: 'fail', error: 'fail' };
const NO_START = new Set(['cancelled', 'superseded', 'promoted', 'accepted', 'awaiting_learner_approval']);
const NO_PAUSE = new Set(['paused', 'cancelled', 'superseded', 'promoted', 'accepted', 'awaiting_learner_approval']);

// start_detached: "started" (running), "already_running", or "exited_early" (the worker found nothing left to do and
// finished before the handshake: exit code 0 is a normal, complete run).
function startNote(out) {
  if (out.started) return `Fon işçisi başladıldı (pid ${out.pid}).`;
  if (out.result === 'already_running') return 'Fon işçisi artıq işləyir.';
  if (out.result === 'exited_early' && out.exit_code === 0) return 'Fon işçisi başladı və işini dərhal tamamladı: görüləcək başqa iş yoxdur.';
  return `Fon işçisi başlamadı: ${out.result}${out.exit_code != null ? ` (çıxış kodu ${out.exit_code})` : ''}.`;
}

const when = value => (value ? new Date(value).toLocaleString('az-AZ') : '—');
const jobLabel = status => JOB_STATUS[status] || status;
const parseRoute = () => (window.location.hash.startsWith('#/handover') ? 'handover' : 'studio');

export default function Owner() {
  const [authed, setAuthed] = useState(api.hasToken());
  const [route, setRoute] = useState(parseRoute());
  const mainRef = useRef(null);
  const [modal, setModal] = useState(false);          // a dialog is open: header and footer are inert (the page body is Studio's own)
  useEffect(() => {
    const onHash = () => setRoute(parseRoute());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  useEffect(() => { mainRef.current?.querySelector('h1')?.focus(); }, [route, authed]);
  const lost = useCallback(() => { api.setToken(null); setAuthed(false); }, []);
  if (!authed) return <TokenForm onDone={() => setAuthed(true)} />;
  return <div className="app owner">
    <a className="skip" href="#main" inert={modal ? true : undefined}>Əsas məzmuna keç</a>
    <header inert={modal ? true : undefined}>
      <span className="brand">◈ Course Studio · sahib paneli</span>
      <nav aria-label="Bölmələr">
        <a href="#/studio" aria-current={route === 'studio' ? 'page' : undefined}>Studio</a>
        <a href="#/handover" aria-current={route === 'handover' ? 'page' : undefined}>Təhvil</a>
      </nav>
      <button type="button" className="pause" onClick={lost}>Tokeni sil və çıx</button>
    </header>
    <main id="main" ref={mainRef}>{route === 'handover' ? <Handover onAuth={lost} /> : <Studio onAuth={lost} onModal={setModal} />}</main>
    <footer inert={modal ? true : undefined}>Bu panel yalnız sahib üçündür. Heç bir ekran müəllif məzmunu qəbul etmir; qərarlar ledger-də qeyd olunur.</footer>
  </div>;
}

function TokenForm({ onDone }) {
  const [value, setValue] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async e => {
    e.preventDefault();
    setBusy(true);
    api.setToken(value.trim());
    try {
      await api.getStatus();
      setValue('');
      onDone();
    } catch (err) {
      api.setToken(null);
      setError(err.status === 401 ? 'Token qəbul edilmədi.' : err.message);
    } finally { setBusy(false); }
  };
  return <main className="boot owner-login">
    <h1 tabIndex={-1}>Sahib girişi</h1>
    <p className="lead">Bu paneli açmaq üçün <code>ALEARN_OWNER_TOKEN</code> dəyərini daxil et. Token yalnız bu səhifənin yaddaşında (və bu tab bağlanana qədər sessiyada) qalır; ünvana və ya jurnala yazılmır.</p>
    <form onSubmit={submit} className="card">
      <label htmlFor="owner-token">Sahib tokeni</label>
      <input id="owner-token" type="password" autoComplete="off" spellCheck={false} value={value} onChange={e => setValue(e.target.value)} required />
      {error && <p role="alert" className="warn">{error}</p>}
      <button type="submit" className="primary" disabled={busy || value.trim().length < 8}>Daxil ol</button>
    </form>
  </main>;
}

// ----------------------------------------------------------------------- Studio
function Studio({ onAuth, onModal }) {
  const [st, setSt] = useState(null);
  const [events, setEvents] = useState(null);
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const opener = useRef(null);

  const load = useCallback(async () => {
    try {
      const [s, e] = await Promise.all([api.getStatus(), api.getEvents(12).catch(() => null)]);
      setSt(s); setEvents(e); setError(null);
    } catch (err) {
      if (err.status === 401) onAuth(); else setError(err.message);
    }
  }, [onAuth]);
  useEffect(() => {
    load();
    const timer = setInterval(() => { if (document.visibilityState === 'visible') load(); }, 4000);
    return () => clearInterval(timer);
  }, [load]);
  useEffect(() => { onModal(confirmCancel); return () => onModal(false); }, [confirmCancel, onModal]);
  useEffect(() => { if (!confirmCancel && opener.current) { opener.current.focus?.(); opener.current = null; } }, [confirmCancel]);

  const run = async (action, text) => {
    setBusy(true);
    try {
      const out = await api.control(action, {});
      setNote(action === 'start' ? startNote(out) : text);
      setError(null);
      await load();
    } catch (err) {
      if (err.status === 401) onAuth(); else setError(err.message);
    } finally { setBusy(false); }
  };

  if (!st) return <section><h1 tabIndex={-1}>Studio</h1>{error ? <p role="alert" className="warn">{error}</p> : <p role="status">Yüklənir…</p>}</section>;
  const none = st.status === 'no_job';
  const total = st.progress?.units_total || 0;
  const staged = st.progress?.units_staged || 0;
  const status = st.status;
  const worker = st.worker || {};
  return <section aria-labelledby="studio-title">
    <h1 id="studio-title" tabIndex={-1}>Studio</h1>
    <div inert={confirmCancel ? true : undefined}>
    {error && <p role="alert" className="warn">{error}</p>}
    {note && <p role="status" className="note">{note}</p>}
    {none ? <p className="card">Bu sessiya üçün hələ yığma işi yoxdur. Terminalda: <code>{st.next_step}</code></p> : <>
      <div className="card job">
        <p className="eyebrow">Yığma işi</p>
        <h2>İş <code>{st.job_id}</code> · <span className="chip" data-status={status}>{jobLabel(status)}</span></h2>
        <p><progress max={Math.max(total, 1)} value={staged} aria-label="Hazır vahidlər" /> <span id="unit-progress">{staged} / {total} vahid hazırdır</span>
          {st.progress?.units_invalid > 0 && <span className="fail"> · {st.progress.units_invalid} vahid düzəliş gözləyir</span>}</p>
        {st.blocked && <p role="alert" className="warn"><strong>Bloklanıb:</strong> {st.blocked.next_step || st.blocked.kind || 'insan müdaxiləsi lazımdır'}</p>}
        {st.needs_learner && <p role="status" className="warn"><strong>Öyrənən addımı lazımdır:</strong> {st.needs_learner.next_step || st.needs_learner.reason || st.needs_learner.message || 'öyrənən əvvəlcə addım atmalıdır'}</p>}
        {st.drift?.length > 0 && <p role="status" className="warn">Kurs bu işdən sonra dəyişib: {st.drift.join(', ')}. Terminalda <code>alearn studio rebase</code> lazımdır.</p>}
        <p className="muted">Yol xəritəsi versiyası <code>{st.binding?.roadmap_version}</code> · yenilənib {when(st.updated_at)}{st.build_digest ? <> · yığma izi <code>{st.build_digest.slice(0, 12)}</code></> : null}</p>
        <div className="controls" role="group" aria-label="İşə nəzarət">
          <button type="button" className="primary" disabled={busy || NO_START.has(status)} onClick={() => run('start')}>Fon işçisini başlat</button>
          <button type="button" disabled={busy || NO_PAUSE.has(status)} onClick={() => run('pause', 'İş dayandırıldı.')}>Dayandır</button>
          <button type="button" disabled={busy || status !== 'paused'} onClick={() => run('resume', 'İş davam etdirildi.')}>Davam et</button>
          <button type="button" disabled={busy || ['cancelled', 'superseded', 'promoted'].includes(status)}
                  onClick={e => { opener.current = e.currentTarget; setConfirmCancel(true); }}>Ləğv et</button>
        </div>
      </div>
      <div className="card" aria-labelledby="worker-title" role="region">
        <h2 id="worker-title">Fon işçisi</h2>
        <p data-worker={worker.alive ? 'alive' : 'down'}>{worker.alive
          ? <>İşləyir (pid {worker.pid}, vəziyyət: {worker.state || '—'}), son siqnal {Math.round(worker.beat_age_seconds ?? 0)} san əvvəl.</>
          : <>İşləmir{worker.beat_at ? <> (son siqnal {when(worker.beat_at)}, vəziyyət: {worker.state || '—'})</> : null}. Yığmanı özün aparırsansa (host agent), işçi lazım deyil.</>}</p>
      </div>
      <div className="card">
        <h2>Mərhələlər üzrə vahidlər</h2>
        <table><caption className="sr-only">Hər mərhələ üçün dərs və tapşırıq vahidlərinin vəziyyəti</caption>
          <thead><tr><th scope="col">Mərhələ</th><th scope="col">Dərs</th><th scope="col">Tapşırıqlar</th></tr></thead>
          <tbody>{(st.nodes || []).map(n => <tr key={n.node}><th scope="row">{n.title || n.node}<br /><code>{n.node}</code></th>
            <td><UnitCell unit={n.lesson} /></td><td><UnitCell unit={n.items} /></td></tr>)}</tbody></table>
      </div>
      <div className="card">
        <h2>Runner-lər</h2>
        <ul>{Object.entries(st.runners || {}).map(([name, r]) => <li key={name}><strong>{name}</strong>: {r.configured
          ? (r.usable ? 'qoşulub və istifadəyə hazırdır' : 'qoşulub, amma istifadə olunmur')
          : 'qoşulmayıb'}{r.binary_drift ? ' (binar dəyişib: yenidən konfiqurasiya lazımdır)' : ''}</li>)}</ul>
        <p className="muted">Runner qoşmaq yalnız terminaldan edilir (<code>alearn studio runners configure</code>); burada sirr və ya token göstərilmir.</p>
      </div>
      <div className="card">
        <h2>Bildirişlər</h2>
        {st.notifications?.notifications?.length
          ? <ul className="log">{[...st.notifications.notifications].reverse().map(n => <li key={n.n}><span className="muted">{when(n.at)}</span> · <strong>{n.kind}</strong>: {n.message}</li>)}</ul>
          : <p className="muted">Hələ bildiriş yoxdur.</p>}
      </div>
      <div className="card">
        <h2>Son hadisələr</h2>
        {events ? <>
          <p className="muted">Hadisə zənciri: {events.verify?.ok ? 'bütövdür' : <strong className="fail">POZULUB: {events.verify?.error}</strong>} · cəmi {events.total}</p>
          <ol className="log">{[...events.events].reverse().map(ev => <li key={ev.seq}><span className="muted">{when(ev.at)}</span> · <code>{ev.type}</code></li>)}</ol>
        </> : <p className="muted">Hadisə yoxdur.</p>}
      </div></>}
    </div>
    {confirmCancel && <ConfirmDialog title="İşi ləğv edək?" safeFirst confirmLabel="Bəli, işi ləğv et" cancelLabel="Xeyr, saxla"
      text="İş 'ləğv edilib' vəziyyətinə keçir və açıq sifarişlər bağlanır. Heç nə silinmir: hazırlanmış vahidlər və hadisələr qalır. Davam etmək üçün yeni iş yaradılır."
      onCancel={() => setConfirmCancel(false)} onConfirm={() => { setConfirmCancel(false); run('cancel', 'İş ləğv edildi.'); }} />}
  </section>;
}

function UnitCell({ unit }) {
  if (!unit) return <span className="muted">—</span>;
  return <><span className="chip" data-unit={unit.status}>{UNIT_STATUS[unit.status] || unit.status}</span>
    {unit.attempts > 0 && <span className="muted"> · cəhd: {unit.attempts}</span>}
    {unit.last_errors?.length > 0 && <ul className="muted small">{unit.last_errors.map((m, i) => <li key={i}>{m}</li>)}</ul>}</>;
}

// --------------------------------------------------------------------- Handover
function Handover({ onAuth }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [preview, setPreview] = useState(null);
  const [statement, setStatement] = useState('');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setData(await api.getHandover()); setError(null); }
    catch (err) {
      if (err.status === 401) { onAuth(); return; }
      setData(null);
      setError(err.code === 'studio_no_build' ? 'Hələ təhvil üçün tam yığma yoxdur: bütün vahidlər hazır olandan sonra dossier yaranır.' : err.message);
    }
  }, [onAuth]);
  useEffect(() => { load(); }, [load]);

  const guard = async fn => {
    setBusy(true);
    try { return await fn(); }
    catch (err) { if (err.status === 401) onAuth(); else setError(err.message); return null; }
    finally { setBusy(false); }
  };
  const openPreview = node => guard(async () => { setPreview({ node, ...(await api.getNode(node)) }); });
  const accept = e => {
    e.preventDefault();
    return guard(async () => {
      const out = await api.control('accept', { digest: data.handover.build_digest, statement });
      setResult(out); setStatement(''); setError(null);
      await load();
    });
  };
  const reject = (node, note) => guard(async () => {
    const out = await api.control('reject', { node, note });
    setResult({ rejected: node, reopened: out.reopened }); setPreview(null); setError(null);
    await load();
  });

  const doc = data?.handover;
  return <section aria-labelledby="handover-title">
    <h1 id="handover-title" tabIndex={-1}>Təhvil dossieri</h1>
    <p><button type="button" disabled={busy} onClick={load}>Yenilə</button></p>
    {error && <p role="alert" className="warn">{error}</p>}
    {result?.accepted && <p role="status" className="note" id="accept-result">Yığma qəbul edildi. {result.promotion?.next_step
      ? <>Kursa keçirmə ayrıca addımdır: <code>{result.promotion.next_step}</code></>
      : result.promotion?.state === 'awaiting_learner_approval' ? 'Öyrənən yol xəritəsinin bu versiyasını təsdiq edəndən sonra keçirilir.' : null}</p>}
    {result?.rejected && <p role="status" className="note">Mərhələ <code>{result.rejected}</code> rədd edildi və yenidən açıldı ({(result.reopened || []).join(', ')}).</p>}
    {!doc ? (!error && <p role="status">Yüklənir…</p>) : <>
      {doc.blocked && <div role="alert" className="blocked-banner"><strong>BLOKLANIB:</strong> bu yığma qəbul edilə bilməz.
        {doc.blocking_findings?.length > 0 && <ul>{doc.blocking_findings.map((f, i) => <li key={i}>{f.rule} / <code>{f.id}</code>: {f.detail}</li>)}</ul>}</div>}
      {!data.receipt_fresh && <p role="status" className="warn">Qəbz köhnəlib ({data.stale_reason || 'səbəb göstərilməyib'}). Terminalda <code>alearn studio handover</code> işlət; qəbul yalnız təzə qəbzlə mümkündür.</p>}
      <div className="card">
        <p className="eyebrow">Yığma izi (build digest)</p>
        <p><code className="digest" id="build-digest">{doc.build_digest}</code></p>
        <p className="muted">İş <code>{doc.job_id}</code> · yol xəritəsi <code>{doc.roadmap_version}</code> · qəbz <code>{doc.receipt?.receipt_id}</code> ({doc.receipt?.scope}, {doc.receipt?.status}) · {when(doc.created_at)}</p>
        <p>{doc.receipt_statement_az}</p>
      </div>
      <div className="card">
        <h2>R1–R10 qəbz cədvəli</h2>
        <table id="rules-table"><caption className="sr-only">On qayda, hər birinin nəticəsi və yoxlama sayları</caption>
          <thead><tr><th scope="col">Qayda</th><th scope="col">Nəticə</th><th scope="col">Yoxlamalar</th><th scope="col">Təsvir</th></tr></thead>
          <tbody>{doc.rules.map(r => <tr key={r.rule}>
            <th scope="row">{r.rule}{r.hard ? <span className="muted small"> (güzəştsiz)</span> : null}</th>
            <td><span className={`chip ${RULE_STATUS_CLASS[r.status] || ''}`}>{r.status_az}</span></td>
            <td>{r.probe_summary_az || `${r.probes_passed} keçdi`}</td><td>{r.label_az}</td></tr>)}</tbody></table>
        <p className="muted small" id="rules-legend">Tətbiq olunmur = yoxlama bu yığmada işləmədi; keçid sayılmır və yoxlanılmış hesab olunmur. Yoxlama mətnləri texniki olduğu üçün olduğu kimi göstərilir.</p>
      </div>
      <div className="card">
        <h2>Nə hazırlanıb</h2>
        <p className="muted">Cəmi {doc.totals.nodes} mərhələ, {doc.totals.lessons} dərs, {doc.totals.walkthrough_steps} nümayiş addımı, {doc.totals.ladders} pilləli təlim, {doc.totals.items} qiymətləndirmə tapşırığı. Saylar yığma fayllarından götürülüb.</p>
        <ul className="nodes">{Object.entries(doc.nodes).map(([nid, row]) => <NodeRow key={nid} nid={nid} title={doc.titles?.[nid]} row={row} busy={busy}
          onPreview={() => openPreview(nid)} onReject={note => reject(nid, note)} />)}</ul>
      </div>
      {preview && <NodePreview data={preview} onClose={() => setPreview(null)} />}
      <div className="card">
        <h2>Nələr yoxdur</h2><ul>{doc.absent.map(x => <li key={x.id}>{x.text_az}</li>)}</ul>
        <h2>Nələr yoxlanılmayıb</h2><ul id="not-verified">{doc.not_verified.map(x => <li key={x.id}>{x.text_az}</li>)}</ul>
      </div>
      <form className="card accept" onSubmit={accept} aria-labelledby="accept-title">
        <h2 id="accept-title">Yığmanı qəbul et</h2>
        <p>Qəbul bu yığma izinə bağlıdır: <code>{doc.build_digest.slice(0, 16)}…</code> (tam iz yuxarıda göstərilib). İz dəyişərsə, qəbul rədd edilir.</p>
        {data.status !== 'ready_for_review' && <p className="warn" role="status">İşin vəziyyəti: {jobLabel(data.status)}. Qəbul yalnız “təhvilə hazırdır” vəziyyətində mümkündür.</p>}
        <label htmlFor="statement">Qəbul bəyanatı (özün yaz)</label>
        <textarea id="statement" rows={3} maxLength={500} value={statement} onChange={e => setStatement(e.target.value)}
                  placeholder="Məsələn: Dossieri yoxladım və qəbul edirəm." />
        <p className="muted">Bəyanat ledger-də saxlanır. O, real insanın verdiyini kriptoqrafik sübut etmir (yuxarıdakı “Nələr yoxlanılmayıb” siyahısına bax).</p>
        <button type="submit" className="primary" disabled={busy || doc.blocked || !data.receipt_fresh || data.status !== 'ready_for_review' || !statement.trim()}>Qəbul edirəm</button>
      </form>
      <details className="card"><summary>HANDOVER.md (olduğu kimi)</summary><pre className="code" tabIndex={0}>{data.markdown}</pre></details>
    </>}
  </section>;
}

function NodeRow({ nid, title, row, busy, onPreview, onReject }) {
  const [note, setNote] = useState('');
  const lesson = row.lesson;
  const items = row.items;
  return <li className="node-row">
    <h3>{title || nid} <code>{nid}</code></h3>
    <p className="muted">{lesson ? <>Dərs: {lesson.hints} ipucu{lesson.hint_ceiling != null ? ` (limit ${lesson.hint_ceiling})` : ''}
      {lesson.walkthrough ? `, nümayiş ${lesson.walkthrough.steps} addım` : ', nümayiş yoxdur'}
      {lesson.ladder ? `, pilləli təlim (${lesson.ladder.blanks} boşluq)` : ''}.</> : 'Dərs yoxdur.'}
      {items ? <> Tapşırıqlar: {items.count} ({items.checks} yoxlama).</> : ' Tapşırıq yoxdur.'}</p>
    <div className="controls">
      <button type="button" disabled={busy} onClick={onPreview} aria-label={`${title || nid}: öyrənən görünüşünü önizlə`}>Önizlə</button>
    </div>
    <details><summary>Bu mərhələni rədd et</summary>
      <label htmlFor={`note-${nid}`}>Rədd qeydi (müəllifə düzəliş sifarişi olur)</label>
      <textarea id={`note-${nid}`} rows={2} maxLength={1000} value={note} onChange={e => setNote(e.target.value)} />
      <button type="button" disabled={busy || !note.trim()} onClick={() => { onReject(note); setNote(''); }}>Rədd et və yenidən aç</button>
    </details>
  </li>;
}

// What a learner would see for one staged node. The server reduces it to the public view; only COUNTS of hidden checks
// reach this screen (never their source, a reference solution or an answer key).
function NodePreview({ data, onClose }) {
  const lesson = data.lesson;
  const heading = useRef(null);
  useEffect(() => { heading.current?.focus(); }, [data.node]);
  return <section className="card preview" aria-labelledby="preview-title">
    <h2 id="preview-title" tabIndex={-1} ref={heading}>Önizləmə: {data.title || data.node}</h2>
    <p className="muted">{data.note_az}</p>
    <p className="muted" id="hidden-counts">Gizli yoxlama sayı: {data.hidden.total} (tapşırıq praktikası: {data.hidden.practice_checks}). Mənbələri göstərilmir.</p>
    {lesson ? <>
      <h3>Dərs</h3>
      <p><strong>Məqsəd:</strong> {lesson.objective}</p>
      <ul>{lesson.success_criteria.map((c, i) => <li key={i}>{c}</li>)}</ul>
      <Text value={lesson.explanation.body} />
      <h3>Praktika</h3>
      <Text value={lesson.practice.prompt_md} />
      {lesson.practice.starter_code && <pre className="code" tabIndex={0} aria-label="Başlanğıc kod"><code>{lesson.practice.starter_code}</code></pre>}
      {lesson.practice.visible_checks?.length > 0 && <ul>{lesson.practice.visible_checks.map(c => <li key={c.id}>{c.name}</li>)}</ul>}
      <p className="muted">İpucu sayı: {lesson.practice.hint_count}{lesson.hint_ceiling != null ? `, limit: ${lesson.hint_ceiling}` : ''}.</p>
      {lesson.walkthrough && <Walkthrough content={lesson.walkthrough} />}
      {lesson.ladder && <><h3>Pilləli təlim</h3><LadderRung rung={lesson.ladder.worked} /><LadderRung rung={lesson.ladder.completion} /></>}
    </> : <p className="muted">Dərs hələ hazır deyil.</p>}
    <h3>Qiymətləndirmə tapşırıqları ({data.items.length})</h3>
    <ul>{data.items.map(item => <li key={item.item_id}><strong>{item.title}</strong> <span className="muted">({item.kind}, {item.scope}, {item.check_count ?? 0} gizli yoxlama)</span>
      <Text value={item.prompt_md} />
      {item.starter_code && <pre className="code" tabIndex={0}><code>{item.starter_code}</code></pre>}
      {item.questions && <ol>{item.questions.map(q => <li key={q.id}>{q.prompt}</li>)}</ol>}</li>)}</ul>
    <button type="button" onClick={onClose}>Önizləməni bağla</button>
  </section>;
}
