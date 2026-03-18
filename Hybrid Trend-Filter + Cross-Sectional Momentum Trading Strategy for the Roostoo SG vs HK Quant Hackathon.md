# Hybrid Trend-Filter + Cross-Sectional Momentum Trading Strategy
## Roostoo SG vs HK University Quant Hackathon — Full Design & Implementation Plan

***

## Executive Summary

This document is a complete, production-ready strategy design and implementation plan for the **SG vs HK University Web3 Quant Trading Hackathon**, run on the Roostoo Mock Exchange. The strategy is a two-layer hybrid:[^1]

1. **Market-regime filter** — a BTC-based 20-day moving average determines whether to be exposed to crypto risk at all.
2. **Cross-sectional momentum selection** — during risk-on periods only, coins are ranked by recent return, and capital is allocated to the top-N names via inverse-volatility weighting.

The competition runs live from **21 March – 14 April 2026**, with a Grand Finale on 17–21 April. Each team starts with a **USD 50,000 mock wallet** and is subject to explicit rate-limit enforcement that targets directional/discretionary strategies with approximately one trade per minute. HFT strategies are explicitly not supported. The architecture runs on an AWS EC2 instance provided as part of the competition.[^2][^3][^1]

***

## 1. Objective and Design Principles

### 1.1 Primary Objective

The primary objective is to **maximize risk-adjusted return** — measured by the Sharpe ratio and maximum-drawdown-controlled equity curve — over the ~25-day live competition window, while remaining competitive on absolute PnL as shown on the leaderboard. A strategy that grows capital by 30% with a 15% maximum drawdown is preferable to one that grows 50% with a 45% drawdown, because leaderboard ranking in Roostoo competitions is based on final portfolio value (CurrBal) relative to initial balance.[^4]

### 1.2 Core Design Principles

- **Robustness over fit**: Use simple, academically validated signals (price MA, cross-sectional momentum) with a wide range of parameter stability, not optimized curves.
- **Rate-limit compatibility**: All signal computation and execution is designed around ≤1 trade per 60 seconds. Each rebalancing cycle touches at most 3–5 orders.[^1]
- **Explainability**: Every decision maps to a transparent rule. This matters for the judge's deck, which must cover trading idea, strategy logic, risk management, and live results.[^1]
- **Resilience to noise**: Crypto is highly volatile. The two-layer structure — first filter regime, then rank within regime — naturally reduces the number of false signals compared to either layer alone.
- **Long-only, no leverage**: Consistent with Roostoo competition structure (mock USD account, no shorting mechanism in the REST API).[^2]

### 1.3 Why This Hybrid Structure Works for This Competition

Academic research confirms that **cross-sectional momentum in cryptocurrencies is statistically significant**: assets in the top 30-day return quintile tend to outperform over the subsequent 7-day period. However, raw momentum strategies suffer large drawdowns during crypto bear phases. Adding a **time-series trend filter** (BTC price vs moving average) as a first-stage gate prevents deployment of capital when the broad market is in decline.[^5][^6]

The combination is well-suited to a 25-day live window because:
- The regime filter is computed infrequently (daily or every 4 hours), keeping API calls well below rate limits.
- Cross-sectional rankings are stable over 15–60 minute windows, so rebalancing can be spaced out easily.
- The logic is explainable in two sentences to any judge.

***

## 2. Market-Regime Filter: When to Be in the Market

### 2.1 BTC Trend Filter Construction

Since Roostoo's `/v3/ticker` endpoint provides only the latest price and a 24-hour change field, **historical OHLCV candles are not natively available** from the Roostoo API. The bot must maintain its own rolling price log by polling the ticker periodically. For the first 20 days of warmup, historical BTC prices can be fetched from a public API (e.g., Binance REST `/api/v3/klines`) to initialize the moving average.[^2]

Define:

\[
P_t = \text{LastPrice}_{\text{BTC/USD}} \text{ at time } t
\]

\[
MA_{20}(t) = \frac{1}{20} \sum_{i=0}^{19} P_{t-i}
\]

where each \( P_{t-i} \) is the daily close (or the last sampled price in a 24-hour bar).

**Regime rule:**

\[
\text{regime}(t) = \begin{cases} \text{risk-on} & \text{if } P_t > MA_{20}(t) \\ \text{risk-off} & \text{if } P_t \leq MA_{20}(t) \end{cases}
\]

### 2.2 Parameter Variations and Trade-offs

| Variant | Description | Pros | Cons |
|---------|-------------|------|------|
| **Single MA20 (baseline)** | Price vs 20-day SMA | Simple, widely tested | More whipsaw in choppy markets |
| **Dual MA (10/30)** | Crossover: 10-day SMA crosses above 30-day SMA = risk-on | Fewer false signals | Slower to enter after regime change |
| **EMA20** | Exponential vs simple weighting | More responsive to recent moves | Slightly harder to explain |
| **MA10 (tighter)** | 10-day window | Better for short 25-day window | More whipsaw |
| **MA30 (wider)** | 30-day window | Slower regime changes | May lag into bear phase |

**Recommended for this hackathon**: Start with single **SMA20** on BTC daily close. If time allows during testing, implement a **dual MA10/30 crossover** as a secondary check. The 25-day competition window aligns well with a 20-day lookback because a few days of data will be sufficient to confirm the regime each morning.

### 2.3 Evaluation Frequency and Whipsaw Prevention

The regime is evaluated **once per day at a fixed UTC time** (e.g., 00:00 UTC). Intra-day re-evaluation is intentionally avoided to prevent over-reaction to transient volatility.

**Whipsaw buffer**: Require that the signal persists for **2 consecutive daily evaluations** before switching regime. A one-day flip does not trigger a full portfolio liquidation.

```python
# Pseudocode: regime evaluation
if btc_price > ma20 and prev_regime == 'risk-on':
    regime = 'risk-on'
elif btc_price > ma20 and prev_regime == 'risk-off':
    # transition candidate: require confirmation next day
    regime_candidate = 'risk-on'
    regime = 'risk-off'  # hold until confirmed
elif btc_price <= ma20:
    regime = 'risk-off'
    regime_candidate = None
```

***

## 3. Cross-Sectional Ranking: Which Coins to Hold

### 3.1 Universe Definition

The full universe consists of all tradeable pairs on the Roostoo exchange, retrieved via `GET /v3/exchangeInfo`. Apply the following filters before ranking:[^2]

- `CanTrade == True`
- **Minimum lookback**: Coin must have at least 3 days of sampled price history in local logs.
- **Minimum volume proxy**: `UnitTradeValue` (from `/v3/ticker`) > some floor threshold (e.g., $1M 24h USD volume). This filters out illiquid or thinly traded altcoins.[^7]
- **Exclude outliers**: Remove coins with 24-hour Change > ±50% (possible data error or extreme pump event).

This typically yields a tradeable universe of 40–60 coins from the 60+ listed.[^2]

### 3.2 Momentum Features

Define the following return features for coin \( i \) at time \( t \):

\[
r_1(i, t) = \frac{P_i(t) - P_i(t - 1d)}{P_i(t - 1d)} \quad \text{(1-day return)}
\]

\[
r_3(i, t) = \frac{P_i(t) - P_i(t - 3d)}{P_i(t - 3d)} \quad \text{(3-day return)}
\]

\[
r_7(i, t) = \frac{P_i(t) - P_i(t - 7d)}{P_i(t - 7d)} \quad \text{(7-day return)}
\]

Research shows that 7–30 day returns are the most predictive for cross-sectional crypto momentum, with 1–3 week estimation windows showing consistent evidence.[^8][^5]

**Composite momentum score:**

\[
\text{MomScore}(i, t) = 0.2 \cdot r_1(i,t) + 0.3 \cdot r_3(i,t) + 0.5 \cdot r_7(i,t)
\]

The weighting places the highest emphasis on the 7-day window (most predictive per academic evidence) while using 1-day and 3-day returns as tiebreakers and recency adjusters.

### 3.3 Volatility Calculation

For each coin, compute rolling realized volatility over the last 7 days of daily returns:

\[
\sigma_i(t) = \sqrt{\frac{1}{6} \sum_{j=1}^{7} \left( r_{1}(i, t-j+1) - \bar{r} \right)^2}
\]

Floor volatility at a minimum of 1% to avoid division-by-zero for stablecoin-like assets.

### 3.4 Ranking and Selection Rule

1. Sort all universe coins by `MomScore` descending.
2. Select the **top N = 5** coins (adjustable: 3–8 based on portfolio concentration preference).
3. Break ties by 1-day return.
4. Exclude the bottom 20% of coins by `MomScore` even if they appear in a smaller universe (anti-momentum guard).

***

## 4. Signal Definition and Integration

### 4.1 Combined Signal Logic

```
IF regime == 'risk-off':
    target_positions = {all coins: 0.0}  # full cash
    action = liquidate all holdings

ELIF regime == 'risk-on':
    ranked_coins = cross_sectional_rank(universe)
    selected = top_N(ranked_coins, N=5)
    target_positions = compute_weights(selected)
    action = rebalance toward target_positions
```

### 4.2 Position Sizing: Inverse Volatility Weighting

For each selected coin \( i \) in the top-N set:

\[
w_i^{\text{raw}} = \frac{1 / \sigma_i}{\sum_{j \in \text{top-N}} 1/\sigma_j}
\]

Apply a per-coin cap (e.g., max 20% of portfolio) and a portfolio exposure target (e.g., 85% invested, 15% cash buffer):

\[
w_i = \min\left(w_i^{\text{raw}}, 0.20\right) \cdot 0.85
\]

Then renormalize weights to sum to 0.85.

**Alternative: Equal-weight among top-N** (simpler, use as fallback):

\[
w_i = \frac{0.85}{N} \quad \forall i \in \text{top-N}
\]

Inverse volatility weighting is the theoretically preferred approach, as it allocates proportionally less to riskier coins, reducing portfolio variance. In practice, for a small team under time pressure, equal-weight is an acceptable starting point.[^7]

### 4.3 Scoring Function with Signal Strength

For more nuanced sizing, a **risk-adjusted score** can drive position weights:

\[
\text{Score}(i) = \frac{\text{MomScore}(i)}{\sigma_i}
\]

\[
w_i^{\text{score}} = \frac{\text{Score}(i)}{\sum_{j \in \text{top-N}} \text{Score}(j)} \cdot 0.85
\]

This approach naturally gives more weight to coins with strong momentum *and* lower volatility — the ideal combination for risk-adjusted performance.

### 4.4 Signal Update Frequency

| Signal | Update Frequency | Rationale |
|--------|-----------------|-----------|
| BTC regime (MA20) | Once per day (00:00 UTC) | Slow filter, avoids whipsaw |
| Cross-sectional ranks | Every 60 minutes | Balances freshness vs. churn |
| Target weights | Every 60 minutes (with regime-on) | Drives rebalancing decisions |
| Order placement | At most 1 order per 60 seconds | Respects rate limits[^1] |

***

## 5. Data and Feature Engineering Under the Roostoo API

### 5.1 Available Endpoints and What to Log

The Roostoo API provides the following relevant data:[^2]

| Endpoint | Data Fields Relevant to Strategy |
|----------|----------------------------------|
| `GET /v3/exchangeInfo` | All tradeable pairs, AmountPrecision, PricePrecision |
| `GET /v3/ticker` | LastPrice, Change (24h%), MaxBid, MinAsk, CoinTradeValue, UnitTradeValue |
| `GET /v3/balance` | Free and locked balance per coin |
| `POST /v3/place_order` | Execution, FilledAverPrice, Commission |

**Critical gap**: The Roostoo API does **not** provide historical OHLCV candle data. This means:[^2]
- Price history must be **self-constructed** by logging `LastPrice` from `/v3/ticker` at fixed intervals.
- For the first 20+ days of history needed by the MA filter, **initialize from an external source** (Binance public API: `GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=30`). This is permissible since Roostoo prices track real market prices in real time.[^9]

### 5.2 Local Bar Construction

Poll `/v3/ticker` (all pairs, no `pair` param) every **5 minutes**. Log to a local SQLite database or CSV:

```
| timestamp | symbol | last_price | bid | ask | volume_24h_usd |
```

Aggregate 5-minute snapshots into:
- **Daily bars**: Group by UTC date, take last snapshot as "daily close"
- **Hourly bars**: Take last snapshot per UTC hour

Use daily bars for the MA20 regime filter. Use hourly or 60-minute bars for cross-sectional momentum returns.

```python
# Bar builder pseudocode
def build_daily_bar(df, date):
    day_df = df[df['date'] == date]
    return {
        'close': day_df.iloc[-1]['last_price'],
        'volume': day_df['volume_24h_usd'].mean()
    }
```

### 5.3 Handling Missing Data and Thin Coins

- **Missing poll**: If a ticker fetch fails, fill forward the last known price. Log the gap.
- **New listings**: Require a minimum of **3 days** (72 hours) of local history before including a coin in cross-sectional rankings.
- **Zero/null price**: Skip coin for that cycle; do not include in ranking.
- **Volume filter**: If `UnitTradeValue` (proxy for 24h USD volume) is below $500,000, exclude from selection even if momentum rank is high.

***

## 6. Portfolio Construction and Sizing

### 6.1 Assumptions

- Starting capital: **USD 50,000**[^2]
- Long-only, no leverage, no shorting
- Market orders for immediate fills (0.1% commission); limit orders for rebalancing (0.05% commission) if the target is not time-sensitive[^10]
- Transaction costs must be considered in sizing — avoid tiny rebalancing trades where the commission exceeds 10% of the position delta value

### 6.2 Target Weight Computation

```python
def compute_target_weights(ranked_coins, regime, portfolio_value, config):
    if regime == 'risk-off':
        return {coin: 0.0 for coin in all_coins}

    top_n = ranked_coins[:config['N']]  # N=5
    
    # Inverse-vol weighting
    inv_vols = {c: 1.0 / max(vol[c], 0.01) for c in top_n}
    total_iv = sum(inv_vols.values())
    raw_weights = {c: inv_vols[c] / total_iv for c in top_n}
    
    # Apply per-coin cap
    capped = {c: min(w, config['max_weight']) for c, w in raw_weights.items()}
    
    # Normalize to target exposure
    total_capped = sum(capped.values())
    target_exposure = config['target_exposure']  # 0.85
    
    weights = {c: (capped[c] / total_capped) * target_exposure for c in top_n}
    return weights

def weights_to_usd(weights, portfolio_value):
    return {c: w * portfolio_value for c, w in weights.items()}
```

### 6.3 Rebalancing Logic

Convert USD target to coin quantity:

\[
\text{target\_qty}(i) = \frac{w_i \cdot \text{portfolio\_value}}{P_i^{\text{current}}}
\]

Compare to current holdings from `/v3/balance`. Compute delta:

\[
\Delta q_i = \text{target\_qty}(i) - \text{current\_qty}(i)
\]

- If \( |\Delta q_i| \cdot P_i < \text{min\_trade\_usd} \) (e.g., $50), skip — avoids churning on trivial adjustments.
- Process **SELL orders first**, then BUY orders, to free up USD capital before purchasing.

***

## 7. Execution Logic and Rate-Limit-Friendly Loop

### 7.1 Main Trading Loop Architecture

The bot runs a continuous `while True` loop on the EC2 instance, sleeping between cycles:[^1]

```python
# Main loop skeleton
import time, logging, yaml
from datetime import datetime, timezone

config = yaml.safe_load(open('config.yaml'))
state = initialize_state(config)  # loads price history, positions

while True:
    try:
        cycle_start = time.time()
        now_utc = datetime.now(timezone.utc)

        # Step 1: Fetch latest prices (1 API call — all tickers at once)
        tickers = fetch_all_tickers()
        log_prices(tickers, now_utc)

        # Step 2: Update regime (once per day or forced re-eval)
        if is_daily_regime_time(now_utc) or state.regime_stale:
            state.regime = compute_regime(state.price_history['BTC'])
            logging.info(f"Regime: {state.regime}")

        # Step 3: Recompute cross-sectional ranks (every 60 min)
        if minutes_since_last_rank(state) >= 60:
            state.ranks = cross_sectional_rank(tickers, state.price_history, config)
            state.target_weights = compute_target_weights(state.ranks, state.regime, config)
            state.last_rank_time = now_utc

        # Step 4: Compute order deltas
        current_positions = fetch_balance()
        orders = compute_orders(state.target_weights, current_positions, tickers, config)

        # Step 5: Place at most 1-2 orders per cycle (rate-limit aware)
        orders_this_cycle = 0
        for order in prioritized_orders(orders):
            if orders_this_cycle >= config['max_orders_per_cycle']:
                break
            result = place_order(order)
            log_order(result)
            orders_this_cycle += 1
            time.sleep(config['order_spacing_sec'])  # ~5-10 sec between orders

        # Step 6: Risk checks
        if portfolio_drawdown(state) > config['max_drawdown']:
            trigger_risk_off(state)

        # Sleep remainder of cycle
        elapsed = time.time() - cycle_start
        time.sleep(max(0, config['cycle_sec'] - elapsed))  # cycle_sec=300 (5 min)

    except Exception as e:
        logging.error(f"Loop error: {e}", exc_info=True)
        time.sleep(60)
```

### 7.2 Rate Limit Management

The Roostoo API enforces a hard rate limit (approximately one trade per minute). Best practices for staying within limits:[^1]

- **Batch ticker fetches**: A single call to `/v3/ticker` (without `pair`) returns all tickers at once — this is one API call for 60+ coins. Never loop per-coin.[^2]
- **Order pacing**: Place at most 1 order per 60-second window (conservative budget). Use `time.sleep(65)` after each order.
- **Exponential backoff**: On HTTP 429, wait 2× the current wait time before retrying.[^11]
- **Cycle cadence**: Data fetch every 5 minutes; order placement only if material rebalancing is needed; full re-rank every 60 minutes.

```python
def safe_request(func, *args, max_retries=3, **kwargs):
    wait = 5
    for attempt in range(max_retries):
        try:
            resp = func(*args, **kwargs)
            if resp.status_code == 429:
                logging.warning(f"Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                wait *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logging.error(f"Request failed (attempt {attempt+1}): {e}")
            time.sleep(wait)
            wait *= 2
    return None
```

### 7.3 HMAC Authentication Helper

```python
import hmac, hashlib, time, os

API_KEY = os.environ['ROOSTOO_API_KEY']
SECRET_KEY = os.environ['ROOSTOO_SECRET_KEY']
BASE_URL = 'https://mock-api.roostoo.com'

def get_signed_headers(params: dict) -> dict:
    params['timestamp'] = str(int(time.time() * 1000))
    sorted_params = sorted(params.items())
    query_string = '&'.join(f"{k}={v}" for k, v in sorted_params)
    sig = hmac.new(SECRET_KEY.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    return {
        'RST-API-KEY': API_KEY,
        'MSG-SIGNATURE': sig,
        'Content-Type': 'application/x-www-form-urlencoded'
    }, query_string
```

***

## 8. Risk Management and Guardrails

### 8.1 Per-Asset Limits

| Parameter | Default Value | Notes |
|-----------|--------------|-------|
| Max weight per coin | 20% of portfolio | Prevents single-coin concentration |
| Max number of coins held | 5 (N) | Manageable for small team |
| Minimum trade size | $50 USD notional | Avoids trivial rebalances |
| Max daily trades | ~20 orders | ~1 per hour × two per rebalance |
| Commission budget (daily) | $100 (0.2% of portfolio) | Monitor and reduce N or frequency if exceeded |

### 8.2 Portfolio-Level Drawdown Control

Track portfolio peak value \( V_{\text{peak}} \) and current value \( V_t \):

\[
\text{DD}(t) = \frac{V_t - V_{\text{peak}}}{V_{\text{peak}}}
\]

**Drawdown-based de-risking ladder:**

```python
if DD < -0.05:   # 5% drawdown: reduce exposure from 85% to 70%
    config['target_exposure'] = 0.70
if DD < -0.10:   # 10% drawdown: reduce to 50%
    config['target_exposure'] = 0.50
if DD < -0.15:   # 15% drawdown: go to full cash regardless of regime
    state.regime = 'risk-off_forced'
    liquidate_all_positions()
```

Restore normal exposure only after the portfolio recovers to 95% of peak and the regime filter confirms risk-on.

### 8.3 Per-Position Soft Stops

For each held position, track entry price \( P_{\text{entry}} \). Compute a volatility-based stop:

\[
\text{stop\_level}(i) = P_{\text{entry}}(i) \cdot \left(1 - 2 \cdot \sigma_i\right)
\]

If `LastPrice(i) < stop_level(i)`, generate a SELL signal for coin \( i \) regardless of cross-sectional rank, and exclude it from the next rebalancing cycle for 24 hours.

### 8.4 Kill Switch Conditions

The bot automatically halts and closes all positions under the following conditions:

- **Repeated API failures**: ≥5 consecutive failed requests (could indicate network, credential, or server issue).
- **Server time drift**: If `abs(local_time - server_time) > 60s`, authentication will fail. Halt and alert.[^2]
- **Abnormal price spike**: Any coin's 24-hour Change > ±80% triggers exclusion from that coin only. If BTC itself shows > ±40% in one day, trigger full cash exit.
- **Max daily loss**: If portfolio drops > 20% in a single day, stop bot and send alert.

```python
def kill_switch_check(state, tickers):
    if state.consecutive_api_errors >= 5:
        logging.critical("KILL SWITCH: API failures. Liquidating.")
        liquidate_all(); sys.exit(1)
    if abs(get_server_time() - time.time()*1000) > 60000:
        logging.critical("KILL SWITCH: Clock drift. Halting.")
        sys.exit(1)
    btc_change = tickers.get('BTC/USD', {}).get('Change', 0)
    if abs(btc_change) > 0.40:
        logging.warning("BTC 40% daily move. Forcing cash.")
        state.regime = 'risk-off_forced'
```

***

## 9. Backtesting and Validation

### 9.1 Data Sourcing for Backtest

Roostoo does not provide historical candle data via its API. Use public exchange data:[^2]

- **Binance REST API**: `GET /api/v3/klines?symbol=BTCUSDT&interval=1d` for daily bars (free, no API key needed for market data).
- For altcoins: Use the Binance symbols that correspond to Roostoo's universe (most Roostoo coins are major exchange-listed assets).
- Time period: Use at least 6–12 months of history (e.g., Oct 2024 – Mar 2026) to capture both bull and bear phases.

### 9.2 Backtest Construction

```python
# Backtest skeleton
for date in backtest_dates:
    # Regime check
    ma20 = price_history['BTC'].rolling(20).mean().loc[date]
    btc_price = price_history['BTC'].loc[date]
    regime = 'risk-on' if btc_price > ma20 else 'risk-off'
    
    # Cross-sectional ranking (only on risk-on days)
    if regime == 'risk-on':
        r7 = returns_7d.loc[date]  # cross-section of 7d returns
        r3 = returns_3d.loc[date]
        r1 = returns_1d.loc[date]
        scores = 0.5*r7 + 0.3*r3 + 0.2*r1
        selected = scores.nlargest(N).index.tolist()
        vol = rolling_7d_vol.loc[date, selected]
        weights = (1/vol) / (1/vol).sum() * 0.85
    else:
        weights = {}
    
    # Simulate trades with transaction costs
    prev_weights = portfolio.get_weights(date - 1)
    turnover = compute_turnover(prev_weights, weights)
    cost = turnover * 0.001  # 0.1% market order commission
    portfolio.rebalance(date, weights, cost)
```

### 9.3 Evaluation Metrics

| Metric | Formula | Target Range |
|--------|---------|--------------|
| Cumulative Return | \( (V_T - V_0) / V_0 \) | > 20% over 25 days |
| Annualized Sharpe | \( \sqrt{252} \cdot \bar{r} / \sigma_r \) | > 1.5 |
| Max Drawdown | \( \min(V_t - V_{\text{peak}}) / V_{\text{peak}} \) | < -20% |
| Hit Rate | Fraction of trades with positive PnL | > 55% |
| Daily Turnover | Avg daily weight change | < 30% |
| Cross-sectional dispersion | Std of returns across selected vs rest | Positive spread desired |

### 9.4 Robustness Checks

Run the backtest varying:
- MA window: 10, 15, **20** (baseline), 25, 30 days
- Momentum weights: equal across r1/r3/r7 vs baseline [0.2/0.3/0.5]
- N (coins selected): 3, **5** (baseline), 7, 10
- Rebalancing frequency: daily, every 12h, every 6h
- Target exposure: 70%, **85%** (baseline), 95%

Accept the baseline parameters only if performance is reasonably stable (Sharpe > 1.0, drawdown < 25%) across most variations. This is the primary defense against overfitting.

***

## 10. System Architecture on AWS EC2

### 10.1 Component Overview

```
┌─────────────────────────────────────────────────────┐
│                   AWS EC2 (t3.small)                │
│                                                     │
│  ┌────────────┐   ┌──────────────┐   ┌───────────┐  │
│  │  Scheduler │──▶│  Data Fetcher│──▶│  Bar      │  │
│  │  (loop)    │   │  (requests)  │   │  Builder  │  │
│  └────────────┘   └──────────────┘   └───────────┘  │
│         │                                  │         │
│         ▼                                  ▼         │
│  ┌────────────────────────────────────────────────┐  │
│  │           Local Price Store (SQLite / CSV)     │  │
│  └────────────────────────────────────────────────┘  │
│         │                                            │
│         ▼                                            │
│  ┌──────────────┐   ┌──────────────┐                 │
│  │ Strategy     │──▶│ Risk Engine  │                 │
│  │ Engine       │   │ (limits,     │                 │
│  │ (regime +    │   │  drawdown,   │                 │
│  │  rank)       │   │  kill switch)│                 │
│  └──────────────┘   └──────────────┘                 │
│         │                 │                          │
│         ▼                 ▼                          │
│  ┌──────────────────────────────────────────────┐    │
│  │       Execution Handler (Roostoo API)        │    │
│  │   place_order / cancel_order / query_order   │    │
│  └──────────────────────────────────────────────┘    │
│                                                     │
│  ┌────────────────────────────────────────────────┐  │
│  │         Logger / Monitor                       │  │
│  │   (structured JSON logs + trade blotter CSV)   │  │
│  └────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### 10.2 Python Stack

| Component | Library | Notes |
|-----------|---------|-------|
| HTTP requests | `requests` or `httpx` | Both handle HMAC headers |
| Data processing | `pandas`, `numpy` | Bar construction, MA, momentum |
| Scheduler | `while True` + `time.sleep()` | Simpler than APScheduler for single process |
| Config | `PyYAML` + `python-dotenv` | YAML config file; secrets via env vars |
| Logging | Python `logging` (JSON formatter) | Structured logs; one file per day |
| Storage | `sqlite3` or flat CSV | Lightweight; persist price history across restarts |
| Process management | `systemd` or `screen`/`tmux` | Keep bot alive if shell disconnects |

### 10.3 Configuration File (`config.yaml`)

```yaml
strategy:
  N: 5                        # top-N coins to hold
  ma_window: 20               # days for BTC trend filter
  momentum_weights: [0.2, 0.3, 0.5]  # r1, r3, r7
  target_exposure: 0.85
  max_weight_per_coin: 0.20
  min_trade_usd: 50.0
  min_volume_usd: 500000

execution:
  cycle_sec: 300              # 5 min main loop
  rank_interval_min: 60       # re-rank every 60 min
  max_orders_per_cycle: 2
  order_spacing_sec: 65

risk:
  max_drawdown_soft: -0.05    # reduce exposure
  max_drawdown_hard: -0.15    # full cash
  btc_daily_move_kill: 0.40
  max_consecutive_errors: 5

data:
  db_path: './prices.db'
  warmup_source: 'binance'    # binance | roostoo_live_only
  log_dir: './logs/'
```

### 10.4 Monitoring and Health Dashboard

- **Logs**: Use structured JSON logging. Parse with `jq` or pipe into a simple HTML dashboard (Flask, 1 page).
- **Key metrics to monitor live**: Current portfolio value, regime state, active positions and weights, last regime change time, number of API errors in last hour, current drawdown.
- **Simple monitoring script** (run separately): Reads the SQLite DB and prints a status table every 60 seconds. Can be left running in a second `tmux` pane.
- **Alert on failure**: Log critical errors to a file and optionally use a Telegram bot webhook for push notifications on kill-switch triggers or large drawdowns.

***

## 11. Hackathon Timeline and Iteration Plan

### Day-by-Day Build Plan (16 days: Mar 16 – Apr 1)

| Days | Phase | Key Tasks |
|------|-------|-----------|
| **Days 1–2** (Mar 16–17) | Foundation | Set up EC2, clone repo, install deps; implement API auth (`get_signed_headers`); test `/v3/ticker` call; build price logger to SQLite; seed BTC history from Binance |
| **Days 3–4** (Mar 18–19) | Core Strategy | Implement `compute_regime()` with MA20; implement `cross_sectional_rank()` with MomScore; implement `compute_target_weights()` with inv-vol sizing |
| **Days 5–6** (Mar 20–21) | Execution Engine | Implement `compute_orders()` delta logic; implement `place_order()` with HMAC; implement main loop skeleton; paper-test with dummy wallet (cancel immediately after placing) |
| **Days 7–8** (Mar 22–23) | Live Trading Start | Bot goes live (Mar 21); monitor closely; verify order fills, balance reconciliation; tune `min_trade_usd` and `order_spacing_sec` |
| **Days 9–10** (Mar 24–25) | Backtest Validation | Run offline backtest on Binance historical data; compute Sharpe, drawdown, hit rate; validate regime filter improves risk-adjusted returns vs pure momentum |
| **Days 11–12** (Mar 26–27) | Risk and Monitoring | Implement drawdown kill-switch; add per-position stop logic; build simple monitoring dashboard; test restart/recovery behavior |
| **Days 13–14** (Mar 28–29) | Conservative Tuning | Analyze first 8 days of live logs; if underperforming, check: regime filter accuracy, top-N selection quality, transaction costs drag; adjust N or exposure conservatively |
| **Days 15–16** (Mar 30–31) | Presentation Prep | Begin slide deck; generate equity curve chart from live blotter; compute live Sharpe and drawdown; write clean code comments for GitHub |
| **Days 17+** (Apr 1–14) | Maintain and Refine | Monitor daily; one small parameter update only if justified by ≥5 days of data; focus on presentation quality |

### What to Prioritize First vs. Later

**Must-have (Days 1–6)**:
- Working API authentication and ticker fetch
- Price history logger (SQLite)
- MA20 regime filter
- Cross-sectional rank with MomScore
- Order placement with rate-limit awareness
- Basic portfolio balance reconciliation

**Nice-to-have (Days 7–14, only if time permits)**:
- Inverse volatility weighting (vs equal-weight)
- Per-position soft stops
- Dual MA10/30 crossover as alternative regime filter
- Telegram alert notifications
- Interactive monitoring dashboard

**Do not build (too risky for hackathon)**:
- ML-based signal generation (overfitting risk)
- Dynamic N or adaptive exposure (too many parameters)
- Limit order optimization (adds complexity for marginal commission savings)

### Parameter Tuning Discipline

With only ~25 days of live trading data, **statistical noise dominates**. Rules for conservative tuning:
- Change at most **one parameter at a time**, and only if supported by at least 7 days of live data showing a consistent pattern.
- Never optimize parameters to fit the first 5–7 days of live performance — this is in-sample overfitting.
- Use the backtest (Binance historical data) as the primary parameter justification for the final presentation.
- The leaderboard reflects your real-time rank; avoid chasing it by over-trading or switching strategies mid-competition.

***

## 12. Presentation and Narrative

### 12.1 The Strategy Story for Judges

**Economic Intuition**: Crypto assets exhibit strong correlated market-wide moves driven by Bitcoin as the global risk barometer. Deploying capital only when BTC is in an uptrend (above its 20-day moving average) avoids the most destructive bear-phase drawdowns. Within uptrend periods, cross-sectional momentum — buying the recent relative outperformers — exploits the well-documented tendency of crypto assets to exhibit short-term performance persistence, driven by herd behavior, narrative momentum, and slow information diffusion across the altcoin universe.[^5][^7]

**Why this hybrid is robust for live competition**: The trend filter is a coarse, slow-moving signal that dramatically reduces false positives. The cross-sectional layer adds active alpha by distinguishing *which* coins to own when the broad market is supportive. Neither layer alone is sufficient: pure trend following misses the cross-sectional dispersion of returns; pure momentum without a regime filter suffers severe drawdowns in bear markets.[^12]

**Risk controls and implementation**: The system is fully automated, explainable, and designed around Roostoo's one-trade-per-minute constraint. Every position is bounded by a 20% cap, a volatility-based stop, and a portfolio-level drawdown kill-switch. The open-source code on GitHub provides full transparency.

### 12.2 Elevator Pitch (2–3 sentences)

*"Our strategy uses Bitcoin's 20-day moving average as a macro regime filter — we only take risk when the broad crypto market is trending upward. Within those risk-on periods, we rotate capital daily into the top five cross-sectional momentum names across the 60+ coin Roostoo universe, weighted by inverse volatility to maximize risk-adjusted returns. This two-layer design delivers disciplined exposure management with a clear economic rationale, implemented as a fully automated bot on AWS that respects all Roostoo API constraints."*

### 12.3 Key Charts and Tables for the Final Deck

| Slide / Chart | Description | What It Shows |
|--------------|-------------|---------------|
| **1. Equity Curve** | Cumulative portfolio value vs time (backtest + live) | Overall performance trajectory |
| **2. Regime Overlay** | BTC price with MA20, green/red shading for risk-on/off periods | Regime filter value-add; avoids bear phases |
| **3. Regime vs Non-Regime Returns** | Bar chart: avg daily return in risk-on vs risk-off periods | Statistical justification of trend filter |
| **4. Top-N vs Rest** | Cross-sectional return spread: selected top-N coins vs remaining universe | Momentum signal quality / alpha |
| **5. Risk Table** | Max drawdown, Sharpe ratio, hit rate, turnover — backtest vs live | Rigorous risk-adjusted performance metrics |

### 12.4 Narrative for the 12-Slide Deck Structure

1. Team intro and problem statement
2. Strategy overview (the hybrid concept, 1-page visual)
3. Economic intuition and academic evidence[^12][^5]
4. Regime filter design (MA20, BTC, formulas)
5. Cross-sectional ranking (MomScore formula, top-N selection)
6. Portfolio construction and risk controls
7. Backtesting methodology and data sources
8. Backtest results (equity curve, Sharpe, drawdown)
9. Live trading results (Roostoo leaderboard performance, live equity curve)
10. System architecture (EC2 diagram, Python stack)
11. Robustness analysis (parameter sensitivity table)
12. Conclusion and key takeaways

***

## Appendix A: Full API Reference Summary

| Endpoint | Method | Auth Level | Key Parameters | Use Case |
|----------|--------|-----------|----------------|----------|
| `/v3/serverTime` | GET | None | — | Clock sync |
| `/v3/exchangeInfo` | GET | None | — | Get tradeable universe |
| `/v3/ticker` | GET | TSCheck | `timestamp`, optional `pair` | Price data for all coins |
| `/v3/balance` | GET | TopLevel | `timestamp` | Current holdings |
| `/v3/pending_count` | GET | TopLevel | `timestamp` | Open order management |
| `/v3/place_order` | POST | TopLevel | `pair`, `side`, `type`, `quantity`, `timestamp` | Buy/sell execution |
| `/v3/query_order` | POST | TopLevel | `timestamp`, optional `pair`, `order_id` | Order history |
| `/v3/cancel_order` | POST | TopLevel | `timestamp`, optional `pair`, `order_id` | Cancel pending orders |

Auth headers: `RST-API-KEY` and `MSG-SIGNATURE` (HMAC SHA256 of sorted params + timestamp).[^2]
Timestamp must be within ±60 seconds of server time.[^2]

***

## Appendix B: Critical Formula Reference

**BTC Trend Filter:**
\[
MA_{20}(t) = \frac{1}{20}\sum_{i=0}^{19} P_{\text{BTC}}(t-i), \quad \text{regime}(t) = \begin{cases} \text{risk-on} & P_t > MA_{20}(t) \\ \text{risk-off} & P_t \leq MA_{20}(t) \end{cases}
\]

**Composite Momentum Score:**
\[
\text{MomScore}(i,t) = 0.2 \cdot r_1(i,t) + 0.3 \cdot r_3(i,t) + 0.5 \cdot r_7(i,t)
\]

**Risk-Adjusted Score:**
\[
\text{Score}(i) = \frac{\text{MomScore}(i)}{\sigma_i}
\]

**Inverse-Volatility Weight:**
\[
w_i = \frac{1/\sigma_i}{\sum_{j \in \text{top-N}} 1/\sigma_j} \cdot \min\left(w_i^{\text{raw}}, 0.20\right) \cdot 0.85
\]

**Portfolio Drawdown:**
\[
\text{DD}(t) = \frac{V_t - \max_{s \leq t} V_s}{\max_{s \leq t} V_s}
\]

---

## References

1. [SG vs. HK Quant Trading Hackathon (Universities)](https://luma.com/tqx5xvcy) - This competition is designed for discretionary/directional strategies (e.g. one trade per minute). R...

2. [roostoo/Roostoo-API-Documents - GitHub](https://github.com/roostoo/Roostoo-API-Documents) - A SIGNED endpoint also requires a timestamp parameter to be sent, which is a millisecond timestamp (...

3. [[SG vs HK Quant Trading Hackathon] [ ...](https://www.instagram.com/p/DVqSF6LE-C9/) - Grand Finale: Apr 17 – Apr 21 (In-person in SG & HK!) Who can join? Undergrads/Postgrads in CS, Quan...

4. [Introducing Roostoo Token Initiative (RSTO)](https://www.roostoo.com/rsto/) - The primary function of RSTO is to reward good mock traders on Roostoo. We decide to start with a si...

5. [Cross-sectional Momentum in Cryptocurrency Markets](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID4337066_code2135545.pdf?abstractid=4322637&mirid=1) - This paper reviews the evidence of price momentum in cryptocurrency markets, explores the constructi...

6. [[PDF] Systematic Trend-Following with Adaptive Portfolio Construction](https://arxiv.org/pdf/2602.11708.pdf)

7. [Cross-Sectional Momentum in Crypto: How to Trade the ...](https://www.fxempire.com/education/article/cross-sectional-momentum-in-crypto-how-to-trade-the-strongest-trends-1535830) - Cross-sectional momentum strategies track the best and worst performing assets across the crypto mar...

8. [GitHub - itsNH98/cryptocurrency_momentum_strategy: Cryptocurrency trading strategy involving price momentum, size and attention proxied by Google Searches](https://github.com/itsNH98/cryptocurrency_momentum_strategy) - itsNH98 / **
cryptocurrency_momentum_strategy ** Public

# itsNH98/cryptocurrency_momentum_strategy
...

9. [Roostoo: Mock Crypto Trading – Apps bei Google Play](https://play.google.com/store/apps/details?id=com.roostoo.roostoo&hl=gsw) - Realistic Crypto Market Simulation

10. [Roostoo User Manual](https://api.unstop.com/api/competition/get-attachment/5ea1838aa381f_roostoo-user-manual.pdf) - Market order (0.1% commission, trade at the current market price);. • Limit order (0.05% commission,...

11. [Mastering API Rate Limits: Reliable Crypto Data Integration](https://www.tokenmetrics.com/blog/mastering-api-rate-limits-crypto-data-integration) - Learn how to handle API rate limits when calling a crypto data endpoint. Discover best practices, er...

12. [Momentum and Trend Following Trading Strategies for Currencies Revisited - Combining Academia and Industry](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2949379) - Momentum trading strategies are thoroughly described in the academic literature and used in many tra...

