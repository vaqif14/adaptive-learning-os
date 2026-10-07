import React, { useState } from 'react';
import plan from './data/roadmap.json';
import Roadmap from './components/Roadmap.jsx';
import Lesson from './components/Lesson.jsx';

export default function App() {
  const [selected, setSelected] = useState(null);
  const stages = [...new Set(plan.nodes.map(item => item.stage).filter(Boolean))];
  const node = plan.nodes.find(item => item.id === selected);
  const returnToRoadmap = () => setSelected(null);
  return <div className="app">
    <header><button type="button" className="brand" onClick={returnToRoadmap}>◈ Adaptive Learning</button><span>Yol xəritəsi → Dərs → Praktika → Rəy</span></header>
    <main><section className="intro"><span className="eyebrow">SƏNİN ÖYRƏNMƏ YOLUN</span>
      <h1>{plan.topic}</h1><p>{plan.goal || 'Məqsəd hələ dəqiqləşdirilməyib'}</p>
      {plan.status === 'needs_curriculum_research' && <p role="status">İlk tapşırıq və mənbələr agent tərəfindən hazırlanmalıdır.</p>}
    </section>
    {node ? <div className="lesson-view"><button className="back" type="button" onClick={returnToRoadmap}>← Yol xəritəsinə qayıt</button><Lesson key={node.id} node={node} workspace={plan.workspace_directory}/></div>
      : <><div className="map-heading"><div><h2>Öyrənmə yol xəritəsi</h2><p>Əvvəl yolu gör. Sonra bir mərhələni seçib dərsi aç.</p></div><span className="map-count">{plan.nodes.length} mərhələ</span></div><div className="map-outline" aria-label="Yol xəritəsinin istiqamətləri">{stages.map(stage => <span key={stage}>{stage}</span>)}</div><Roadmap nodes={plan.nodes} onSelect={setSelected}/></>}
    {plan.notebooklm?.notebook && <section className="sources"><h2>Seçilən NotebookLM</h2><a href={plan.notebooklm.notebook.url} target="_blank" rel="noreferrer">{plan.notebooklm.notebook.title || 'Adsız notebook'}</a></section>}
    <section className="sources"><h2>Mənbələr</h2>{plan.sources.length ? plan.sources.map(url => <a key={url} href={url} target="_blank" rel="noreferrer">{url}</a>) : <p>Mənbə araşdırması gözlənilir.</p>}</section>
    </main><footer>Xətlər əvvəl öyrəniləcək mərhələləri göstərir. Proqres agentin yoxlamasından sonra session ledger-də saxlanır.</footer>
  </div>;
}
