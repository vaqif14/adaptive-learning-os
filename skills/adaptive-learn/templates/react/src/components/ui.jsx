import React from 'react';

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

export function Refs({ refs }) {
  if (!refs?.length) return null;
  return <div className="refs" aria-label="Mənbə əsası">
    {refs.map((r, i) => r.grounding === 'source'
      ? <blockquote key={i} className="ref source"><span className="ref-label">Mənbədən{r.title ? ` · ${r.title}` : ''}{r.stale_source ? ' · köhnəlmiş ola bilər' : ''}</span>“{r.quote}”</blockquote>
      : <p key={i} className="ref supplementary"><span className="ref-label">Əlavə izah — mənbədə yoxdur</span>{r.note}</p>)}
  </div>;
}

export const ACTIONS = { now: 'İndi et', why: 'Niyə', help: 'Kömək', check: 'Yoxla', next: 'Sonrakı' };

// The five default actions. Each entry: {onClick, disabled, hint}; missing = hidden.
export function ActionBar({ actions, busy }) {
  return <div className="action-bar" role="toolbar" aria-label="Əsas addımlar">
    {Object.entries(ACTIONS).map(([key, label]) => {
      const a = actions[key];
      if (!a) return null;
      // Accessible name stays exactly the action label; the hint is a description.
      return <React.Fragment key={key}>
        <button type="button" className={key === 'now' ? 'primary' : ''} onClick={a.onClick}
                disabled={busy || a.disabled} title={a.hint || undefined}
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
