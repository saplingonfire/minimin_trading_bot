/**
 * API communication helpers and data-parsing utilities.
 */

const API = '/api';

export function getAccount() {
  return window.location.pathname.includes('/live') ? 'live' : 'test';
}

export async function fetchApi(path) {
  const sep = path.indexOf('?') >= 0 ? '&' : '?';
  const url = API + path + (path.includes('account=') ? '' : sep + 'account=' + encodeURIComponent(getAccount()));
  const r = await fetch(url);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    const msg = (d && typeof d === 'object' && d.message != null) ? d.message : (typeof d === 'string' ? d : null);
    throw new Error(msg || r.statusText || 'Request failed');
  }
  return data;
}

export function getTickerRow(tickerData, pair) {
  const payload = tickerData.Data && typeof tickerData.Data === 'object' ? tickerData.Data : tickerData;
  return payload[pair] ?? payload[pair?.replace('/', '')] ?? null;
}

export function parseWallet(balanceData) {
  const spot = balanceData.SpotWallet && typeof balanceData.SpotWallet === 'object' ? balanceData.SpotWallet : null;
  const margin = balanceData.MarginWallet && typeof balanceData.MarginWallet === 'object' ? balanceData.MarginWallet : null;
  const legacy = balanceData.Wallet ?? balanceData.wallet ?? balanceData.CurrBal;
  const wallet = (typeof legacy === 'object' && legacy !== null) ? legacy : (spot ?? margin ?? balanceData);
  if (typeof wallet !== 'object' || wallet === null) return {};
  const combined = spot && margin ? { ...spot, ...margin } : wallet;
  const result = {};
  for (const [asset, v] of Object.entries(combined)) {
    if (!v || typeof v !== 'object') continue;
    const total = (Number(v.Free ?? v.free ?? 0) || 0) + (Number(v.Lock ?? v.lock ?? 0) || 0);
    if (total !== 0) result[asset] = total;
  }
  return result;
}

export function computePV(holdings, tickerData, priceOverrides) {
  let pv = 0;
  for (const [asset, qty] of Object.entries(holdings)) {
    if (asset === 'USD' || asset === 'USDT') { pv += qty; continue; }
    const override = priceOverrides && priceOverrides[asset];
    if (override > 0) { pv += qty * override; continue; }
    const row = getTickerRow(tickerData, asset + '/USD');
    const price = row ? (Number(row.LastPrice ?? row.lastPrice ?? row.Last) || 0) : 0;
    if (price > 0) pv += qty * price;
  }
  return pv;
}

export function extractOrders(data) {
  const list = data.OrderMatched ?? data.Orders ?? data.orders ?? data.Data ?? data.List ?? (Array.isArray(data) ? data : []);
  return Array.isArray(list) ? list : [];
}
