import React, { useState } from 'react';
import { ActionBar, QuestionForm, RoleNote, Steps, Text } from './ui.jsx';
import { HelpCard } from './Lesson.jsx';

export function CheckList({ checks, title }) {
  const passed = checks.filter(c => c.passed).length;
  return <section className="ci" aria-label={title}>
    <h3>{title} <span className={passed === checks.length ? 'ok' : 'fail'}>{passed}/{checks.length} keçdi</span></h3>
    <ul>{checks.map(c => <li key={c.id} className={c.passed ? 'ok' : 'fail'}>
      <span aria-hidden="true">{c.passed ? '✓' : '✗'}</span>
      <span className="sr-only">{c.passed ? 'keçdi' : 'keçmədi'}: </span>
      <span>{c.name}{c.category && c.category !== 'visible' ? <em className="cat"> · {({ core: 'əsas', edge: 'kənar hal', transfer: 'görməmiş hal (transfer)', build: 'icra' })[c.category] || c.category}</em> : null}
        {!c.passed && c.feedback && <span className="why"> — {c.feedback}</span>}
        {!c.passed && c.observed != null && <span className="obs"> Sənin nəticən: <code>{c.observed}</code>{c.expected ? <> · gözlənilən: <code>{c.expected}</code></> : null}</span>}
        {!c.passed && c.error && <span className="err"> ({c.error})</span>}</span>
    </li>)}</ul>
  </section>;
}

export default function Practice(props) {
  const { info } = props;
  if (!info?.practice) return <p role="status">Praktika yüklənir…</p>;
  return info.practice.kind && info.practice.kind !== 'python' ? <ConceptPractice {...props} /> : <CodePractice {...props} />;
}

function PracticeResult({ result, alt, onExam, busy }) {
  if (!result) return null;
  return <div className={`card result ${result.passed ? 'ok' : 'fail'}`} role="status" aria-live="polite">
    <CheckList checks={result.checks} title="Yoxlama nəticəsi" />
    {/* Per-check lines are already in the CI list; show only the diagnosis lines here. */}
    <ul className="feedback">{result.feedback_az.filter(f => !f.startsWith('✗')).map((f, i) => <li key={i}>{f}</li>)}</ul>
    {result.method_change && <button type="button" disabled={busy} onClick={alt}>Başqa yolla izah et</button>}
    {result.passed && <button type="button" className="primary" disabled={busy} onClick={onExam}>Müstəqil yoxlamaya keç</button>}
  </div>;
}

// Concept/scenario practice: answered in the app, graded deterministically by the key,
// formative only (recorded as assisted/weak evidence, never mastery).
function ConceptPractice({ view, nodeId, info, act, confirmExam, busy }) {
  const [answers, setAnswers] = useState({});
  const [result, setResult] = useState(null);
  const [hints, setHints] = useState([]);
  const help = kind => async () => { const out = await act('/api/course/help', { node: nodeId, kind }); if (out) setHints(h => [...h, out]); };
  const check = async e => {
    e?.preventDefault();
    const out = await act('/api/course/practice/answer', { node: nodeId, answers });
    if (out) setResult(out);
  };
  const sp = view.support_provenance;
  const pr = info.practice;
  return <section className="practice" aria-labelledby="practice-title">
    <p className="eyebrow">Bələdçili praktika · anlayış</p>
    <h1 id="practice-title" tabIndex={-1}>{info.node.title}</h1>
    <Steps current="practice" />
    <form className="card" onSubmit={check}>
      <h2>Tapşırıq</h2><Text value={pr.prompt_md} />
      {pr.scenario_md && <div className="scenario"><h3>Ssenari</h3><Text value={pr.scenario_md} /></div>}
      <QuestionForm questions={pr.questions} value={answers} onChange={setAnswers} idPrefix="practice" disabled={busy} />
      <button type="submit" className="primary" disabled={busy}>Yoxla</button>
      <p className="muted">Bu praktika köməkli sayılır{sp.hints_count ? ` (${sp.hints_count} ipucu istifadə edilib)` : ''}; cavablar dərhal açarla yoxlanır və səhv seçimin arxasındakı anlaşılmazlıq göstərilir.</p>
    </form>
    {hints.map((h, i) => <HelpCard key={i} item={h} />)}
    <PracticeResult result={result} alt={help('alternative')} onExam={() => confirmExam(nodeId, false)} busy={busy} />
    <ActionBar busy={busy} actions={{
      why: { onClick: help('why'), hint: 'Bu bacarıq niyə vacibdir' },
      help: { onClick: help('hint'), hint: `İpucu (${pr.hint_count} səviyyə)` },
      check: { onClick: check, hint: 'Cavabları yoxla' },
      next: { onClick: () => confirmExam(nodeId, false), hint: 'Yeni, köməksiz tapşırıq' },
    }} />
    <RoleNote role="Checker (yoxlayan)" text="cavabını açarla yoxlayır və səhvin ehtimal olunan səbəbini deyir; düzgün cavabı əvvəlcədən göstərmir." />
  </section>;
}

function CodePractice({ view, nodeId, info, act, confirmExam, busy }) {
  const [result, setResult] = useState(null);
  const [hints, setHints] = useState([]);
  const [copied, setCopied] = useState(false);
  const file = info.practice.file;
  const hint = async () => { const out = await act('/api/course/help', { node: nodeId, kind: 'hint' }); if (out) setHints(h => [...h, out]); };
  const alt = async () => { const out = await act('/api/course/help', { node: nodeId, kind: 'alternative' }); if (out) setHints(h => [...h, out]); };
  const why = async () => { const out = await act('/api/course/help', { node: nodeId, kind: 'why' }); if (out) setHints(h => [...h, out]); };
  const check = async () => { const out = await act('/api/course/practice/check', { node: nodeId }); if (out) setResult(out); };
  const copy = async () => { try { await navigator.clipboard.writeText(file); setCopied(true); } catch { setCopied(false); } };
  const sp = view.support_provenance;
  return <section className="practice" aria-labelledby="practice-title">
    <p className="eyebrow">Bələdçili praktika</p>
    <h1 id="practice-title" tabIndex={-1}>{info.node.title}</h1>
    <Steps current="practice" />
    <div className="card"><h2>Tapşırıq</h2><Text value={info.practice.prompt_md} />
      <h3>Faylın</h3>
      <p className="file"><code>{file}</code> <button type="button" onClick={copy}>{copied ? 'Kopyalandı' : 'Yolu kopyala'}</button></p>
      <p className="muted">Kodu öz redaktorunda yaz və saxla. Tətbiq kodu icra etmir; “Yoxla” lokal runtime-da təhlükəsiz yoxlayıcı ilə işləyir.</p>
      <h3>Görünən yoxlamalar</h3><ul>{info.practice.checks.map(c => <li key={c.id}>{c.name}</li>)}</ul>
      <p className="muted">Bu praktika köməkli sayılır{sp.hints_count ? ` (${sp.hints_count} ipucu istifadə edilib)` : ''}; müstəqil bacarığı sonrakı yeni tapşırıq göstərəcək.</p>
    </div>
    {hints.map((h, i) => <HelpCard key={i} item={h} />)}
    <PracticeResult result={result} alt={alt} onExam={() => confirmExam(nodeId, false)} busy={busy} />
    <ActionBar busy={busy} actions={{
      now: { onClick: copy, hint: 'Fayl yolunu kopyala və redaktorda aç' },
      why: { onClick: why, hint: 'Bu bacarıq niyə vacibdir' },
      help: { onClick: hint, hint: `İpucu (${info.practice.hint_count} səviyyə)` },
      check: { onClick: check, hint: 'Görünən yoxlamaları işlət' },
      next: { onClick: () => confirmExam(nodeId, false), hint: 'Yeni, köməksiz tapşırıq' },
    }} />
    <RoleNote role="Checker (yoxlayan)" text="səhvi və çatışmayanı göstərir, kodunu sənin yerinə yenidən yazmır." />
  </section>;
}
