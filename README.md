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

- **Strategies**: `example` (place a MARKET buy every N ticks for testing); `cross_sectional_momentum` (weekly rebalance, top N by 90d return, 200 MA filter); `momentum_20_50` (EMA 20/50 crossover, ATR trailing stop); `bollinger_rsi` (BB + RSI oversold, 4H regime filter). Add more under `bot/strategies/` and register in `bot/strategies/__init__.py`. Example params in `.env.example`.
- **Config**: Env vars in `.env` or environment; see `.env.example`. CLI: `--strategy`, `--dry-run`, `--tick-seconds`, `--env-file`.
- **Hackathon (AWS)**: On the EC2 instance, create and activate the repo venv (`./scripts/setup_venv.sh` then `source venv/bin/activate`), then run the bot. Use `tmux` so the bot keeps running after you disconnect; see the [Roostoo hackathon guide](https://roostoo.notion.site/Hackathon-Guide-How-to-Sign-In-AWS-and-Launch-Your-Bot-309ba22fed798071b4dde6d1e8666816).

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
