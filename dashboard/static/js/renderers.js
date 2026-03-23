/**
 * DOM rendering functions for dashboard sections.
 * Each renderer receives Promise.allSettled result objects.
 */

import { fmtUsd, fmtPrice, fmtQty, timeAgo, statusBadge, setCardLoading, setCardError } from './utils.js';
import { getTickerRow, extractOrders } from './api.js';

export function renderServerTime(result) {
  setCardLoading('time', false);
  if (result.status === 'rejected') {
    setCardError('time', result.reason);
    document.getElementById('server-time').textContent = 'Error';
    return false;
  }
  setCardError('time', null);
  const data = result.value;
  const ts = data.ServerTime ?? data.server_time ?? data.time;
  document.getElementById('server-time').textContent = ts != null ? new Date(ts).toLocaleString() : '—';
  return true;
}

export function renderPortfolio(balanceResult, tickerResult) {
  setCardLoading('balance', false);
  if (balanceResult.status === 'rejected' || tickerResult.status === 'rejected') {
    const err = balanceResult.reason || tickerResult.reason;
    setCardError('balance', err);
    document.getElementById('portfolio-value').textContent = 'Error';
    document.getElementById('balance').textContent = '—';
    document.getElementById('positions-body').innerHTML = '';
    return false;
  }
  setCardError('balance', null);
  const balanceData = balanceResult.value;
  const tickerData = tickerResult.value;

  const spot = balanceData.SpotWallet && typeof balanceData.SpotWallet === 'object' ? balanceData.SpotWallet : null;
  const margin = balanceData.MarginWallet && typeof balanceData.MarginWallet === 'object' ? balanceData.MarginWallet : null;
  const legacy = balanceData.Wallet ?? balanceData.wallet ?? balanceData.CurrBal;
  const wallet = (typeof legacy === 'object' && legacy !== null) ? legacy : (spot ?? margin ?? balanceData);
  if (typeof wallet !== 'object' || wallet === null) {
    document.getElementById('portfolio-value').textContent = '—';
    document.getElementById('balance').textContent = '—';
    document.getElementById('positions-body').innerHTML = '<tr><td colspan="6" class="empty">No data</td></tr>';
    return true;
  }
  const combined = spot && margin ? { ...spot, ...margin } : wallet;
  const entries = Object.entries(combined).filter(([, v]) => v && typeof v === 'object');

  let cashUsd = 0;
  let portfolioValue = 0;
  const positions = [];

  for (const [asset, v] of entries) {
    const free = Number(v.Free ?? v.free ?? 0) || 0;
    const lock = Number(v.Lock ?? v.lock ?? 0) || 0;
    const total = free + lock;
    if (total === 0) continue;

    if (asset === 'USD' || asset === 'USDT') {
      cashUsd += total;
      portfolioValue += total;
      positions.push({ asset, total, free, lock, price: 1, value: total, change: null, isCash: true });
    } else {
      const pair = asset + '/USD';
      const row = getTickerRow(tickerData, pair);
      const price = row ? (Number(row.LastPrice ?? row.lastPrice ?? row.Last) || 0) : 0;
      const change = row ? (Number(row.Change ?? row.change) || null) : null;
      const value = price > 0 ? total * price : 0;
      portfolioValue += value;
      positions.push({ asset, total, free, lock, price, value, change, isCash: false });
    }
  }

  positions.sort((a, b) => b.value - a.value);

  document.getElementById('portfolio-value').textContent = portfolioValue > 0 ? '$' + fmtUsd(portfolioValue) : '—';
  document.getElementById('balance').textContent = cashUsd > 0 ? '$' + fmtUsd(cashUsd) : '—';

  const tbody = document.getElementById('positions-body');
  if (positions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">No positions</td></tr>';
  } else {
    tbody.innerHTML = positions.map(p => {
      const pct = portfolioValue > 0 ? (p.value / portfolioValue * 100) : 0;
      const barWidth = Math.min(pct, 100);
      const priceStr = p.isCash ? '—' : (p.price > 0 ? '$' + fmtUsd(p.price) : '—');
      const valueStr = p.value > 0 ? '$' + fmtUsd(p.value) : '—';
      let changeStr = '—';
      if (p.change != null && !isNaN(p.change)) {
        const sign = p.change >= 0 ? '+' : '';
        const color = p.change >= 0 ? 'var(--success)' : 'var(--error)';
        changeStr = `<span style="color: ${color}">${sign}${p.change.toFixed(2)}%</span>`;
      }
      const pctStr = `<span class="pct-bar" style="width: ${barWidth}%"></span>${pct.toFixed(1)}%`;
      const qtyTitle = p.lock > 0 ? `Free: ${fmtQty(p.free)}, Locked: ${fmtQty(p.lock)}` : '';
      return `<tr>
        <td class="asset-name">${p.asset}</td>
        <td class="num" title="${qtyTitle}">${p.isCash ? fmtUsd(p.total) : fmtQty(p.total)}</td>
        <td class="num">${priceStr}</td>
        <td class="num">${valueStr}</td>
        <td class="num">${changeStr}</td>
        <td class="num pct-col">${pctStr}</td>
      </tr>`;
    }).join('');
  }

  const dataPayload = tickerData.Data && typeof tickerData.Data === 'object' ? tickerData.Data : tickerData;
  const btcTicker = dataPayload['BTC/USD'] ?? dataPayload.BTCUSD ?? tickerData['BTC/USD'] ?? tickerData.BTCUSD ?? tickerData;
  const btcPrice = btcTicker?.LastPrice ?? btcTicker?.lastPrice ?? btcTicker?.Last;
  const btcChange = btcTicker?.Change ?? btcTicker?.change;
  let tickerHtml = btcPrice != null ? 'USD ' + Number(btcPrice).toLocaleString(undefined, { minimumFractionDigits: 2 }) : '—';
  if (btcChange != null && !isNaN(btcChange)) tickerHtml += ` <span style="color: var(--muted);">(${Number(btcChange) >= 0 ? '+' : ''}${Number(btcChange).toFixed(2)}%)</span>`;
  document.getElementById('ticker').innerHTML = tickerHtml;
  document.getElementById('ticker-err').hidden = true;
  return true;
}

export function renderPendingCount(result) {
  setCardLoading('pending', false);
  if (result.status === 'rejected') {
    setCardError('pending', result.reason);
    document.getElementById('pending').textContent = 'Error';
    return false;
  }
  setCardError('pending', null);
  const data = result.value;
  const n = data.PendingCount ?? data.pending_count ?? data.TotalPending ?? data.total_pending ?? data.count ?? data;
  document.getElementById('pending').textContent = typeof n === 'number' ? n : (n != null ? String(n) : '—');
  return true;
}

function computeOrderPnL(orders) {
  const filled = orders
    .map((o, i) => ({ o, i }))
    .filter(({ o }) => {
      const s = (o.Status ?? o.status ?? '').toLowerCase();
      return s === 'filled' || s === 'completed';
    });
  filled.sort((a, b) => {
    const ta = a.o.CreateTimestamp ?? a.o.createTimestamp ?? 0;
    const tb = b.o.CreateTimestamp ?? b.o.createTimestamp ?? 0;
    return ta - tb;
  });

  const basis = {};
  const pnlMap = new Map();

  for (const { o, i } of filled) {
    const pair = o.Pair ?? o.pair ?? '';
    const base = pair.split('/')[0];
    const side = (o.Side ?? o.side ?? '').toUpperCase();
    const qty = Number(o.FilledQuantity ?? o.filledQuantity ?? 0);
    const price = Number(o.FilledAverPrice ?? o.filledAverPrice ?? 0);
    if (!base || qty <= 0 || price <= 0) continue;

    if (side === 'BUY') {
      const b = basis[base] ?? { cost: 0, qty: 0 };
      b.cost += qty * price;
      b.qty += qty;
      basis[base] = b;
    } else if (side === 'SELL') {
      const b = basis[base] ?? { cost: 0, qty: 0 };
      const avgCost = b.qty > 0 ? b.cost / b.qty : 0;
      const pnl = qty * (price - avgCost);
      pnlMap.set(i, pnl);
      if (b.qty > 0) {
        const costAlloc = avgCost * qty;
        b.cost = Math.max(0, b.cost - costAlloc);
        b.qty = Math.max(0, b.qty - qty);
      }
      basis[base] = b;
    }
  }
  return pnlMap;
}

export function renderOrders(result) {
  const errEl = document.getElementById('orders-err');
  errEl.hidden = true;
  if (result.status === 'rejected') {
    document.getElementById('orders-body').innerHTML = '';
    errEl.hidden = false;
    errEl.textContent = result.reason?.message || String(result.reason);
    return false;
  }
  const rows = extractOrders(result.value);
  const tbody = document.getElementById('orders-body');
  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty">No orders</td></tr>';
    return true;
  }
  const pnlMap = computeOrderPnL(rows);
  tbody.innerHTML = rows.map((o, idx) => {
    const pair = o.Pair ?? o.pair ?? '—';
    const side = o.Side ?? o.side ?? '—';
    const sideClass = side.toLowerCase() === 'buy' ? 'side-buy' : side.toLowerCase() === 'sell' ? 'side-sell' : '';
    const type = o.Type ?? o.type ?? '—';
    const reqQty = Number(o.Quantity ?? o.quantity ?? 0);
    const filledQty = Number(o.FilledQuantity ?? o.filledQuantity ?? 0);
    const fillPct = reqQty > 0 ? Math.min(filledQty / reqQty * 100, 100) : 0;
    const avgPrice = Number(o.FilledAverPrice ?? o.filledAverPrice ?? o.Price ?? o.price ?? 0);
    const value = filledQty > 0 && avgPrice > 0 ? filledQty * avgPrice : 0;
    const status = o.Status ?? o.status ?? '—';
    const ts = o.CreateTimestamp ?? o.createTimestamp ?? o.CreateTime ?? o.createTime;
    const timeStr = ts != null ? new Date(ts).toLocaleString() : '—';
    const relTime = ts != null ? timeAgo(ts) : '';

    const reqQtyStr = reqQty > 0 ? fmtQty(reqQty) : '—';
    const filledStr = filledQty > 0
      ? `<span class="fill-bar-wrap">${fmtQty(filledQty)}<span class="fill-bar"><span class="fill-bar-inner" style="width:${fillPct}%"></span></span></span>`
      : '—';
    const avgPriceStr = avgPrice > 0 ? '$' + fmtPrice(avgPrice) : '—';
    const valueStr = value > 0 ? '$' + fmtUsd(value) : '—';
    const timeCell = relTime ? `<span title="${timeStr}">${relTime}</span>` : timeStr;

    let pnlStr = '—';
    if (pnlMap.has(idx)) {
      const pnl = pnlMap.get(idx);
      const sign = pnl >= 0 ? '+' : '';
      const color = pnl >= 0 ? 'var(--success)' : 'var(--error)';
      pnlStr = `<span style="color: ${color}">${sign}$${fmtUsd(Math.abs(pnl))}</span>`;
    }

    return `<tr>
      <td>${pair}</td>
      <td class="${sideClass}">${side}</td>
      <td>${type}</td>
      <td class="num">${reqQtyStr}</td>
      <td class="num">${filledStr}</td>
      <td class="num">${avgPriceStr}</td>
      <td class="num">${valueStr}</td>
      <td class="num">${pnlStr}</td>
      <td>${statusBadge(status)}</td>
      <td class="num">${timeCell}</td>
    </tr>`;
  }).join('');
  return true;
}
