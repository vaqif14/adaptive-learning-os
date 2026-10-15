import React, { useEffect, useRef, useState } from 'react';
import { Text } from './ui.jsx';

// Faded ladder: worked example -> completion with visible ___ blanks -> the independent rung IS the practice.
// Azerbaijani: "pillə" (rung/step) should be reviewed by a native speaker.
// Rungs are teaching material, never evidence. Rung 3 has no content of its own: it opens the practice.
export function LadderRung({ rung, id }) {
  if (rung.rung === 'worked') {
    const body = rung.code_or_steps;
    return <article className="card ladder-rung" aria-label={rung.title}>
      <h4 id={id} tabIndex={-1}>{rung.title}</h4>
      {Array.isArray(body)
        ? <ol className="worked">{body.map((s, i) => <li key={i}><Text value={s} /></li>)}</ol>
        : <pre className="code" tabIndex={0} aria-label="Həll olunmuş nümunə"><code>{body}</code></pre>}
      <Text value={rung.explain_md} />
      {rung.note && <p className="muted">{rung.note}</p>}
    </article>;
  }
  return <article className="card ladder-rung" aria-label={rung.title}>
    <h4 id={id} tabIndex={-1}>{rung.title}</h4>
    <pre className="code" tabIndex={0} aria-label="Boşluqlu kod"><code>{rung.template}</code></pre>
    <p className="muted">{rung.blank_count} boşluq (<code>___</code>) var. Cavablar burada yoxlanmır: onları öz redaktorunda doldur, sonra müstəqil pilləyə keç.</p>
    <Text value={rung.explain_md} />
    {rung.note && <p className="muted">{rung.note}</p>}
  </article>;
}

export default function Ladder({ nodeId, act, busy, onIndependent }) {
  const [rungs, setRungs] = useState({});
  const focusRung = useRef(null);
  // A rung that is already open is not fetched again (each fetch records a help event): its heading just gets focus.
  const open = async rung => {
    if (rungs[rung]) { document.getElementById(`rung-${rung}`)?.focus(); return; }
    const out = await act('/api/course/help', { node: nodeId, kind: 'ladder', rung });
    if (out) { focusRung.current = rung; setRungs(prev => ({ ...prev, [rung]: out.content })); }
  };
  useEffect(() => {
    if (focusRung.current) { document.getElementById(`rung-${focusRung.current}`)?.focus(); focusRung.current = null; }
  }, [rungs]);
  return <section className="card ladder" aria-labelledby="ladder-title">
    <h2 id="ladder-title">Pilləli təlim</h2>
    <p className="muted">Üç pillə: əvvəl həll olunmuş nümunə, sonra boşluqları doldurmaq, axırda isə müstəqil iş (praktikanın özü). Bu pillələr öyrətmə materialıdır, sübut sayılmır.</p>
    <ol className="ladder-steps">
      <li><span className="rung-no">1</span> <button type="button" disabled={busy} aria-expanded={Boolean(rungs.worked)} aria-controls="rung-worked-box" onClick={() => open('worked')}>Həll olunmuş nümunəni aç</button>{rungs.worked && <span className="muted"> · açıldı</span>}</li>
      <li><span className="rung-no">2</span> <button type="button" disabled={busy} aria-expanded={Boolean(rungs.completion)} aria-controls="rung-completion-box" onClick={() => open('completion')}>Boşluqları doldur</button>
        {rungs.completion ? <span className="muted"> · açıldı</span> : !rungs.worked && <span className="muted"> · əvvəl 1-ci pillə tövsiyə olunur</span>}</li>
      <li><span className="rung-no">3</span> <button type="button" disabled={busy} onClick={onIndependent}>Müstəqil: praktikaya keç</button></li>
    </ol>
    <div id="rung-worked-box">{rungs.worked && <LadderRung rung={rungs.worked} id="rung-worked" />}</div>
    <div id="rung-completion-box">{rungs.completion && <LadderRung rung={rungs.completion} id="rung-completion" />}</div>
  </section>;
}
