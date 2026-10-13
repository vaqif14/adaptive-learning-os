import React, { useEffect, useRef, useState } from 'react';
import { Text } from './ui.jsx';

// Capture learner work only. Correctness and diagnostic judgments belong to the
// host agent; this form never accepts or fabricates a score.
export default function Intake({ view, act, busy }) {
  const intake = view.intake;
  const [answer, setAnswer] = useState('');
  const [assisted, setAssisted] = useState(false);
  const [help, setHelp] = useState('');
  const [skipOpen, setSkipOpen] = useState(false);
  const [skipReason, setSkipReason] = useState('');
  const heading = useRef(null);
  useEffect(() => { heading.current?.focus(); }, []);
  if (!intake) return null;
  const goal = intake.action === 'ask_goal';
  const background = intake.action === 'ask_background';
  const clarification = intake.action === 'clarify_goal'
    || (view.generation_readiness?.clarification_required && intake.ready_to_plan);
  const probe = intake.probe;
  const waiting = probe?.status === 'awaiting_judgment' || intake.status === 'awaiting_judgment';
  const ready = intake.action === 'ready_to_plan';
  const submit = async e => {
    e.preventDefault();
    const out = await act(goal || background ? '/api/course/intake' : '/api/course/diagnostic/answer',
      goal ? { goal: answer.trim() } : background ? { stated_background: answer.trim() }
        : { response: answer.trim(), assistance: assisted ? [help.trim() || 'Learner kömək aldığını qeyd edib.'] : [] });
    if (out) { setAnswer(''); setAssisted(false); setHelp(''); }
  };
  return <section className="card intake" aria-labelledby="intake-title">
    <p className="eyebrow">Başlanğıc · bir addım hər dəfə</p>
    <h2 id="intake-title" tabIndex={-1} ref={heading}>{goal ? 'Məqsədin nədir?' : clarification ? 'Məqsədi dəqiqləşdirək' : background ? 'Başlanğıc təcrübən' : 'Qısa başlanğıc yoxlaması'}</h2>
    {goal || background ? <form onSubmit={submit}>
      <label htmlFor="intake-answer">{goal ? 'Sonda nəyi müstəqil edə bilmək istəyirsən?' : 'Bu işə aid indiyə qədər nə etmisən? Bir konkret nümunə yaz.'}</label>
      <textarea id="intake-answer" rows={3} value={answer} onChange={e => setAnswer(e.target.value)}
        maxLength={goal ? 2000 : 4000} required aria-describedby="intake-context" />
      <p id="intake-context" className="muted">{goal ? 'Konkret işi qeyd et: məsələn, modeli seçmək və seçimini izah etmək.' : '“Bilmirəm” cavabı da uyğundur. Özünü qiymətləndirmən bacarıq sübutu sayılmır.'}</p>
      <button type="submit" className="primary" disabled={busy || !answer.trim()}>Cavabı qeyd et</button>
    </form> : clarification ? <div role="status">
      <p>Məqsədin bir neçə cür başa düşülə bilər. Agentlə söhbətdə konkret nə etmək istədiyini dəqiqləşdir; nəticə sənin niyyətini təsdiqləyən cavaba əsaslanacaq.</p>
      <p className="muted">Bu ekranda məqsəd və bacarıq avtomatik qiymətləndirilmir.</p>
    </div> : waiting ? <div role="status">
      <p>Cavabın saxlanıb. Agent onu tapşırığın meyarları ilə yoxlayır; hələ düzgünlük və ya bacarıq nəticəsi verilməyib.</p>
      {probe?.response && <details><summary>Göndərdiyim cavab</summary><Text value={probe.response} /></details>}
    </div> : ready ? <p>{intake.skipped ? 'Qısa diagnostic razılığınla keçirildi; bacarıq sübutu yaranmayıb.' : 'Qısa diagnostic üçün tələb olunan müşahidələr qeydə alınıb; bu ilkin qiymətdir.'} Mənbə və hədəf hazırlığı tamamlandıqdan sonra yol xəritəsi təklif ediləcək.</p>
      : probe ? <form onSubmit={submit}>
        <Text value={probe.prompt} />
        {probe.criteria && <p className="muted">Cavabında göstər: {Array.isArray(probe.criteria) ? probe.criteria.join('; ') : probe.criteria}</p>}
        <label htmlFor="probe-response">Öz cavabın<textarea id="probe-response" rows={5} value={answer}
          onChange={e => setAnswer(e.target.value)} maxLength={8000} required disabled={busy} /></label>
        <label className="check"><input type="checkbox" checked={assisted} onChange={e => setAssisted(e.target.checked)} disabled={busy} />Bu cavabda izah, ipucu və ya hazır nümunədən istifadə etdim.</label>
        {assisted && <label>Aldığın kömək (qısa)<input value={help} onChange={e => setHelp(e.target.value)} maxLength={300} /></label>}
        <p className="muted">Əmin deyilsənsə, nəyi bilmədiyini yaz. Kömək almaq cəza deyil; nəticənin nəyi göstərdiyini düzgün qeyd edirik.</p>
        <button type="submit" className="primary" disabled={busy || !answer.trim()}>Cavabı göndər</button>
      </form> : <p role="status">Agent məqsədinə uyğun kiçik tapşırıq hazırlayır. Bu yoxlama ən çox iki müşahidə ilə ilkin planı yönləndirir; ümumi zəka və ya sabit öyrənmə üslubu ölçülmür.</p>}
    {intake.skipped && <p className="warn" role="status">Başlanğıc yoxlaması sənin razılığınla keçirildi. Səbəb: {intake.skip_reason}. Plan ilkin qalır; bacarıq sübutu yoxdur.</p>}
    <p className="muted small">Yoxlanılmış müşahidə: {intake.observations || 0}/{intake.observation_limit || 2}. İlkin qiymət qeyri-müəyyəndir və yeni cəhdlərlə yenilənir.</p>
    {!goal && !background && !clarification && !ready && !waiting && !probe && <details open={skipOpen} onToggle={e => setSkipOpen(e.currentTarget.open)}>
      <summary>Bu format mənə uyğun deyil</summary>
      <p>Əlçatanlıq ehtiyacını və ya uyğun tapşırığın niyə mümkün olmadığını qeyd et. Yoxlamasız plan ilkin qalacaq; bacarıq sübutu yaranmayacaq.</p>
      <form onSubmit={async e => { e.preventDefault(); await act('/api/course/diagnostic/skip', { reason: skipReason.trim() }); }}>
        <label>Səbəb<textarea value={skipReason} onChange={e => setSkipReason(e.target.value)} minLength={20} maxLength={1000} required /></label>
        <button type="submit" disabled={busy || skipReason.trim().length < 20}>İlkin planla davam etməyə razıyam</button>
      </form>
    </details>}
    {probe && !waiting && <p className="muted">Tapşırığın formatı əlçatan deyilsə, agentə bildir. Hazırkı tapşırıq razılaşdırılmadan səssizcə ötürülmür; uyğun başqa format istəyə bilərsən.</p>}
  </section>;
}
