import React, { useState } from 'react';
import { ActionBar, RoleNote, Steps, Text } from './ui.jsx';
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

export default function Practice({ view, nodeId, info, act, confirmExam }) {
  const [result, setResult] = useState(null);
  const [hints, setHints] = useState([]);
  const [copied, setCopied] = useState(false);
  if (!info?.practice) return <p role="status">Praktika yüklənir…</p>;
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
    {result && <div className={`card result ${result.passed ? 'ok' : 'fail'}`} role="status" aria-live="polite">
      <CheckList checks={result.checks} title="Yoxlama nəticəsi" />
      {/* Per-check lines are already in the CI list; show only the diagnosis lines here. */}
      <ul className="feedback">{result.feedback_az.filter(f => !f.startsWith('✗')).map((f, i) => <li key={i}>{f}</li>)}</ul>
      {result.method_change && <button type="button" onClick={alt}>Başqa yolla izah et</button>}
      {result.passed && <button type="button" className="primary" onClick={() => confirmExam(nodeId, false)}>Müstəqil yoxlamaya keç</button>}
    </div>}
    <ActionBar actions={{
      now: { onClick: copy, hint: 'Fayl yolunu kopyala və redaktorda aç' },
      why: { onClick: why, hint: 'Bu bacarıq niyə vacibdir' },
      help: { onClick: hint, hint: `İpucu (${info.practice.hint_count} səviyyə)` },
      check: { onClick: check, hint: 'Görünən yoxlamaları işlət' },
      next: { onClick: () => confirmExam(nodeId, false), hint: 'Yeni, köməksiz tapşırıq' },
    }} />
    <RoleNote role="Checker (yoxlayan)" text="səhvi və çatışmayanı göstərir, kodunu sənin yerinə yenidən yazmır." />
  </section>;
}
