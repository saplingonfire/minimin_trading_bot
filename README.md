# Minimin Trading Bot

Modular crypto trading bot for the [Roostoo](https://github.com/roostoo/Roostoo-API-Documents) API, built for the **SG vs HK University Web3 Quant Hackathon**. Includes a Python SDK for the Roostoo Public API (v3), pluggable strategies, risk controls, backtesting, and a read-only dashboard.

---

## Features

- **Roostoo SDK** — Python client for Roostoo Public API v3: server time, exchange info, ticker, balance, place/cancel/query orders (market and limit). HMAC signing, configurable base URL.
- **Modular bot** — Strategy abstraction (`Strategy.next(context) -> signals`), execution layer (precision, risk guards, retries), market-data facade. Add strategies under `bot/strategies/` and register by name.
- **Fee-aware trading** — Configurable trading fees. Strategies adjust buy quantities to account for fees and use dead-zone filters to prevent unprofitable churn from small rebalances. Backtest engine deducts fees from simulated fills.
- **Test vs live credentials** — Two credential sets (env: `ROOSTOO_TEST_*` and `ROOSTOO_API_KEY`/`ROOSTOO_SECRET_KEY`). Switch with `BOT_LIVE` or CLI `--live` / `--test`.
- **Config** — Env vars (`.env`) plus optional `config.yaml` for strategy params, execution pacing, risk, and backtest settings. Strategy section is merged into `strategy_params` for the chosen strategy.
- **Risk and kill switch** — Drawdown-based de-risking, market-move circuit breakers, consecutive API error halt, optional cancel-on-stop for managed pairs.
- **Strategies** — Multiple pluggable strategies with configurable parameters. See `bot/strategies/` for implementations.
- **Backtest** — Script runs configured strategy over Binance historical OHLCV; prints performance report (returns, drawdown, fees, etc.). Simulates trading fees on all fills.
- **Historical data sync** — Script to download Binance spot klines to local CSV (Roostoo tradeable universe or custom tickers). Strategies that need OHLCV use `BINANCE_DATA_DIR` or a file-based provider.
- **Dashboard** — Read-only web UI (FastAPI) to monitor balance, orders, ticker; uses same SDK. No trading from the dashboard (competition rules). Deployable to Vercel.

---

## Project structure

```
minimin_trading_bot/
├── roostoo/              # Roostoo Public API SDK (client, auth, models, exceptions)
├── bot/                  # Trading bot
│   ├── base.py           # Strategy ABC, TradingContext, PlaceOrderSignal, CancelOrderSignal, FeeSchedule
│   ├── runner.py         # Tick loop, strategy load, kill switch, order pacing
│   ├── market.py         # build_context (ticker, balance, pending orders)
│   ├── execution.py      # Signal → orders; precision, risk guards, retries
│   ├── risk.py           # Drawdown management, kill switch
│   ├── price_store.py    # SQLite price history (daily + hourly closes)
│   ├── regime.py         # Market regime detection
│   ├── strategies/       # Strategy implementations + registry
│   └── backtest/         # Backtest engine and report
├── config/               # BotSettings, load_settings, optional config.yaml merge
├── dashboard/            # Read-only monitoring server
├── scripts/
│   ├── run_bot.py        # Main entrypoint: run bot with --strategy, --live/--test, --dry-run
│   ├── run_backtest.py   # Backtest from config + Binance data
│   ├── sync_binance_historical.py  # Download Binance OHLCV (Roostoo universe or --tickers)
│   ├── warmup_price_store.py  # Backfill prices.db from Binance daily klines (auto-run by run_bot.py)
│   ├── setup_venv.sh     # Create venv and install deps (macOS/Linux)
│   └── setup_venv.bat    # Windows
├── config.yaml           # Strategy params, execution, data paths, backtest (see config.yaml.example)
├── .env.example          # Env template (credentials, BOT_*, optional BINANCE_DATA_DIR)
└── requirements.txt
```

---

## Install

The project uses a **virtual environment in the repo** (`venv/`). Run all commands with this venv activated.

**One-time setup** (from repo root):

```bash
# Create venv and install dependencies
./scripts/setup_venv.sh          # macOS/Linux
# or
scripts\setup_venv.bat           # Windows

# Activate the venv
source venv/bin/activate         # macOS/Linux
# or
venv\Scripts\activate            # Windows
```

**Manual setup:**

```bash
python3 -m venv venv
source venv/bin/activate        # or venv\Scripts\activate on Windows
pip install -e .
pip install -r requirements.txt
```

**Without activating** (use venv Python directly):

```bash
venv/bin/python scripts/run_bot.py --strategy example --dry-run
venv/bin/python -m pytest tests/ -v
```

(On Windows use `venv\Scripts\python.exe`.)

The `venv/` directory is gitignored.

---

## Configuration

### Environment (.env)

Copy `.env.example` to `.env` and set:

- **Credentials**
  - **Test account (default):** `ROOSTOO_TEST_API_KEY`, `ROOSTOO_TEST_SECRET_KEY`. Optional: `ROOSTOO_TEST_BASE_URL` (default `https://mock-api.roostoo.com`).
  - **Live account:** `ROOSTOO_API_KEY`, `ROOSTOO_SECRET_KEY`. Optional: `ROOSTOO_BASE_URL`.
- **Bot**
  - `BOT_STRATEGY` — Strategy name (e.g. `hybrid_trend_cross_sectional`, `hybrid_trend_cross_sectional_throttled`, `example`).
  - `BOT_LIVE` — `true` to use live credentials, `false` (default) for test.
  - `BOT_TICK_SECONDS` — Seconds between ticks (default 30; overridable by `config.yaml` execution.cycle_sec).
  - `BOT_DRY_RUN` — `true` to log orders only, no API place/cancel.
  - `BOT_CANCEL_ORDERS_ON_STOP` — If true, cancel managed pairs on graceful shutdown.
  - `BOT_CONFIG_PATH` — Path to YAML config (default `config.yaml`).
  - Optional: `BOT_STRATEGY_PARAMS` (JSON) to override strategy params; usually strategy params come from `config.yaml`.
  - Optional: `BOT_PRICE_STORE_PATH` or set in config `data.db_path` for hybrid strategies (SQLite price history). Use a **different path per bot process** if you run test and live (or two sessions) on the same host: WAL and retries reduce lock errors but do not replace separate DB files.
  - **Append-only JSONL logs (trades + Roostoo API metadata):** By default, paths depend on account mode: `trades-test.log` / `roostoo-api-test.log` for test, `trades-live.log` / `roostoo-api-live.log` for live. Set `BOT_TRADES_LOG` and/or `BOT_ROOSTOO_API_LOG` to override with a fixed path (e.g. `BOT_TRADES_LOG=trades.log` for the previous single-file behavior). If `data.log_dir` is set in `config.yaml`, default basenames are written under that directory.
  - Optional: `BOT_MAX_ORDERS_PER_CYCLE`, `BOT_ORDER_SPACING_SEC` (or in config execution).
  - Optional risk: `BOT_MAX_PENDING_ORDERS`, `BOT_MAX_ORDER_NOTIONAL`.
- **OHLCV (strategies that need candles):** `BINANCE_DATA_DIR` — Path to Binance CSV dump (e.g. `data/binance`). If unset, strategies that require OHLCV will no-op.

### config.yaml

Optional. Used for strategy params, execution pacing, data paths, and backtest.

- **strategy** — Merged into `strategy_params` for the strategy named in `BOT_STRATEGY`. Includes strategy-specific tuning parameters, **risk** thresholds, and optional **regime** settings. See `config.yaml.example` for the full parameter list.
- **execution** — cycle_sec (tick interval), max_orders_per_cycle, order_spacing_sec, **fees** (market_bps, limit_bps — trading fee rates in basis points, automatically injected into strategy params).
- **data** — db_path (price store), log_dir (optional directory for default trade/API JSONL logs when `BOT_TRADES_LOG` / `BOT_ROOSTOO_API_LOG` are unset).
- **backtest** — start_date, end_date, initial_balance, data_dir.
- **strategy.exclude_pairs** — Optional list of pairs to exclude from the tradeable universe (e.g. `["TRUMP/USD", "PENGU/USD"]`). Env override: `BOT_EXCLUDE_PAIRS` (comma-separated).

See `config.yaml.example` and the in-file comments in `config.yaml`.

---

## Usage

### Running the bot

With the repo venv **activated**:

```bash
cp .env.example .env
# Edit .env: set credentials and BOT_STRATEGY
python scripts/run_bot.py --strategy hybrid_trend_cross_sectional [--test] [--dry-run]
```

**CLI options:**

- `--strategy`, `-s` — Strategy name (overrides `BOT_STRATEGY`).
- `--live` — Use live credentials.
- `--test` — Use test credentials (default).
- `--dry-run` — Do not place or cancel orders; log only.
- `--tick-seconds` — Override tick interval.
- `--skip-warmup` — Skip automatic Binance price store warmup (see below).
- `--env-file` — Path to `.env` (default `.env`).
- `-v` — Verbose logging.

**Price store warmup:** For strategies that use historical price data, `run_bot.py` automatically backfills `prices.db` with recent daily closes from the Binance public klines API. Symbols that already have enough history are skipped. Use `--skip-warmup` to disable. You can also run warmup standalone:

```bash
python scripts/warmup_price_store.py [--db-path prices.db] [--days 30] [--tickers BTC,ETH,SOL]
```

**Strategies:**

Multiple strategies are available under `bot/strategies/`. Set the strategy name via `BOT_STRATEGY` in `.env` or `--strategy` on the CLI. Some strategies run entirely on Roostoo ticker data (with a local price store), while others require historical OHLCV from Binance (`BINANCE_DATA_DIR`). See `config.yaml.example` for tunable parameters.

**Hackathon (AWS EC2):**

1. Follow the [Roostoo hackathon guide](https://roostoo.notion.site/Hackathon-Guide-How-to-Sign-In-AWS-and-Launch-Your-Bot-309ba22fed798071b4dde6d1e8666816): launch instance in ap-southeast-2, connect via Session Manager.
2. Clone the repo, create and activate venv (`./scripts/setup_venv.sh` then `source venv/bin/activate`).
3. Copy `.env.example` to `.env`, set test/live credentials and `BOT_STRATEGY`.
4. Run under **tmux** so the bot keeps running after disconnect: `tmux`, then `python scripts/run_bot.py --strategy <your_strategy>`. Detach: `Ctrl+B` then `D`. Reattach: `tmux attach`.

---

### Historical data (OHLCV)

Strategies that need candles read from local data. The bot does **not** download data at runtime.

1. **Optional dependency:** `pip install .[binance]` or `pip install binance-historical-data`.
2. **Sync data** (with venv activated):

   ```bash
   python scripts/sync_binance_historical.py --data-dir data/binance --interval 1h --interval 4h
   ```

   Default tickers = full Roostoo tradeable universe. Limit with `--tickers BTC,ETH,SOL`. Use `--update` to update existing files. See `--help`.
3. **Point the bot:** set `BINANCE_DATA_DIR=data/binance` in `.env` (same path as `--data-dir`).

---

### Backtest

Run a backtest from the configured strategy and Binance OHLCV; prints a performance report to stdout.

```bash
python scripts/run_backtest.py --config config.yaml --data-dir data/binance [--start-date 2025-10-01] [--end-date 2026-03-18] [--initial-balance 50000]
```

- `--config` — Path to YAML (default `config.yaml` or `BOT_CONFIG_PATH`).
- `--data-dir` — Binance OHLCV directory (default `BINANCE_DATA_DIR`).
- `--start-date`, `--end-date` — Optional; otherwise uses config backtest section or full data range.
- `--initial-balance` — Optional; otherwise from config or 10000.
- `--exclude-pairs` — Comma-separated pairs to exclude (overrides `strategy.exclude_pairs` and `BOT_EXCLUDE_PAIRS`).
- Ticker exclusion also uses **`strategy.exclude_pairs`** in config and **`BOT_EXCLUDE_PAIRS`** in `.env` (same as live bot).
- Set `BOT_STRATEGY` in env (or in .env) to choose the strategy to backtest.
- **Fee simulation** — The backtest deducts trading fees on every simulated fill using the rates from `execution.fees` (default: 10 bps market, 5 bps limit). The performance report includes total fees paid. Strategy dead-zone and buy-qty adjustments are also active during backtesting.

---

### Dashboard

Read-only web UI to monitor your Roostoo account (balance, pending orders, server time, recent orders, ticker). Uses the same SDK; API keys stay on the server. **No trading** from the dashboard (competition rules).

1. Dependencies are in `requirements.txt` (FastAPI, uvicorn).
2. Set credentials in `.env` (same as bot). Use `DASHBOARD_USE_LIVE=true` to use live credentials.
3. From repo root with venv activated:

   ```bash
   uvicorn dashboard.server:app --reload --port 8000
   ```

4. Open [http://localhost:8000/dashboard](http://localhost:8000/dashboard).

**Vercel:** Connect the repo; set env vars (e.g. `ROOSTOO_TEST_API_KEY`, `ROOSTOO_TEST_SECRET_KEY`). Build: install `pip install -r requirements.txt`; entrypoint is in `pyproject.toml` (`app = "dashboard.server:app"`).

---

## Risk and kill switch

- **Drawdown-based de-risking** (`bot/risk.py`): Exposure is automatically reduced as the portfolio draws down from its peak, with configurable thresholds.
- **Kill switch** (`kill_switch_check`): Halts or de-risks the bot on consecutive API errors, clock drift, or extreme market moves.
- **Regime filter**: BTC MA20 trend filter determines risk-on/risk-off. The throttled strategy uses a three-tier regime (strong/soft/off) with sub-daily evaluation (default every 6 hours, configurable via `regime.regime_eval_hours`). When evaluation runs sub-daily, hourly price closes are used for the MA computation.
- **Breakout fast-entry**: When in `risk_off`, if the live BTC price exceeds the daily MA20 by a configurable threshold (default 2%), the bot immediately enters `risk_on_soft` at reduced exposure (default 35%) and triggers a re-rank. This reduces upside entry lag from ~24 hours to ~5 minutes for strong breakouts. A cooldown (default 60 min) prevents repeated triggers. See `regime.breakout_threshold_pct`, `regime.breakout_exposure`, and `regime.breakout_cooldown_min` in `config.yaml`.
- Config: see `strategy.risk` and `strategy.regime` in `config.yaml`.

---

## Roostoo SDK (direct use)

```python
from roostoo import RoostooClient, RoostooAPIError

client = RoostooClient()  # uses ROOSTOO_API_KEY, ROOSTOO_SECRET_KEY, optional ROOSTOO_BASE_URL

# Public
client.get_server_time()
client.get_exchange_info()
client.get_ticker()           # all pairs
client.get_ticker("BTC/USD")  # one pair

# Signed
client.get_balance()
client.get_pending_count()
client.place_order("BNB/USD", "BUY", 1)  # MARKET
client.place_order("BTC/USD", "BUY", 0.01, order_type="LIMIT", price=95000)
client.query_order(pair="BTC/USD", pending_only=False)
client.cancel_order(pair="BNB/USD")
```

On API errors the client raises `RoostooAPIError` with message, optional `status_code`, `response_body`, and `raw`.

---

## Tests

With the repo venv activated:

```bash
pip install pytest   # if not already
python -m pytest tests/ -v
```

---

## API reference

- [Roostoo Public API docs](https://github.com/roostoo/Roostoo-API-Documents)
- [Hackathon guide (AWS, launch bot)](https://roostoo.notion.site/Hackathon-Guide-How-to-Sign-In-AWS-and-Launch-Your-Bot-309ba22fed798071b4dde6d1e8666816)
- [Problem statement (SG vs HK Quant Hackathon)](https://roostoo.notion.site/Problem-Statement-SG-vs-HK-University-Web3-Quant-Hackathon-309ba22fed7980a79da6d8a08b5216c9)
