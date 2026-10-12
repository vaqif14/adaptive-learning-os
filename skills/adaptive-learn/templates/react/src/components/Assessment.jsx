import React, { useState } from 'react';
import { ActionBar, RoleNote, Steps, Text } from './ui.jsx';
import { CheckList } from './Practice.jsx';

const LOCKED = 'İmtahan zamanı izah və ipucu bağlıdır';

export function Exam({ view, act, go }) {
  const att = view.active_attempt;
  const [confirmAbandon, setConfirmAbandon] = useState(false);
  if (!att) return <p role="status">Aktiv imtahan yoxdur.</p>;
  const submit = async () => {
    const out = await act('/api/course/exam/submit', { attempt_id: att.attempt_id });
    if (out?.status === 'finalized') go(`#/result/${att.attempt_id}`);
  };
  const abandon = async () => {
    const out = await act('/api/course/exam/abandon', { attempt_id: att.attempt_id, reason: 'learner_exit' });
    if (out) go('#/');
  };
  const cats = { core: 'əsas', edge: 'kənar hal', transfer: 'görmədiyin hal (transfer)' };
  const failed = att.status === 'scoring_failed' || view.attempt_status === 'scoring_failed';
  return <section className="exam" aria-labelledby="exam-title">
    <div className="lock-banner" role="note"><strong>Müstəqil yoxlama.</strong> Bu cəhd bitənə qədər dərs, izah və ipucu bağlıdır — səhifəni yeniləsən, geri qayıtsan və ya başqa pəncərə açsan da eyni cəhdə qayıdacaqsan.</div>
    <p className="eyebrow">{att.fast_track ? 'Birbaşa yoxlama' : 'Yeni tapşırıq'} · {att.item.scope === 'near_transfer' ? 'yaxın transfer' : 'müstəqil təkrar'}</p>
    <h1 id="exam-title" tabIndex={-1}>{att.item.title}</h1>
    <Steps current="exam" />
    <div className="card"><Text value={att.item.prompt_md} />
      <h3>Faylın</h3><p className="file"><code>{att.file}</code></p>
      <p className="muted">Göndərəndən sonra {att.item.check_count} gizli yoxlama işləyəcək ({att.item.check_categories.map(c => cats[c] || c).join(', ')}). Gizli halların girişləri göstərilmir ki, cavab onlara uyğunlaşdırılmasın.</p>
    </div>
    {failed && <div className="card warn" role="alert"><p>Qiymətləndirmə tamamlanmadı ({att.scoring_error || 'səbəb qeyd olunub'}). Cavabın saxlanıb və uğur və ya uğursuzluq kimi sayılmayıb. Yenidən cəhd et.</p>
      <button type="button" className="primary" onClick={submit}>Qiymətləndirməni yenidən başlat</button></div>}
    <ActionBar actions={{
      now: { onClick: submit, hint: 'Göndər və gizli yoxlamaları işlət', disabled: view.attempt_status === 'finalized' },
      why: { onClick: () => {}, disabled: true, hint: LOCKED },
      help: { onClick: () => {}, disabled: true, hint: LOCKED },
      check: { onClick: submit, hint: 'Göndər və yoxla' },
    }} />
    {!confirmAbandon
      ? <button type="button" className="link" onClick={() => setConfirmAbandon(true)}>Cəhdi dayandır</button>
      : <div className="card warn" role="alertdialog" aria-labelledby="abandon-q"><p id="abandon-q">Cəhdi dayandırsan, o uğur kimi sayılmayacaq və bu tapşırıq bir daha verilməyəcək. Davam edək?</p>
          <button type="button" onClick={abandon}>Bəli, dayandır</button> <button type="button" className="primary" onClick={() => setConfirmAbandon(false)}>Xeyr, imtahana qayıt</button></div>}
    <RoleNote role="Examiner (imtahan edən)" text="yalnız görmədiyin yeni tapşırıq verir; cəhd zamanı izah etmir." />
    <p className="muted small">{view.honest_boundaries_az[0]}</p>
  </section>;
}

const OUTCOME = { correct: ['Keçdi', 'ok'], partial: ['Qismən keçdi', 'partial'], incorrect: ['Keçmədi', 'fail'] };

export function Result({ view, act, go }) {
  const r = view.assessment_result;
  if (!r) return <p role="status">Nəticə hələ yekunlaşmayıb.</p>;
  const [label, cls] = OUTCOME[r.outcome] || [r.outcome, ''];
  const next = async () => { if (await act('/api/course/next', {})) go('#/'); };
  return <section className="result-view" aria-labelledby="result-title">
    <p className="eyebrow">Yekunlaşmış nəticə</p>
    <h1 id="result-title" tabIndex={-1} className={cls}>{label}: {r.passed}/{r.total} yoxlama</h1>
    <Steps current="result" />
    <div className="card"><CheckList checks={r.checks} title="Gizli yoxlamalar" />
      {r.misconception_feedback?.length > 0 && <><h3>Cavabında görünən anlaşılmazlıq</h3>
        <ul>{r.misconception_feedback.map(m => <li key={m.id}><strong>{m.name}:</strong> {m.feedback}</li>)}</ul></>}
      <p className="muted">{r.independence_note_az} Sübut: <code>{r.evidence_id}</code></p>
    </div>
    <MissionCard view={view} act={act} go={go} />
    {view.phase === 'feedback' && <ActionBar actions={{ next: { onClick: next, hint: 'Növbəti missiyaya keç' } }} />}
    <RoleNote role="Diagnostician (diaqnost)" text="səhvin arxasındakı ehtimal olunan anlaşılmazlığı göstərir; bir cəhd səbəbi tam sübut etmir." />
  </section>;
}

export function MissionCard({ view, act, go, confirmExam }) {
  const m = view.next_mission;
  if (!m) return null;
  const nodes = view.roadmap.nodes;
  const node = nodes.find(n => n.id === m.node_id);
  const start = async () => {
    if (await act('/api/course/mission/start', { node: m.node_id })) go(`#/learn/${m.node_id}`);
  };
  const blocked = m.blocked_on === 'lesson_content_missing'
    ? 'Bu mərhələnin dərsi hələ hazırlanmayıb. Agentdən mənbələrə əsaslanan dərsi hazırlamasını istə — hazır olmayan dərs “hazır” kimi göstərilmir.'
    : m.blocked_on === 'no_fresh_item' ? 'Görmədiyin yeni yoxlama tapşırığı qalmayıb. Agent yeni tapşırıq hazırlamalıdır.' : null;
  const open = view.phase !== 'feedback' && node && node.status !== 'locked';
  return <article className="card mission" aria-labelledby="mission-title">
    <p className="eyebrow">Növbəti missiya</p>
    <h2 id="mission-title">{m.title || node?.title}</h2>
    <p>{m.reason_az}</p>
    {m.uncertainty_az && <p className="muted">{m.uncertainty_az}</p>}
    {m.review && <p className="muted">Təkrar yoxlama ən tez: {new Date(m.review.not_before).toLocaleString('az-AZ')}</p>}
    {blocked && <p className="warn" role="status">{blocked}</p>}
    {open && <div className="entry-actions">
      {m.lesson_ready && <button type="button" className="primary" onClick={start}>İndi et</button>}
      {confirmExam && node?.fresh_items > 0 && <button type="button" onClick={() => confirmExam(m.node_id, true)}>Bunu bilirəm — birbaşa yoxla</button>}
    </div>}
  </article>;
}
