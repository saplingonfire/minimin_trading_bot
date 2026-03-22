/**
 * Portfolio value time-series chart with timezone picker.
 * Uses Lightweight Charts library (loaded globally via <script> tag).
 */

import { fmtUsd } from './utils.js';
import { parseWallet, computePV, getTickerRow, extractOrders } from './api.js';

let pvChart = null;
let pvAreaSeries = null;
let pvBaselineLine = null;
let lastChartData = null;

const TIMEZONE_OPTIONS = [
  { label: 'UTC',            value: 'UTC',              offsetMin: 0 },
  { label: 'EST (UTC-5)',    value: 'America/New_York', offsetMin: -300 },
  { label: 'CST (UTC-6)',    value: 'America/Chicago',  offsetMin: -360 },
  { label: 'MST (UTC-7)',    value: 'America/Denver',   offsetMin: -420 },
  { label: 'PST (UTC-8)',    value: 'America/Los_Angeles', offsetMin: -480 },
  { label: 'GMT (UTC+0)',    value: 'Europe/London',    offsetMin: 0 },
  { label: 'CET (UTC+1)',    value: 'Europe/Berlin',    offsetMin: 60 },
  { label: 'IST (UTC+5:30)', value: 'Asia/Kolkata',     offsetMin: 330 },
  { label: 'CST (UTC+8)',    value: 'Asia/Shanghai',    offsetMin: 480 },
  { label: 'JST (UTC+9)',    value: 'Asia/Tokyo',       offsetMin: 540 },
  { label: 'AEST (UTC+10)',  value: 'Australia/Sydney',  offsetMin: 600 },
];

function detectLocalTimezone() {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const match = TIMEZONE_OPTIONS.find(o => o.value === tz);
    if (match) return match.value;
  } catch { /* fallback */ }
  const localOffsetMin = -new Date().getTimezoneOffset();
  const closest = TIMEZONE_OPTIONS.reduce((best, o) =>
    Math.abs(o.offsetMin - localOffsetMin) < Math.abs(best.offsetMin - localOffsetMin) ? o : best
  );
  return closest.value;
}

function getTimezoneOffsetSec(tzValue) {
  const entry = TIMEZONE_OPTIONS.find(o => o.value === tzValue);
  return entry ? entry.offsetMin * 60 : 0;
}

export function getSelectedTimezone() {
  const sel = document.getElementById('tz-select');
  return sel ? sel.value : 'UTC';
}

export function initTimezoneSelect() {
  const sel = document.getElementById('tz-select');
  if (!sel) return;
  sel.innerHTML = TIMEZONE_OPTIONS.map(o =>
    `<option value="${o.value}">${o.label}</option>`
  ).join('');
  sel.value = detectLocalTimezone();
  sel.addEventListener('change', () => {
    if (lastChartData) {
      applyChartData(lastChartData.points, lastChartData.initialBalance);
    }
  });
}

function shiftPointsForTimezone(points) {
  const offsetSec = getTimezoneOffsetSec(getSelectedTimezone());
  return points.map(p => ({
    ...p,
    time: p.time + offsetSec,
  }));
}

function reconstructPortfolioHistory(balanceData, tickerData, ordersData, initialBalance) {
  const holdings = parseWallet(balanceData);
  if (Object.keys(holdings).length === 0) return [];

  const currentPV = computePV(holdings, tickerData);
  const nowSec = Math.floor(Date.now() / 1000);
  const points = [{ time: nowSec, value: currentPV }];

  const allOrders = extractOrders(ordersData);
  const filled = allOrders.filter(o => {
    const s = (o.Status ?? o.status ?? '').toLowerCase();
    return s === 'filled' || s === 'completed';
  });
  filled.sort((a, b) => {
    const ta = a.CreateTimestamp ?? a.createTimestamp ?? a.CreateTime ?? a.createTime ?? 0;
    const tb = b.CreateTimestamp ?? b.createTimestamp ?? b.CreateTime ?? b.createTime ?? 0;
    return tb - ta;
  });

  for (const o of filled) {
    const pair = o.Pair ?? o.pair ?? '';
    const side = (o.Side ?? o.side ?? '').toUpperCase();
    const ts = o.CreateTimestamp ?? o.createTimestamp ?? o.CreateTime ?? o.createTime;
    if (!pair || !side || ts == null) continue;

    const parts = pair.includes('/') ? pair.split('/') : [pair, 'USD'];
    const base = parts[0];
    const quote = parts[1] || 'USD';

    const coinChange = Number(o.CoinChange ?? o.coinChange ?? 0);
    const unitChange = Number(o.UnitChange ?? o.unitChange ?? 0);
    const filledQty = Number(o.FilledQuantity ?? o.filledQuantity ?? 0);
    const avgPrice = Number(o.FilledAverPrice ?? o.filledAverPrice ?? 0);

    if (coinChange !== 0 && unitChange !== 0) {
      if (side === 'BUY') {
        holdings[base] = (holdings[base] || 0) - coinChange;
        holdings[quote] = (holdings[quote] || 0) + unitChange;
      } else {
        holdings[base] = (holdings[base] || 0) + coinChange;
        holdings[quote] = (holdings[quote] || 0) - unitChange;
      }
    } else if (filledQty > 0 && avgPrice > 0) {
      if (side === 'BUY') {
        holdings[base] = (holdings[base] || 0) - filledQty;
        holdings[quote] = (holdings[quote] || 0) + filledQty * avgPrice;
      } else {
        holdings[base] = (holdings[base] || 0) + filledQty;
        holdings[quote] = (holdings[quote] || 0) - filledQty * avgPrice;
      }
    } else {
      continue;
    }

    const overrides = avgPrice > 0 ? { [base]: avgPrice } : undefined;
    const pv = computePV(holdings, tickerData, overrides);
    const timeSec = Math.floor(ts / 1000);
    points.push({ time: timeSec, value: pv });
  }

  points.reverse();

  if (initialBalance > 0 && points.length > 0) {
    const earliestSec = points[0].time - 1;
    points.unshift({ time: earliestSec, value: initialBalance });
  }

  const seen = new Set();
  const deduped = [];
  for (const p of points) {
    if (!seen.has(p.time)) {
      seen.add(p.time);
      deduped.push(p);
    }
  }

  const WHITESPACE_INTERVAL = 300;
  const filledOut = [];
  for (let i = 0; i < deduped.length; i++) {
    filledOut.push(deduped[i]);
    if (i < deduped.length - 1) {
      const gap = deduped[i + 1].time - deduped[i].time;
      if (gap > WHITESPACE_INTERVAL * 2) {
        const steps = Math.floor(gap / WHITESPACE_INTERVAL);
        for (let s = 1; s < steps; s++) {
          const wt = deduped[i].time + s * WHITESPACE_INTERVAL;
          if (!seen.has(wt)) {
            seen.add(wt);
            filledOut.push({ time: wt });
          }
        }
      }
    }
  }

  return filledOut;
}

function ensureChart(chartEl) {
  if (pvChart) return;
  pvChart = LightweightCharts.createChart(chartEl, {
    layout: {
      background: { type: 'solid', color: '#1a2332' },
      textColor: '#8b949e',
    },
    grid: {
      vertLines: { color: '#2d3a4d' },
      horzLines: { color: '#2d3a4d' },
    },
    timeScale: {
      timeVisible: true,
      secondsVisible: false,
      borderColor: '#2d3a4d',
    },
    rightPriceScale: {
      borderColor: '#2d3a4d',
    },
    crosshair: {
      horzLine: { color: '#58a6ff', style: 2 },
      vertLine: { color: '#58a6ff', style: 2 },
    },
  });
  pvAreaSeries = pvChart.addAreaSeries({
    topColor: 'rgba(88, 166, 255, 0.4)',
    bottomColor: 'rgba(88, 166, 255, 0.05)',
    lineColor: '#58a6ff',
    lineWidth: 2,
    priceFormat: { type: 'custom', formatter: (p) => '$' + p.toFixed(2) },
  });
  pvBaselineLine = pvChart.addLineSeries({
    color: '#8b949e',
    lineWidth: 1,
    lineStyle: 2,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  });
  const ro = new ResizeObserver(() => {
    pvChart.applyOptions({ width: chartEl.clientWidth });
  });
  ro.observe(chartEl);
}

function applyChartData(points, initialBalance) {
  const chartEl = document.getElementById('pv-chart');
  ensureChart(chartEl);

  const shifted = shiftPointsForTimezone(points);
  pvAreaSeries.setData(shifted);

  if (initialBalance > 0 && shifted.length >= 2) {
    pvBaselineLine.setData([
      { time: shifted[0].time, value: initialBalance },
      { time: shifted[shifted.length - 1].time, value: initialBalance },
    ]);
  } else {
    pvBaselineLine.setData([]);
  }

  pvChart.timeScale().fitContent();
}

export function renderChart(balanceResult, tickerResult, ordersResult, configResult) {
  const chartEl = document.getElementById('pv-chart');
  const emptyEl = document.getElementById('pv-chart-empty');
  const errEl = document.getElementById('pv-chart-err');
  const pnlEl = document.getElementById('pv-pnl');
  errEl.hidden = true;
  if (pnlEl) pnlEl.textContent = '';

  if (balanceResult.status === 'rejected' || tickerResult.status === 'rejected' || ordersResult.status === 'rejected') {
    const err = balanceResult.reason || tickerResult.reason || ordersResult.reason;
    errEl.hidden = false;
    errEl.textContent = err?.message || String(err);
    emptyEl.hidden = true;
    if (pvChart) chartEl.style.display = 'none';
    return;
  }

  const initialBalance = (configResult && configResult.status === 'fulfilled' && configResult.value)
    ? Number(configResult.value.initial_balance || 0)
    : 0;

  const points = reconstructPortfolioHistory(balanceResult.value, tickerResult.value, ordersResult.value, initialBalance);

  if (points.length < 2) {
    emptyEl.hidden = false;
    chartEl.style.display = 'none';
    return;
  }

  emptyEl.hidden = true;
  chartEl.style.display = '';

  if (pnlEl && initialBalance > 0) {
    const currentPV = points[points.length - 1].value;
    const pnlUsd = currentPV - initialBalance;
    const pnlPct = (pnlUsd / initialBalance) * 100;
    const sign = pnlUsd >= 0 ? '+' : '';
    const color = pnlUsd >= 0 ? 'var(--success)' : 'var(--error)';
    pnlEl.innerHTML = `<span style="color: ${color}; font-size: 0.875rem;">${sign}$${fmtUsd(Math.abs(pnlUsd))} (${sign}${pnlPct.toFixed(2)}%)</span>`;
  }

  lastChartData = { points, initialBalance };
  applyChartData(points, initialBalance);
}
