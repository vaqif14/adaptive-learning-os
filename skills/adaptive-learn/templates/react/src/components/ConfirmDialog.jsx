import React, { useEffect, useRef } from 'react';

// Modal confirm: focus starts on the primary action, Tab cycles inside, Escape cancels,
// the page behind is inert, and focus returns to the opener on cancel.
export default function ConfirmDialog({ onConfirm, onCancel, title = 'Müstəqil yoxlamaya başlayaq?',
  text = 'Görmədiyin yeni tapşırıq veriləcək. Cəhd bitənə qədər izah və ipucu bağlı olacaq; yarımçıq çıxsan, cəhd uğur kimi sayılmayacaq.',
  confirmLabel = 'Başla', cancelLabel = 'Hələ yox', safeFirst = false }) {
  const ref = useRef(null);
  useEffect(() => { ref.current?.querySelector(safeFirst ? 'button.safe' : 'button')?.focus(); }, [safeFirst]);
  const onKey = e => {
    if (e.key === 'Escape') { e.preventDefault(); onCancel(); return; }
    if (e.key !== 'Tab') return;
    const items = [...ref.current.querySelectorAll('button')];
    const i = items.indexOf(document.activeElement);
    if (e.shiftKey && i <= 0) { e.preventDefault(); items[items.length - 1].focus(); }
    else if (!e.shiftKey && i === items.length - 1) { e.preventDefault(); items[0].focus(); }
  };
  return <div className="modal" role="dialog" aria-modal="true" aria-labelledby="exam-confirm" aria-describedby="exam-confirm-text"
              onKeyDown={onKey} ref={ref}>
    <div className="card"><h2 id="exam-confirm">{title}</h2>
      <p id="exam-confirm-text">{text}</p>
      <button type="button" className={safeFirst ? '' : 'primary'} onClick={onConfirm}>{confirmLabel}</button>{' '}
      <button type="button" className={safeFirst ? 'primary safe' : ''} onClick={onCancel}>{cancelLabel}</button></div></div>;
}
