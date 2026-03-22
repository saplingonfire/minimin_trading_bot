/**
 * Application entry point — wires up page mode, event listeners, and refresh loop.
 */

import { setStatus, setCardLoading } from './utils.js';
import { getAccount, fetchApi } from './api.js';
import { renderServerTime, renderPortfolio, renderPendingCount, renderOrders } from './renderers.js';
import { renderChart, initTimezoneSelect } from './chart.js';

function setPageMode() {
  const isLive = getAccount() === 'live';
  document.title = 'Roostoo Bot Dashboard (' + (isLive ? 'Live' : 'Test') + ')';
  const h1 = document.getElementById('page-title');
  if (h1) h1.textContent = 'Roostoo Bot Dashboard (' + (isLive ? 'Live' : 'Test') + ')';
  const btn = document.getElementById('switch-mode');
  if (btn) {
    btn.textContent = isLive ? 'Switch to Test' : 'Switch to Live';
    btn.onclick = () => {
      window.location.href = isLive ? '/dashboard/test/' : '/dashboard/live/';
    };
  }
}

async function refresh() {
  document.getElementById('refresh').disabled = true;
  setStatus('Refreshing…', null);
  setCardLoading('time', true);
  setCardLoading('balance', true);
  setCardLoading('pending', true);

  const [timeResult, balanceResult, tickerResult, pendingResult, ordersResult, configResult] = await Promise.allSettled([
    fetchApi('/server_time'),
    fetchApi('/balance'),
    fetchApi('/ticker'),
    fetchApi('/pending_count'),
    fetchApi('/orders?limit=200'),
    fetchApi('/config'),
  ]);

  const t = renderServerTime(timeResult);
  const b = renderPortfolio(balanceResult, tickerResult);
  const p = renderPendingCount(pendingResult);
  renderOrders(ordersResult);
  renderChart(balanceResult, tickerResult, ordersResult, configResult);

  const ok = t && b && p;
  setStatus(ok ? 'Connected' : 'Some data failed. Check credentials and API.', ok);
  document.getElementById('refresh').disabled = false;
}

setPageMode();
initTimezoneSelect();
document.getElementById('refresh').addEventListener('click', refresh);
refresh();
