import React, { useState } from 'react';

// Minimal, safe text rendering: paragraphs, "- " bullets, `inline code` and **bold**.
// Content is always rendered as text nodes (React escapes it); no HTML injection.
function inline(text) {
  return String(text).split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((part, i) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) return <strong key={i}>{part.slice(2, -2)}</strong>;
    return <React.Fragment key={i}>{part}</React.Fragment>;
  });
}

export function Text({ value }) {
  if (!value) return null;
  const blocks = String(value).split(/\n{2,}/);
  return <>{blocks.map((block, i) => {
    const lines = block.split('\n');
    if (lines.every(l => l.trim().startsWith('- '))) {
      return <ul key={i}>{lines.map((l, j) => <li key={j}>{inline(l.trim().slice(2))}</li>)}</ul>;
    }
    return <p key={i}>{lines.map((l, j) => <React.Fragment key={j}>{j > 0 && <br />}{inline(l)}</React.Fragment>)}</p>;
  })}</>;
}

// Labels come from the server's grounding decision. Teaching is only served while every
// source quote re-verifies against the committed sources, so "Mənbədən" is never stale.
export function Refs({ refs }) {
  if (!refs?.length) return null;
  return <div className="refs" aria-label="Mənbə əsası">
    {refs.map((r, i) => r.grounding === 'source'
      ? <blockquote key={i} className="ref source"><span className="ref-label">Mənbədən (yoxlanılıb){r.title ? ` · ${r.title}` : ''}</span>“{r.quote}”</blockquote>
      : <p key={i} className="ref supplementary"><span className="ref-label">Əlavə izah — mənbədə yoxdur</span>{r.note}</p>)}
  </div>;
}

export const LESSON_STATUS = {
  ready: 'dərs hazırdır', missing: 'dərs hazır deyil', grounding_invalid: 'mənbə yenidən yoxlanmalıdır',
  capability_changed: 'məqsəd dəyişib — dərs yenilənməlidir', locked_during_exam: 'imtahan zamanı bağlıdır',
  review_required: 'əvvəl müstəqil təkrar yoxlama',
};

// Accessible answer form for concept/scenario questions (practice and exam).
export function QuestionForm({ questions, value, onChange, idPrefix, disabled }) {
  const set = (qid, v) => onChange({ ...value, [qid]: v });
  return <div className="questions">{questions.map((q, qi) => {
    const name = `${idPrefix}-${q.id}`;
    const v = value[q.id];
    return <fieldset key={q.id} className="question" disabled={disabled}>
      <legend><span className="qnum">{qi + 1}.</span> {q.prompt}</legend>
      {q.type === 'single_choice' && q.options.map(o => <label key={o.id} className="radio">
        <input type="radio" name={name} value={o.id} checked={v === o.id} onChange={() => set(q.id, o.id)} /> {o.text}</label>)}
      {q.type === 'multi_choice' && <>{q.options.map(o => {
        const arr = Array.isArray(v) ? v : [];
        return <label key={o.id} className="check"><input type="checkbox" checked={arr.includes(o.id)}
          onChange={e => set(q.id, e.target.checked ? [...arr, o.id] : arr.filter(x => x !== o.id))} /> {o.text}</label>;
      })}<p className="muted small">Bir neçə düzgün cavab ola bilər.</p></>}
      {q.type === 'ordering' && <Ordering q={q} answered={Array.isArray(v) && v.length === q.options.length}
        value={Array.isArray(v) && v.length === q.options.length ? v : q.options.map(o => o.id)} onChange={nv => set(q.id, nv)} />}
      {q.type === 'numeric' && <label>Cavab{q.unit ? ` (${q.unit})` : ''}
        <input inputMode="decimal" value={v ?? ''} onChange={e => set(q.id, e.target.value)} /></label>}
    </fieldset>;
  })}</div>;
}

function Ordering({ q, value, onChange, answered }) {
  const byId = Object.fromEntries(q.options.map(o => [o.id, o]));
  const [msg, setMsg] = useState('');
  const move = (i, d) => {
    const nv = [...value];
    const j = i + d;
    if (j < 0 || j >= nv.length) return;
    [nv[i], nv[j]] = [nv[j], nv[i]];
    onChange(nv);
    setMsg(`${byId[nv[j]].text}: ${j + 1}-ci yerdə`);
  };
  return <>
    <ol className="ordering">{value.map((id, i) => <li key={id}><span>{byId[id].text}</span>
      <button type="button" onClick={() => move(i, -1)} disabled={i === 0} aria-label={`${byId[id].text} — yuxarı`}>↑</button>
      <button type="button" onClick={() => move(i, 1)} disabled={i === value.length - 1} aria-label={`${byId[id].text} — aşağı`}>↓</button>
    </li>)}</ol>
    {!answered && <button type="button" onClick={() => onChange([...value])}>Bu sıranı cavab kimi seç</button>}
    {answered && <p className="muted small">Sıra cavab kimi seçilib; dəyişmək üçün oxlardan istifadə et.</p>}
    <span className="sr-only" aria-live="polite">{msg}</span>
  </>;
}

export const ACTIONS = { now: 'İndi et', why: 'Niyə', help: 'Kömək', check: 'Yoxla', next: 'Sonrakı' };

// The five default actions. Each entry: {onClick, disabled, hint}; missing = hidden.
export function ActionBar({ actions, busy }) {
  return <div className="action-bar" role="toolbar" aria-label="Əsas addımlar">
    {Object.entries(ACTIONS).map(([key, label]) => {
      const a = actions[key];
      if (!a) return null;
      // Accessible name stays exactly the action label; the hint is a description. A locked
      // action stays focusable (aria-disabled) so keyboard and screen-reader users hear why.
      return <React.Fragment key={key}>
        <button type="button" className={key === 'now' ? 'primary' : ''}
                onClick={a.disabled || busy ? undefined : a.onClick}
                aria-disabled={a.disabled || busy ? 'true' : undefined} aria-busy={busy || undefined}
                title={a.hint || undefined}
                aria-describedby={a.hint ? `hint-${key}` : undefined}>{label}</button>
        {a.hint && <span id={`hint-${key}`} className="sr-only">{a.hint}</span>}
      </React.Fragment>;
    })}
  </div>;
}

export function RoleNote({ role, text }) {
  return <details className="role-note"><summary>Bu addım haqqında</summary>
    <p><strong>{role}</strong> — {text}</p></details>;
}

const RESOURCE_STATUS = {
  validated: 'Yoxlanılıb', missing: 'Notebook-da tapılmadı', failed_import: 'İdxal alınmadı',
  partial: 'Natamam mətn', unreadable: 'Mətn oxunmur', empty: 'Boş', processing: 'Hələ emal olunur',
  identity_mismatch: 'Məzmun gözlənilən mövzu deyil',
  cli_unavailable: 'NotebookLM CLI əlçatan deyil',
};
export const resourceStatus = s => RESOURCE_STATUS[s] || s;

export const NODE_STATUS = { passed: 'Keçilib', active: 'Hazırda', available: 'Açıqdır', locked: 'İlkin şərt lazımdır' };

export function Steps({ current }) {
  const steps = [['teach', 'İzah'], ['example', 'Nümunə'], ['self', 'Öz sözlərinlə'], ['practice', 'Praktika'],
                 ['exam', 'Müstəqil yoxlama'], ['result', 'Nəticə']];
  const idx = steps.findIndex(s => s[0] === current);
  return <ol className="steps" aria-label="Dərs mərhələləri">
    {steps.map(([key, label], i) => <li key={key} className={i < idx ? 'done' : i === idx ? 'current' : ''}
                                         aria-current={i === idx ? 'step' : undefined}>{label}</li>)}
  </ol>;
}
