import React, { useEffect, useRef, useState } from 'react';
import { ActionBar, QuestionForm, RoleNote, Steps, Text } from './ui.jsx';
import { CheckList } from './Practice.jsx';

const LOCKED = 'İmtahan zamanı izah və ipucu bağlıdır';
const SCOPE = { independent_reproduction: 'müstəqil təkrar', near_transfer: 'yaxın transfer', far_transfer: 'uzaq transfer' };
const CATS = { core: 'əsas', edge: 'kənar hal', transfer: 'görmədiyin hal (transfer)' };

function NonCodeAnswer({ att, answers, setAnswers, disabled }) {
  if (att.kind === 'free_text') {
    const lim = att.item.response_limits || { min_chars: 20, max_chars: 6000 };
    const text = answers.response || '';
    return <div className="free-text">
      <label htmlFor="exam-response">Cavabın</label>
      <textarea id="exam-response" rows={8} value={text} disabled={disabled} maxLength={lim.max_chars}
                aria-describedby="exam-response-help" onChange={e => setAnswers({ response: e.target.value })} />
      <p id="exam-response-help" className="muted small">Ən azı {lim.min_chars} simvol ({text.trim().length} yazılıb). {att.item.grading_note_az}</p>
    </div>;
  }
  return <>
    {att.item.scenario_md && <div className="scenario"><h3>Ssenari</h3><Text value={att.item.scenario_md} /></div>}
    <QuestionForm questions={att.item.questions} value={answers} onChange={setAnswers} idPrefix="exam" disabled={disabled} />
  </>;
}

export function Exam({ view, act, go, busy }) {
  const att = view.active_attempt;
  const [confirmAbandon, setConfirmAbandon] = useState(false);
  const [answers, setAnswers] = useState(att?.saved_answers || {});
  const [dirty, setDirty] = useState(false);
  const [savedAt, setSavedAt] = useState(null);
  const keepRef = useRef(null);
  const stopRef = useRef(null);
  const wasAsking = useRef(false);
  useEffect(() => {   // the irreversible choice gets focus on the safe option; focus returns on cancel
    if (confirmAbandon) { keepRef.current?.focus(); wasAsking.current = true; }
    else if (wasAsking.current) { stopRef.current?.focus(); wasAsking.current = false; }
  }, [confirmAbandon]);
  if (!att) return <p role="status">Aktiv imtahan yoxdur.</p>;
  const code = att.kind === 'python' || !att.kind;
  const pending = att.status === 'awaiting_rubric' || view.attempt_status === 'awaiting_rubric';
  const change = v => { setAnswers(v); setDirty(true); };
  const save = async () => {
    const out = await act('/api/course/exam/answers', { attempt_id: att.attempt_id, answers });
    if (out) { setDirty(false); setSavedAt(new Date()); }
    return out;
  };
  const submit = async () => {
    if (!code && att.status === 'active' && (dirty || !Object.keys(att.saved_answers || {}).length)) {
      if (!(await save())) return;
    }
    const out = await act('/api/course/exam/submit', { attempt_id: att.attempt_id });
    if (out?.status === 'finalized') go(`#/result/${att.attempt_id}`);
  };
  const abandon = async () => {
    const out = await act('/api/course/exam/abandon', { attempt_id: att.attempt_id, reason: 'learner_exit' });
    if (out) go('#/');
  };
  const failed = att.status === 'scoring_failed' || view.attempt_status === 'scoring_failed';
  const lim = att.item.response_limits;
  const tooShort = att.kind === 'free_text' && att.status === 'active'
    && (answers.response || '').trim().length < (lim?.min_chars || 0);
  const blockedHint = pending ? 'Rubrika qiymətləndirməsi gözlənilir' : tooShort ? `Ən azı ${lim.min_chars} simvol yaz` : null;
  const label = att.review_id ? 'Təkrar yoxlama' : att.fast_track ? 'Birbaşa yoxlama' : 'Yeni tapşırıq';
  return <section className="exam" aria-labelledby="exam-title">
    <div className="lock-banner" role="status"><strong>Müstəqil yoxlama.</strong> Bu cəhd bitənə qədər dərs, izah və ipucu bağlıdır — səhifəni yeniləsən, geri qayıtsan və ya başqa pəncərə açsan da eyni cəhdə qayıdacaqsan.</div>
    <p className="eyebrow">{label} · {SCOPE[att.item.scope] || att.item.scope}</p>
    <h1 id="exam-title" tabIndex={-1}>{att.item.title}</h1>
    <Steps current="exam" />
    <div className="card"><Text value={att.item.prompt_md} />
      {code ? <>
        <h3>Faylın</h3><p className="file"><code>{att.file}</code></p>
        <p className="muted">Göndərəndən sonra {att.item.check_count} gizli yoxlama işləyəcək ({(att.item.check_categories || []).map(c => CATS[c] || c).join(', ')}). Gizli halların girişləri göstərilmir ki, cavab onlara uyğunlaşdırılmasın.</p>
      </> : !pending && <>
        <NonCodeAnswer att={att} answers={answers} setAnswers={change} disabled={att.status !== 'active'} />
        <div className="entry-actions">
          <button type="button" onClick={save} disabled={busy || att.status !== 'active'}>Cavabları yadda saxla</button>
          <span className="muted small" role="status">{dirty ? 'Yadda saxlanmamış dəyişiklik var.' : savedAt ? `Yadda saxlanıb (${savedAt.toLocaleTimeString('az-AZ')}).` : ''}</span>
        </div>
        {att.kind !== 'free_text' && <p className="muted">Cavablar göndəriləndən sonra açarla yoxlanacaq; o vaxta qədər düzgün/yanlış göstərilmir.</p>}
      </>}
    </div>
    {pending && <div className="card warn" role="status"><h2>Cavabın rubrika ilə qiymətləndirilməsini gözləyir</h2>
      <p>Açıq cavab açar sözlərlə yox, rubrikanın hər meyarı üzrə qiymətləndirilir və hər qərar cavabındakı konkret cümləyə istinad etməlidir. Qiymətləndirmə bitənə qədər imtahan kilidi qalır.</p>
      <p className="muted">Bir kalibrasiya edilməmiş AI qiymətləndiricisinin rəyi tək başına mərhələni keçmiş saymır. Eyni AI-nin ikinci rəyi də müstəqil təsdiq deyil.</p>
      {att.saved_answers?.response && <details><summary>Göndərdiyim cavab</summary><Text value={att.saved_answers.response} /></details>}
      <details><summary>Host-agent üçün növbəti addım</summary><p><code>alearn course exam-rubric</code> ilə konkret meyar rəylərini qeyd et. Qiymətləndirmə uğursuz olsa, saxlanmış cəhd təkrar yoxlana və ya learner tərəfindən dayandırıla bilər.</p></details></div>}
    {failed && <div className="card warn" role="alert"><p>Qiymətləndirmə tamamlanmadı ({att.scoring_error || 'səbəb qeyd olunub'}). Cavabın saxlanıb və uğur və ya uğursuzluq kimi sayılmayıb. Yenidən cəhd et.</p>
      <button type="button" className="primary" onClick={submit} disabled={busy}>Qiymətləndirməni yenidən başlat</button></div>}
    <ActionBar busy={busy} actions={{
      now: { onClick: submit, hint: blockedHint || (code ? 'Göndər və gizli yoxlamaları işlət' : 'Cavabları göndər'),
             disabled: pending || tooShort || view.attempt_status === 'finalized' },
      why: { onClick: () => {}, disabled: true, hint: LOCKED },
      help: { onClick: () => {}, disabled: true, hint: LOCKED },
      check: { onClick: submit, hint: blockedHint || 'Göndər və yoxla', disabled: pending || tooShort },
    }} />
    {!confirmAbandon
      ? <button type="button" className="link" ref={stopRef} disabled={busy} onClick={() => setConfirmAbandon(true)}>Cəhdi dayandır</button>
      : <div className="card warn" role="alertdialog" aria-labelledby="abandon-q" aria-describedby="abandon-q"
             onKeyDown={e => { if (e.key === 'Escape') { e.preventDefault(); setConfirmAbandon(false); } }}>
          <p id="abandon-q">{pending ? 'Göndərilmiş cavabın qiymətləndirilməyəcək və cəhd uğur kimi sayılmayacaq; bu tapşırıq bir daha verilməyəcək. Davam edək?'
                                     : 'Cəhdi dayandırsan, o uğur kimi sayılmayacaq və bu tapşırıq bir daha verilməyəcək. Davam edək?'}</p>
          <button type="button" onClick={abandon} disabled={busy}>Bəli, dayandır</button> <button type="button" className="primary" ref={keepRef} disabled={busy} onClick={() => setConfirmAbandon(false)}>Xeyr, imtahana qayıt</button></div>}
    <RoleNote role="Examiner (imtahan edən)" text="yalnız görmədiyin yeni tapşırıq verir; cəhd zamanı izah etmir." />
    <p className="muted small">{view.honest_boundaries_az[0]}</p>
  </section>;
}

const OUTCOME = { correct: ['Keçdi', 'ok'], partial: ['Qismən keçdi', 'partial'], incorrect: ['Keçmədi', 'fail'] };
const UNIT = { python: 'yoxlama', concept: 'sual', scenario: 'sual', free_text: 'meyar' };
const LIST_TITLE = { python: 'Gizli yoxlamalar', concept: 'Suallar', scenario: 'Suallar', free_text: 'Rubrika meyarları' };

export function Result({ view, act, go, busy }) {
  const r = view.assessment_result;
  if (!r) return <p role="status">Nəticə hələ yekunlaşmayıb.</p>;
  const [label, cls] = OUTCOME[r.outcome] || [r.outcome, ''];
  const kind = r.kind || 'python';
  const next = async () => { if (await act('/api/course/next', {})) go('#/'); };
  return <section className="result-view" aria-labelledby="result-title">
    <p className="eyebrow">{r.review_id ? 'Təkrar yoxlamanın nəticəsi' : 'Yekunlaşmış nəticə'}</p>
    <h1 id="result-title" tabIndex={-1} className={cls}>{label}: {r.passed}/{r.total} {UNIT[kind] || 'yoxlama'}</h1>
    <Steps current="result" />
    <div className="card"><CheckList checks={r.checks} title={LIST_TITLE[kind] || 'Yoxlamalar'} />
      {r.misconception_feedback?.length > 0 && <><h3>Cavabında görünən anlaşılmazlıq</h3>
        <ul>{r.misconception_feedback.map(m => <li key={m.id}><strong>{m.name}:</strong> {m.feedback}</li>)}</ul></>}
      {r.rubric && <p className="muted">Qiymətləndirən: {r.rubric.grader === 'human' ? 'insan' : 'agent'} · əminlik: {({ low: 'aşağı', medium: 'orta', high: 'yüksək' })[r.rubric.confidence] || r.rubric.confidence}. Bu, runtime yoxlaması deyil, rəydir.</p>}
      {r.grader_reliability === 'uncalibrated_ai' && <p className="warn">AI grader-in düzgünlüyü real cavablar üzərində kalibrasiya edilməyib. Təkrar AI rəyi müstəqil təsdiq deyil.</p>}
      {r.grader_reliability === 'attributed_human' && <p className="muted">İnsan rəyi kimi qeydə alınıb; qiymətləndirici kimliyi və ölçülmüş grader agreement bu lokal tətbiqdə təsdiqlənmir.</p>}
      {(kind === 'concept' || kind === 'scenario') && <p className="muted">Bu cavablar açarla yoxlanılan seçim, sıralama və ya ədəd suallarıdır: tanıma səviyyəli sübut sayılır və tək başına ustalıq göstərmir.</p>}
      {r.provisional && <p className="warn" role="status">Bu nəticə müvəqqətidir: yeni tapşırıqda insan rəyi və ya müstəqil icra yoxlaması ilə təsdiq lazımdır.</p>}
      <p className="muted">{r.independence_note_az} Sübut: <code>{r.evidence_id}</code></p>
    </div>
    <MissionCard view={view} act={act} go={go} busy={busy} />
    {view.phase === 'feedback' && <ActionBar busy={busy} actions={{ next: { onClick: next, hint: 'Növbəti missiyaya keç' } }} />}
    <RoleNote role="Diagnostician (diaqnost)" text="səhvin arxasındakı ehtimal olunan anlaşılmazlığı göstərir; bir cəhd səbəbi tam sübut etmir." />
  </section>;
}

const BLOCKED = {
  lesson_content_missing: 'Bu mərhələnin dərsi hələ hazırlanmayıb. Agentdən mənbələrə əsaslanan dərsi hazırlamasını istə — hazır olmayan dərs “hazır” kimi göstərilmir.',
  no_fresh_item: 'Görmədiyin yeni yoxlama tapşırığı qalmayıb. Agent yeni tapşırıq hazırlamalıdır.',
  grounding_invalid: 'Bu dərsin mənbəsi dəyişib və ya etibarsızdır. Mənbə yenidən yoxlanana qədər dərs və yoxlama bağlıdır.',
  capability_changed: 'Bu mərhələnin məqsədi dəyişib. Köhnə nəticən tarixçədə qalır; yeni məqsəd üçün dərs və yeni tapşırıq hazırlanmalıdır.',
  recovery_required: 'Eyni dövrə dayandırılıb. Agentlə şərti, ilkin bacarığı, alət və iş yükünü yoxlayıb konkret yeni plan razılaşdır.',
};

export function MissionCard({ view, act, go, confirmExam, busy }) {
  const m = view.next_mission;
  if (!m) return null;
  const nodes = view.roadmap.nodes;
  const node = nodes.find(n => n.id === m.node_id);
  const start = async () => {
    if (await act('/api/course/mission/start', { node: m.node_id })) go(`#/learn/${m.node_id}`);
  };
  const review = async () => {
    const out = await act('/api/course/review/start', { node: m.node_id });
    if (out?.attempt) go(`#/exam/${out.attempt.attempt_id}`);
  };
  const blocked = BLOCKED[m.blocked_on] || null;
  const open = view.phase !== 'feedback' && node && node.status !== 'locked';
  return <article className="card mission" aria-labelledby="mission-title">
    <p className="eyebrow">{m.type === 'due_review' ? 'Təkrar yoxlamanın vaxtıdır' : 'Növbəti missiya'}</p>
    <h2 id="mission-title">{m.title || node?.title}</h2>
    <p>{m.reason_az}</p>
    {m.uncertainty_az && <p className="muted">{m.uncertainty_az}</p>}
    {m.review?.due_at && m.type !== 'due_review' && <p className="muted">Növbəti təkrar yoxlama: {new Date(m.review.due_at).toLocaleString('az-AZ')}</p>}
    {m.deferred?.title && <p className="muted small">Sonra davam: “{m.deferred.title}”.</p>}
    {blocked && <p className="warn" role="status">{blocked}</p>}
    {node && m.type === 'recovery_check' && <RecoveryForm key={m.recovery?.triggering_attempt_id || m.node_id} nodeId={m.node_id} act={act} busy={busy} />}
    {open && !blocked && <div className="entry-actions">
      {m.type === 'due_review'
        ? <button type="button" className="primary" onClick={review} disabled={busy}>Təkrar yoxlamaya başla</button>
        : m.type === 'confirm_with_fresh_item'
          ? confirmExam && <button type="button" className="primary" disabled={busy} onClick={() => confirmExam(m.node_id, true)}>Yeni tapşırıqla təsdiqlə</button>
          : <>
            {m.lesson_ready && <button type="button" className="primary" onClick={start} disabled={busy}>İndi et</button>}
            {confirmExam && node?.fresh_items > 0 && <button type="button" disabled={busy} onClick={() => confirmExam(m.node_id, true)}>Bunu bilirəm — birbaşa yoxla</button>}
          </>}
    </div>}
  </article>;
}

function RecoveryForm({ nodeId, act, busy }) {
  const [blocker, setBlocker] = useState('task_interpretation');
  const [plan, setPlan] = useState('');
  const [agreed, setAgreed] = useState(false);
  const submit = async e => {
    e.preventDefault();
    await act('/api/course/recovery', { node: nodeId, plan: { blocker, plan, learner_agreed: agreed } });
  };
  return <form onSubmit={submit} aria-label="Dəyişmiş recovery planı">
    <h3>Dəyişmiş planı razılaşdır</h3>
    <p>Agentlə səbəbi yoxla və bir kiçik yeni addımı müəyyən et. Sadəcə eyni izahı təkrarlamaq kifayət deyil.</p>
    <label>İlk araşdırılacaq ehtimal <select value={blocker} onChange={e => setBlocker(e.target.value)}>
      <option value="task_interpretation">Tapşırığın şərti</option><option value="prerequisite">İlkin bacarıq</option>
      <option value="workload">İş yükü</option><option value="accessibility">Əlçatanlıq</option>
      <option value="tooling">Alət və texniki problem</option><option value="other">Digər</option>
    </select></label>
    <label>Konkret dəyişmiş addım (20–2000 simvol)<textarea minLength={20} maxLength={2000} required
      value={plan} onChange={e => setPlan(e.target.value)} /></label>
    <label className="check"><input type="checkbox" checked={agreed} onChange={e => setAgreed(e.target.checked)} />Bu planla davam etməyə razıyam.</label>
    <button type="submit" className="primary" disabled={busy || !agreed || plan.trim().length < 20}>Planı qəbul et</button>
  </form>;
}
