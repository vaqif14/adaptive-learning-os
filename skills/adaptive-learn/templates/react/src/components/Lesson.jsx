import React, { useState } from 'react';
import { ActionBar, Refs, RoleNote, Steps, Text, resourceStatus } from './ui.jsx';

// Teaching view. Content arrives only through recorded help actions, so every
// explanation or worked example the learner sees is part of the support provenance.
export function HelpCard({ item }) {
  const c = item.content;
  return <article className={`card help-${item.kind}`} aria-label={c.title}>
    <h3>{c.title}</h3>
    {c.short && <p className="lead">{c.short}</p>}
    {c.problem && <><h4>Məsələ</h4><Text value={c.problem} /></>}
    {c.steps && <ol className="worked">{c.steps.map((s, i) => <li key={i}><Text value={s} /></li>)}</ol>}
    {c.result && <p><strong>Nəticə:</strong> {c.result}</p>}
    <Text value={c.body} />
    {c.example && <p className="example"><strong>Nümunə:</strong> {c.example}</p>}
    {c.note && <p className="muted">{c.note}</p>}
    <Refs refs={c.refs} />
  </article>;
}

export default function Lesson({ view, nodeId, info, act, go, confirmExam, busy }) {
  const [cards, setCards] = useState([]);
  const [text, setText] = useState('');
  const [coverage, setCoverage] = useState(null);
  if (!info) return <p role="status">Dərs yüklənir…</p>;
  const sp = view.support_provenance;
  const ready = info.lesson?.status === 'ready';
  const help = async kind => {
    const out = await act('/api/course/help', { node: nodeId, kind });
    if (out) setCards(prev => [...prev.filter(c => c.kind !== kind), out]);
  };
  const begin = async () => { if (await act('/api/course/practice/begin', { node: nodeId })) go(`#/practice/${nodeId}`); };
  const nextHelp = !sp.explanation_shown ? 'explain' : !sp.worked_example_shown ? 'worked_example' : 'alternative';
  const step = !sp.explanation_shown ? 'teach' : !sp.worked_example_shown ? 'example' : 'self';
  const sendSelf = async e => {
    e.preventDefault();
    const out = await act('/api/course/self-explanation', { node: nodeId, text });
    if (out) setCoverage(out);
  };
  if (!ready) {
    const st = info.lesson?.status;
    const snap = info.lesson?.historical_snapshot;
    const message = st === 'grounding_invalid'
      ? 'Bu dərsin mənbəsi dəyişib və ya artıq etibarlı deyil. Mənbə yenidən yoxlanana qədər izah, ipucu, praktika və yoxlama bağlıdır — köhnə material cari kimi göstərilmir.'
      : st === 'capability_changed'
        ? 'Bu mərhələnin məqsədi yol xəritəsində dəyişib. Köhnə dərs və nəticələr tarixçədə qalır; yeni məqsəd üçün dərs hazırlanmalıdır.'
        : 'Bu mərhələnin dərs məzmunu hələ hazırlanmayıb. Agent mənbələrə əsaslanan izahı, nümunəni və praktikanı hazırlayandan sonra burada görünəcək.';
    return <section className="lesson"><h1 tabIndex={-1}>{info.node.title}</h1>
      <p role="status" className="warn">{message}</p>
      {snap && <p className="muted">{snap.label_az} Versiya <code>{snap.content_version}</code>{snap.installed_at ? `, ${new Date(snap.installed_at).toLocaleDateString('az-AZ')}` : ''}.</p>}
      {info.lesson?.grounding_problems?.length > 0 && <ul className="muted">{info.lesson.grounding_problems.map((p, i) =>
        <li key={i}>{p.source_id}: {({ source_changed_since_binding: 'mənbə mətni dəyişib', quote_not_found_in_source: 'sitat yeni mətndə yoxdur',
          cached_text_missing_revalidate_sources: 'mənbə mətni tapılmadı', source_not_in_manifest: 'mənbə manifestdə yoxdur' })[p.reason]
          || (p.reason?.startsWith('source_status_') ? `mənbənin vəziyyəti: ${resourceStatus(p.reason.slice(14)).toLowerCase()}` : p.reason)}</li>)}</ul>}
    </section>;
  }
  return <section className="lesson" aria-labelledby="lesson-title">
    <p className="eyebrow">Dərs · {info.node.exam_domain || 'mərhələ'}</p>
    <h1 id="lesson-title" tabIndex={-1}>{info.node.title}</h1>
    <Steps current={step} />
    <div className="card objective"><h2>Bu dərsin məqsədi</h2><p>{info.lesson.objective}</p>
      <h3>Düzgün cavab nəyi göstərməlidir</h3><ul>{info.lesson.success_criteria.map((c, i) => <li key={i}>{c}</li>)}</ul>
      {info.lesson.grounding !== 'source' && <p className="muted">Bəzi hissələr mənbədən deyil, əlavə izahdır — hər birinin yanında qeyd olunub.</p>}
    </div>
    {!sp.explanation_shown && !sp.worked_example_shown && cards.length === 0 &&
      <div className="card entry"><h2>Bu mövzu sənə nə qədər tanışdır?</h2>
        <div className="entry-actions">
          <button type="button" className="primary" disabled={busy} onClick={() => help('explain')}>Bilmirəm — qısa izah et</button>
          <button type="button" disabled={busy} onClick={() => confirmExam(nodeId, true)}>Bunu bilirəm — yeni tapşırıqla yoxla</button>
        </div>
        <p className="muted">Birbaşa yoxlama görmədiyin yeni tapşırıqdır; keçməsən, izahdan davam edəcəyik.</p>
      </div>}
    {cards.map(item => <HelpCard key={item.kind} item={item} />)}
    {(sp.explanation_shown || sp.worked_example_shown) &&
      <form className="card self" onSubmit={sendSelf}>
        <h2><label htmlFor="self-text">Öz sözlərinlə izah et</label></h2>
        <p>{info.self_explanation_prompt}</p>
        <textarea id="self-text" rows={4} value={text} onChange={e => setText(e.target.value)} minLength={10} required />
        <button type="submit" disabled={busy || text.trim().length < 10}>Göndər</button>
        {coverage && <div role="status" className="coverage">
          <p>Açar anlayışların əhatəsi: {Math.round(coverage.coverage * 100)}%.{coverage.missed?.length ? ` Toxunmadığın anlayışlar: ${coverage.missed.join(', ')}.` : ''}</p>
          <p className="muted">{coverage.note_az}</p></div>}
      </form>}
    <ActionBar busy={busy} actions={{
      now: { onClick: () => (step === 'self' ? begin() : help(nextHelp)), hint: step === 'self' ? 'Bələdçili praktikaya keç' : 'Növbəti izah addımı' },
      why: { onClick: () => help('why'), hint: 'Bu mövzu məqsədin üçün niyə vacibdir' },
      help: { onClick: () => help(nextHelp), hint: nextHelp === 'alternative' ? 'Başqa yolla izah' : 'İzah / nümunə' },
      check: { onClick: begin, hint: 'Praktikaya keç və kodunu yoxlat' },
      next: { onClick: begin, hint: 'Bələdçili praktika' },
    }} />
    <RoleNote role={step === 'self' ? 'Socratic Questioner (sual verən)' : 'Explainer (izah edən)'}
              text={step === 'self' ? 'cavabı vermir; öz sözlərinlə izah etməyini istəyir ki, boşluğu özün tapasan.'
                                    : 'yalnız lazım olan hissəni izah edir; sonra müstəqil cəhd gəlir.'} />
  </section>;
}
