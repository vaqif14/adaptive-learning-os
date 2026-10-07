import React from 'react';

export default function Roadmap({ nodes, onSelect }) {
  const levels = new Map();
  const rows = [];
  for (const node of nodes) {
    const level = node.requires.length ? Math.max(...node.requires.map(id => levels.get(id) ?? 0)) + 1 : 0;
    levels.set(node.id, level);
    (rows[level] ??= []).push(node);
  }
  const width = Math.max(320, ...rows.map(row => row.length * 210));
  const height = rows.length * 142 + 32;
  const positions = new Map();
  rows.forEach((row, level) => row.forEach((node, index) => positions.set(node.id, {
    x: width * (index + 0.5) / row.length, y: level * 142 + 24,
  })));
  return <nav className="roadmap" aria-label="Öyrənmə yol xəritəsi">
    <div className="map-legend"><span className="legend-square"/>Öyrənmə mərhələsi<span className="legend-line"/>İlkin şərt əlaqəsi</div>
    <div className="roadmap-viewport" ref={element => { if (element) element.scrollLeft = Math.max(0, (element.scrollWidth - element.clientWidth) / 2); }} tabIndex={0} aria-label="Əlaqəli mərhələlər diaqramı; geniş xəritəni yana sürüşdürə bilərsiniz">
      <div className="map-canvas" style={{ width, height }}>
        <svg className="map-edges" width={width} height={height} aria-hidden="true">
          {nodes.flatMap(node => node.requires.map(id => {
            const from = positions.get(id), to = positions.get(node.id);
            if (!from) return null;
            const start = from.y + 104, end = to.y;
            const middle = (start + end) / 2;
            return <path key={`${id}-${node.id}`} d={`M ${from.x} ${start} V ${middle} H ${to.x} V ${end}`} />;
          }))}
        </svg>
        <ol>{nodes.map((node, index) => {
          const position = positions.get(node.id);
          return <li key={node.id} style={{ left: position.x - 95, top: position.y }}>
            <button type="button" className="node" onClick={() => onSelect(node.id)} aria-label={`${node.title} — dərsi aç`}>
              <span className="number">MƏRHƏLƏ {String(index + 1).padStart(2, '0')}</span><strong>{node.title}</strong><small>{node.stage || "Dərsi aç ↗"}</small>
            </button>
            <span className="sr-only">Əvvəl: {node.requires.join(', ') || 'başlanğıc'}. Hələ qiymətləndirilməyib.</span>
          </li>;
        })}</ol>
      </div>
    </div>
    <p className="map-hint">Eyni səviyyədəki mərhələlər ayrı istiqamətlərdir. Xəritə tamamlanma göstəricisi deyil.</p>
  </nav>;
}
