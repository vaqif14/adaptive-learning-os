import React, { useMemo, useRef, useState } from 'react';
import { NODE_STATUS, resourceStatus } from './ui.jsx';

const COL = 236, ROW = 176, CARD_W = 208, CARD_H = 128;   // CARD_H must match .node height in styles.css

function layout(nodes) {
  const level = new Map(), rows = [];
  for (const n of nodes) {
    const l = (n.requires || []).reduce((m, r) => Math.max(m, (level.get(r) ?? 0) + 1), 0);
    level.set(n.id, l);
    (rows[l] ??= []).push(n);
  }
  const width = Math.max(320, ...rows.map(r => r.length * COL));
  const pos = new Map();
  rows.forEach((row, l) => row.forEach((n, i) => pos.set(n.id, { x: width * (i + 0.5) / row.length, y: l * ROW + 16 })));
  return { level, rows, width, height: rows.length * ROW + 24, pos };
}

export default function Roadmap({ nodes, selected, onSelect }) {
  const { level, rows, width, height, pos } = useMemo(() => layout(nodes), [nodes]);
  const refs = useRef({});
  const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
  const move = (e, n) => {
    const row = rows[level.get(n.id)], i = row.indexOf(n);
    const target = { ArrowRight: row[i + 1], ArrowLeft: row[i - 1], ArrowUp: byId[(n.requires || [])[0]],
                     ArrowDown: nodes.find(m => (m.requires || []).includes(n.id)) }[e.key];
    if (target) { e.preventDefault(); refs.current[target.id]?.focus(); }
  };
  return <nav className="roadmap" aria-label="Öyrənmə yol xəritəsi">
    <p className="map-hint" id="map-keys">Mərhələni açmaq üçün klik et və ya Enter bas. Ox düymələri: sağ/sol — eyni səviyyə, yuxarı — ilkin şərt, aşağı — növbəti.</p>
    <div className="roadmap-viewport" tabIndex={-1}>
      <div className="map-canvas" style={{ width, height }}>
        <svg className="map-edges" width={width} height={height} aria-hidden="true">
          {nodes.flatMap(n => (n.requires || []).map(r => {
            const a = pos.get(r), b = pos.get(n.id);
            if (!a || !b) return null;
            const y1 = a.y + CARD_H, mid = (y1 + b.y) / 2;
            return <path key={`${r}-${n.id}`} className={byId[r]?.status === 'passed' ? 'done' : ''}
                         d={`M ${a.x} ${y1} V ${mid} H ${b.x} V ${b.y}`} />;
          }))}
        </svg>
        <ol>{nodes.map((n, i) => {
          const p = pos.get(n.id);
          return <li key={n.id} style={{ left: p.x - CARD_W / 2, top: p.y }}>
            <button type="button" ref={el => { refs.current[n.id] = el; }} className={`node status-${n.status}`}
                    aria-pressed={selected === n.id} aria-describedby="map-keys"
                    onClick={() => onSelect(n.id)} onKeyDown={e => move(e, n)}>
              <span className="number">MƏRHƏLƏ {String(i + 1).padStart(2, '0')}</span>
              <strong>{n.title}</strong>
              <small><span className={`badge ${n.status}`}>{NODE_STATUS[n.status]}</span>
                {n.lesson?.status === 'ready' ? ' · dərs hazırdır' : ' · dərs hazır deyil'}</small>
              <small className="prereq">{(n.requires || []).length
                ? `Əvvəl: ${n.requires.map(r => byId[r]?.title || r).join(', ')}` : 'Başlanğıc mərhələ'}</small>
            </button>
          </li>;
        })}</ol>
      </div>
    </div>
  </nav>;
}

export function NodeDetail({ node, nodes, sources }) {
  if (!node) return <aside className="detail" aria-live="polite"><p>Təfərrüat üçün mərhələ seç.</p></aside>;
  const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
  const srcByKey = Object.fromEntries((sources || []).map(s => [s.key, s]));
  const list = (title, items) => items?.length ? <><h3>{title}</h3><ul>{items.map((x, i) => <li key={i}>{x}</li>)}</ul></> : null;
  return <aside className="detail" aria-live="polite" aria-label={`${node.title} təfərrüatı`}>
    <p className="eyebrow">{node.exam_domain || 'Mərhələ'}</p>
    <h2>{node.title}</h2>
    <p>{node.task}</p>
    {list('Bacarıqlar', node.skills)}
    {list('Nəticədə edə biləcəksən', node.outcomes)}
    {list('İlkin şərtlər', (node.requires || []).map(r => `${byId[r]?.title || r} — ${NODE_STATUS[byId[r]?.status] || ''}`))}
    {node.practice && <><h3>Gözlənilən praktika</h3><p>{node.practice}</p></>}
    {list('Mənbə əsası', (node.source_refs || []).map(k => srcByKey[k]
      ? `${srcByKey[k].title || srcByKey[k].url} — ${resourceStatus(srcByKey[k].status)}${srcByKey[k].stale ? ' (köhnəlmiş ola bilər)' : ''}`
      : `${k} — mənbə manifestində yoxdur`))}
    {list('Harada ilişirlər', node.pitfalls)}
    <p className="muted">Dərs: {node.lesson?.status === 'ready' ? 'hazırdır' : 'hələ hazırlanmayıb'} · Görülməmiş yoxlama tapşırığı: {node.fresh_items}</p>
  </aside>;
}

export function ApprovalPanel({ roadmap, onApprove, busy }) {
  const [reviewed, setReviewed] = useState(false);
  const [minutes, setMinutes] = useState(120);
  const [days, setDays] = useState(3);
  const [due, setDue] = useState('');
  const changes = roadmap.review_required?.changes || [];
  const label = c => ({ initial_roadmap: 'İlk təklif', node_added: `Yeni mərhələ: ${c.node}`, node_removed: `Silinən mərhələ: ${c.node}`,
                        node_changed: `Dəyişən mərhələ: ${c.node} (${(c.fields || []).join(', ')})`, goal_changed: 'Məqsəd dəyişib',
                        sources_changed: 'Mənbələr dəyişib', order_changed: 'Sıra dəyişib' }[c.change] || c.change);
  const submit = e => {
    e.preventDefault();
    const commitments = { weekly_active_minutes: Number(minutes), study_days_per_week: Number(days),
                          agreed_text: `Həftədə ${days} gün, ${minutes} dəqiqə aktiv iş` };
    if (due) commitments.checkpoints = [{ node_id: roadmap.nodes.find(n => !(n.requires || []).length)?.id, due }];
    onApprove(commitments);
  };
  return <form className="approval" onSubmit={submit} aria-labelledby="approval-title">
    <h2 id="approval-title">Yol xəritəsini təsdiq et</h2>
    <p>Kurs faylları və dərslər yalnız sənin təsdiqindən sonra yaradılır. Versiya: <code>{roadmap.version}</code></p>
    {roadmap.review_required?.reason === 'material_change' &&
      <p role="alert" className="warn">Yol xəritəsi əvvəl təsdiqlədiyin versiyadan fərqlənir. Dəyişiklikləri nəzərdən keçir.</p>}
    {changes.length > 0 && <ul className="changes">{changes.map((c, i) => <li key={i}>{label(c)}</li>)}</ul>}
    <fieldset><legend>Razılaşdığımız vaxt (sonra dəyişə bilərsən)</legend>
      <label>Həftədə neçə gün <input type="number" min="1" max="7" value={days} onChange={e => setDays(e.target.value)} required /></label>
      <label>Həftədə aktiv dəqiqə <input type="number" min="15" max="1500" step="15" value={minutes} onChange={e => setMinutes(e.target.value)} required /></label>
      <label>İlk yoxlama nöqtəsi (istəyə bağlı) <input type="date" value={due} onChange={e => setDue(e.target.value)} /></label>
    </fieldset>
    <label className="check"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} />
      Mərhələləri, ilkin şərtləri və mənbələri nəzərdən keçirdim.</label>
    <button type="submit" className="primary" disabled={!reviewed || busy}>Təsdiq edirəm</button>
  </form>;
}
