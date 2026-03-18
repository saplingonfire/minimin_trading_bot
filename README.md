# Roostoo Public API SDK (Python)

Python client for the [Roostoo Public API (v3)](https://github.com/roostoo/Roostoo-API-Documents).

## Install

The project uses a **virtual environment in the repo** (`venv/`). All commands (run_bot, pytest, scripts) should be run with this venv activated.

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

**Manual setup** (if you prefer):

```bash
python3 -m venv venv
source venv/bin/activate         # or venv\Scripts\activate on Windows
pip install -e .
pip install -r requirements.txt
```

**Manual setup without running scripts** (no `./scripts/setup_venv.sh`, no `source activate`). From repo root, run these one at a time:

```bash
# 1. Create the venv
python3 -m venv venv

# 2. Install the project and dependencies (use the venv’s pip directly)
venv/bin/pip install -e .
venv/bin/pip install -r requirements.txt
```

To run the bot or tests without activating the venv, call the venv’s Python explicitly:

```bash
venv/bin/python scripts/run_bot.py --strategy example --dry-run
venv/bin/python -m pytest tests/ -v
```

(On Windows use `venv\Scripts\python.exe` and `venv\Scripts\pip.exe` instead of `venv/bin/python` and `venv/bin/pip`.)

The `venv/` directory is gitignored; each clone creates its own.

## Configuration

Set your API credentials (from [Roostoo](https://github.com/roostoo/Roostoo-API-Documents#public-apikey--secretkey)):

- **Environment:** `ROOSTOO_API_KEY` and `ROOSTOO_SECRET_KEY`
- **Or** pass them when creating the client:

```python
from roostoo import RoostooClient

client = RoostooClient(
    api_key="YOUR_API_KEY",
    secret_key="YOUR_SECRET_KEY",
    base_url="https://mock-api.roostoo.com",  # optional, this is the default
)
```

## Usage

```python
from roostoo import RoostooClient, RoostooAPIError

client = RoostooClient()

# Public (no auth)
print(client.get_server_time())
print(client.get_exchange_info())

# Ticker (timestamp only)
print(client.get_ticker())
print(client.get_ticker("BTC/USD"))

# Signed (API key + HMAC)
print(client.get_balance())
print(client.get_pending_count())
print(client.place_order("BNB/USD", "BUY", 1))  # MARKET
print(client.place_order("BTC/USD", "BUY", 0.01, order_type="LIMIT", price=95000))
print(client.query_order(pair="BTC/USD", pending_only=False))
print(client.cancel_order(pair="BNB/USD"))
```

On HTTP or API errors (e.g. `Success: false`), the client raises `RoostooAPIError` with the message and optional `status_code`, `response_body`, and `raw` response.

## Running the bot

With the repo venv **activated** (see Install above):

```bash
cp .env.example .env
# Edit .env: set ROOSTOO_API_KEY, ROOSTOO_SECRET_KEY, BOT_STRATEGY
python scripts/run_bot.py --strategy example [--dry-run]
```

- **Strategies**: `example` (place a MARKET buy every N ticks for testing); `hybrid_trend_cross_sectional` (recommended for competition: BTC MA20 regime + cross-sectional momentum, inverse-vol top-N); `cross_sectional_momentum` (weekly rebalance, top N by 90d return, 200 MA filter); `momentum_20_50` (EMA 20/50 crossover, ATR trailing stop); `bollinger_rsi` (BB + RSI oversold, 4H regime filter). Add more under `bot/strategies/` and register in `bot/strategies/__init__.py`. Example params in `.env.example`.
- **Config**: Env vars in `.env` or environment; see `.env.example`. CLI: `--strategy`, `--dry-run`, `--tick-seconds`, `--env-file`.
- **Hackathon (AWS)**: On the EC2 instance, create and activate the repo venv (`./scripts/setup_venv.sh` then `source venv/bin/activate`), then run the bot. Use `tmux` so the bot keeps running after you disconnect; see the [Roostoo hackathon guide](https://roostoo.notion.site/Hackathon-Guide-How-to-Sign-In-AWS-and-Launch-Your-Bot-309ba22fed798071b4dde6d1e8666816).

### Competition deployment (SG vs HK Quant Hackathon)

For the [Roostoo SG vs HK University Web3 Quant Hackathon](https://roostoo.notion.site/Problem-Statement-SG-vs-HK-University-Web3-Quant-Hackathon-309ba22fed7980a79da6d8a08b5216c9), use the **hybrid_trend_cross_sectional** strategy so the bot runs with Roostoo data only (no ongoing external OHLCV):

- Set `BOT_STRATEGY=hybrid_trend_cross_sectional`.
- Set `BOT_PRICE_STORE_PATH` (or `price_store_path` in config) to a path for the price DB (e.g. `prices.db`). The bot needs ~20 days of BTC history for the regime filter; on first run it can warm up from Binance if available, or pre-fill the DB.
- The runner throttles orders for this strategy (e.g. order spacing) to stay within API limits.
- **Trade and API logging**: Each order attempt and its success/failure is logged with `order_result` / `cancel_result` so you can verify autonomous execution and audit API outcomes (required for judging).
- Other strategies (`cross_sectional_momentum`, `momentum_20_50`, `bollinger_rsi`) require `BINANCE_DATA_DIR` and local OHLCV; they will not place trades if that env is unset.

### Historical data (OHLCV)

Strategies that need candles (e.g. momentum, Bollinger/RSI) read from local CSV dumps. The bot does **not** download data at runtime. Use the repo **venv** when running scripts.

1. **Install the optional Binance sync dependency**: `pip install .[binance]` (or `pip install binance-historical-data`).
2. **Sync data** (run manually or via cron, with venv activated):
   ```bash
   python scripts/sync_binance_historical.py --data-dir data/binance --interval 1h --interval 4h
   ```
   Use `--tickers BTC,ETH,BNB` to limit pairs; see `--help` for options.
3. **Point the bot at the dump directory**: set `BINANCE_DATA_DIR=data/binance` in `.env` (use the same path as `--data-dir`). The runner reads `BINANCE_DATA_DIR` and passes it to the OHLCV provider. If `BINANCE_DATA_DIR` is not set, `context.ohlcv_provider` is `None` and strategies that need OHLCV should no-op.

## Tests

With the repo **venv** activated:

```bash
pip install pytest   # if not already installed
python -m pytest tests/ -v
```

## API reference

- [Roostoo Public API docs](https://github.com/roostoo/Roostoo-API-Documents)
