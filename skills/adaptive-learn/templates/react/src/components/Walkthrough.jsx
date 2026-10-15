import React, { useEffect, useRef, useState } from 'react';
import { Refs, Text } from './ui.jsx';

// Stepper over an agent-authored, source-grounded demonstration. It is a DIFFERENT instance from the practice and the
// exams, and watching it earns no evidence (the server says so in `content.note`). The learner's own prediction is
// never stored or sent anywhere: predict-then-reveal is a thinking aid, not an answer form.
// Azerbaijani wording below is plain UI text; "nümayiş" (demonstration) and "proqnoz" (prediction) should be
// reviewed by a native speaker; "qəsdən edilmiş səhv" (deliberate mistake) likewise.
export default function Walkthrough({ content, heading = 'h3' }) {
  const steps = content.steps || [];
  const [index, setIndex] = useState(0);
  const [guess, setGuess] = useState({});
  const [revealed, setRevealed] = useState({});
  const H = heading;
  // Where focus goes after a step change: the step title (Next/Previous buttons) or the stepper button (arrow keys).
  const focusAfter = useRef(null);
  const titleRef = useRef(null);
  const navRef = useRef(null);
  useEffect(() => {
    const target = focusAfter.current;
    focusAfter.current = null;
    if (target === 'title') { titleRef.current?.focus(); titleRef.current?.scrollIntoView?.({ block: 'start' }); }
    else if (target === 'nav') navRef.current?.querySelectorAll('button')[index]?.focus();
  }, [index]);
  if (!steps.length) return null;
  const step = steps[index];
  const mistake = content.mistake || {};
  const isMistake = mistake.step === index + 1;
  const gated = Boolean(step.predict) && !revealed[index];
  // `focus` is set only when the step really changes, so a press at either end leaves no stale focus request behind.
  const go = (i, focus) => {
    const to = Math.max(0, Math.min(steps.length - 1, i));
    if (to !== index) focusAfter.current = focus || null;
    setIndex(to);
  };
  const onKeys = e => {
    if (!e.target.closest?.('.wt-nav')) return;
    // Only Left/Right are taken; Up/Down keep scrolling the page.
    const next = e.key === 'ArrowRight' ? index + 1 : e.key === 'ArrowLeft' ? index - 1 : null;
    if (next === null) return;
    e.preventDefault();
    go(next, 'nav');
  };
  const stepBy = delta => go(index + delta, 'title');
  return <article className="card walkthrough" aria-label={content.title} onKeyDown={onKeys}>
    <p className="wt-banner" role="note">{content.label || 'Agent tərəfindən yazılmış, mənbəyə əsaslanan nümayiş'}</p>
    <H>{content.title}</H>
    <Text value={content.scenario_md} />
    <ol className="wt-nav" aria-label="Nümayiş addımları" ref={navRef}>
      {steps.map((_, i) => <li key={i}>
        <button type="button" onClick={() => go(i)} aria-current={i === index ? 'step' : undefined}
                aria-label={`Addım ${i + 1}${mistake.step === i + 1 ? ' (qəsdən edilmiş səhv)' : ''}`}
                className={`${i === index ? 'current' : ''}${mistake.step === i + 1 ? ' has-mistake' : ''}`}>{i + 1}{mistake.step === i + 1 && <span aria-hidden="true"> ⚠</span>}</button>
      </li>)}
    </ol>
    {mistake.step >= 1 && mistake.step <= steps.length && <p className="muted small"><span aria-hidden="true">⚠ </span>= qəsdən edilmiş səhv olan addım</p>}
    <section className="wt-step" aria-label={`Addım ${index + 1} / ${steps.length}`}>
      <h4 tabIndex={-1} ref={titleRef} aria-live="polite">Addım {index + 1} / {steps.length}</h4>
      <Text value={step.explain_md} />
      {step.code && <pre className="code" tabIndex={0} aria-label="Addımın kodu"><code>{step.code}</code></pre>}
      {step.predict && <div className="predict">
        <label htmlFor={`predict-${index}`}>Əvvəl özün proqnoz ver: {step.predict.prompt}</label>
        <textarea id={`predict-${index}`} rows={2} value={guess[index] || ''} placeholder="Fikrini bir-iki sözlə yaz (yadda saxlanmır)"
                  onChange={e => setGuess({ ...guess, [index]: e.target.value })} />
        {gated ? <button type="button" onClick={() => setRevealed({ ...revealed, [index]: true })}>Cavabı göstər</button>
          : <p className="reveal" role="status"><strong>Cavab:</strong> {step.predict.reveal}</p>}
      </div>}
      {step.checkpoint && (gated
        ? <p className="muted">Gözlənilən çıxış proqnozdan sonra açılır.</p>
        : <div className="checkpoint"><h5>Gözlənilən çıxış</h5>
            <pre className="code out" tabIndex={0} aria-label="Gözlənilən çıxış"><code>{step.checkpoint.expected_output}</code></pre></div>)}
      {isMistake && gated && <p className="muted">Qəsdən edilmiş səhvin izahı proqnozdan sonra açılır.</p>}
      {isMistake && !gated && <div className="mistake" role="group" aria-label="Qəsdən edilmiş səhv">
        <h5><span aria-hidden="true">⚠ </span>Qəsdən edilmiş səhv</h5>
        {mistake.wrong_code && <><p className="muted">Səhv variant:</p>
          <pre className="code wrong" tabIndex={0} aria-label="Səhv kod"><code>{mistake.wrong_code}</code></pre></>}
        <p><strong>Simptom:</strong> {mistake.symptom}</p>
        <p><strong>Düzəliş:</strong> {mistake.fix_explain}</p>
      </div>}
    </section>
    <div className="wt-controls">
      <button type="button" onClick={() => stepBy(-1)} aria-disabled={index === 0 ? 'true' : undefined}>Əvvəlki addım</button>
      <button type="button" className="primary" onClick={() => stepBy(1)} aria-disabled={index === steps.length - 1 ? 'true' : undefined}>Növbəti addım</button>
    </div>
    {index === steps.length - 1 && <p className="muted">Nümayiş bitdi. İndi özün cəhd et: izləmək sübut sayılmır.</p>}
    {content.transcript_md && <details><summary>Mətn variantı</summary><Text value={content.transcript_md} /></details>}
    <Refs refs={content.refs} />
    {content.note && <p className="muted">{content.note}</p>}
  </article>;
}
