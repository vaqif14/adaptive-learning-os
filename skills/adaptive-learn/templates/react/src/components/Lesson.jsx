import React, { useState } from 'react';
export default function Lesson({ node, workspace }) {
  const [tab, setTab] = useState('lesson');
  const folder = `${workspace}/modules/${node.id}/submission`;
  return <section className="lesson"><span className="eyebrow">AKTİV MƏRHƏLƏ</span><h2>{node.title}</h2>
    <div className="tabs" aria-label="Məzmun görünüşü"><button type="button" aria-pressed={tab === 'lesson'} onClick={() => setTab('lesson')}>Dərs</button><button type="button" aria-pressed={tab === 'task'} onClick={() => setTab('task')}>Praktik tapşırıq</button></div>
    {tab === 'lesson' ? <div className="text">{node.lesson || 'Bu mərhələnin izahını agent məqsədə və ehtiyacına uyğun hazırlayacaq.'}</div> : <><div className="text">{node.task}</div><div className="ide"><h3>İşini yazacağın qovluq</h3><code>{folder}</code><p>Cavabını tapşırığın tələb etdiyi formada (kod, mətn, həll, qeyd) bu qovluğa yaz və chat-da “yoxla” de. Agent işini yoxlayacaq (kod olarsa icra edəcək), nəticəni və növbəti addımı bildirəcək.</p></div></>}
  </section>;
}
