/**
 * Shared formatting and UI utility functions.
 */

export function fmtUsd(n) {
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function fmtPrice(n) {
  if (n === 0) return '0';
  if (n >= 1) return Number(n.toPrecision(6)).toLocaleString(undefined, { maximumFractionDigits: 6 });
  const s = n.toPrecision(6);
  return Number(s).toLocaleString(undefined, { maximumSignificantDigits: 6 });
}

export function fmtQty(n) {
  if (n >= 1000) return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n >= 1) return n.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 4 });
  return n.toLocaleString(undefined, { minimumFractionDigits: 6, maximumFractionDigits: 6 });
}

export function timeAgo(ms) {
  const sec = Math.floor((Date.now() - ms) / 1000);
  if (sec < 60) return sec + 's ago';
  const min = Math.floor(sec / 60);
  if (min < 60) return min + 'm ago';
  const hr = Math.floor(min / 60);
  if (hr < 24) return hr + 'h ago';
  const d = Math.floor(hr / 24);
  return d + 'd ago';
}

export function statusBadge(status) {
  const s = (status || '').toLowerCase();
  let cls = 'status-other';
  if (s === 'filled' || s === 'completed') cls = 'status-filled';
  else if (s === 'pending' || s === 'new' || s === 'partially_filled') cls = 'status-pending';
  else if (s === 'cancelled' || s === 'canceled' || s === 'rejected') cls = 'status-cancelled';
  return `<span class="status-badge ${cls}">${status}</span>`;
}

export function setStatus(text, ok) {
  const el = document.getElementById('status');
  el.textContent = text;
  el.className = 'status ' + (ok === true ? 'ok' : ok === false ? 'err' : '');
}

export function setCardLoading(cardId, loading) {
  const card = document.getElementById('card-' + cardId);
  if (card) card.classList.toggle('loading', !!loading);
}

export function setCardError(cardId, err) {
  const card = document.getElementById('card-' + cardId);
  const errEl = document.getElementById(cardId + '-err');
  if (card) card.classList.toggle('error', !!err);
  if (errEl) {
    errEl.hidden = !err;
    errEl.textContent = err ? (err.message || String(err)) : '';
  }
}
