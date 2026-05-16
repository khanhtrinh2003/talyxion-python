# Talyxion Python SDK

[![PyPI](https://img.shields.io/pypi/v/talyxion.svg)](https://pypi.org/project/talyxion/)
[![Python](https://img.shields.io/pypi/pyversions/talyxion.svg)](https://pypi.org/project/talyxion/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Official Python client for the [Talyxion](https://talyxion.com) platform — a
quant-research and algorithmic-trading stack built around three surfaces:

1. **Alpha research** — write expressions, run regular/super simulations,
   stream progress, and run authoritative overfit checks.
2. **Trading desk** — store exchange credentials in the OS keyring, create
   simulation/live profiles, activate them, and stream cycle history.
3. **Marketplace** — discover vetted alphas, buy with VND credits, list your
   own alphas for sale, and top-up your wallet via VietQR.

The package ships with both a typed synchronous client (`talyxion.Talyxion`)
and an interactive REPL CLI (`talyxion`) used by traders to run the daemon
that fires alpha cycles against their exchange accounts.

- **REST + WebSocket API reference:** <https://talyxion.com/api/docs/>
- **Homepage:** <https://talyxion.com>
- **Source:** <https://github.com/khanhtrinh2003/talyxion-python>

---

## Table of contents

- [Installation](#installation)
- [Authentication](#authentication)
- [Quick start](#quick-start)
- [Alpha research](#alpha-research)
- [Trading desk](#trading-desk)
- [Marketplace + wallet](#marketplace--wallet)
- [Realtime streaming](#realtime-streaming)
- [Interactive CLI](#interactive-cli-talyxion)
- [Resource reference](#resource-reference)
- [Pandas integration](#pandas-integration)
- [Error handling](#error-handling)
- [Configuration](#configuration)
- [Type safety](#type-safety)
- [Versioning + compatibility](#versioning--compatibility)
- [Development](#development)
- [License](#license)

---

## Installation

The SDK supports Python 3.10+ on macOS, Linux, and Windows 10+.

```bash
pip install talyxion                 # core client (HTTP + WebSocket)
pip install "talyxion[pandas]"       # adds pandas helpers (.to_dataframe, .to_pandas)
pip install "talyxion[cli]"          # adds the interactive REPL + keyring
pip install "talyxion[pandas,cli]"   # everything
```

Two console scripts are installed:

| Script             | Purpose                                                                  |
| ------------------ | ------------------------------------------------------------------------ |
| `talyxion`         | Interactive REPL (live slash-menu, dashboards, order entry).             |
| `talyxion-runner`  | Headless daemon for systemd / launchd / Windows scheduled tasks.         |

## Authentication

API keys are issued from the Talyxion dashboard and require the `api` or
`institutional` subscription tier. Pass the key via the constructor or the
`TALYXION_API_KEY` environment variable:

```bash
export TALYXION_API_KEY=tk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

```python
from talyxion import Talyxion

tlx = Talyxion()                              # picks up TALYXION_API_KEY
tlx = Talyxion(api_key="tk_...")              # or pass explicitly
tlx = Talyxion(base_url="http://localhost:8000")  # local dev override
```

The CLI stores credentials in the OS keyring (Keychain / Secret Service /
Credential Manager) — never in plain-text dotfiles. Run `talyxion` and use
`/login` to authenticate interactively.

## Quick start

```python
from talyxion import Backtest, Talyxion

tlx = Talyxion(api_key="tk_...")

# 1. Build + simulate a regular alpha
result = (
    Backtest(region="crypto_trade", universe="TOP19", decay=4, save=True)
    .alpha("rank(close - ts_mean(close, 20)) * volume", delay=1)
    .simulate(tlx)
)
print(result.alpha_id, result.sharpe, result.passes_overfit())

# 2. Deploy the alpha to a trading profile (simulation mode)
profile = tlx.trading.profiles.create(
    name="my_btc_v1",
    alpha_id=result.alpha_id,
    exchange="binance",
    credential_id=42,
    mode="simulation",
    leverage=2,
    book_usd=500,
).activate()

# 3. Discover vetted alphas on the marketplace
for listing in tlx.market.search(min_sharpe=2.0, limit=10):
    print(listing.title, listing.price_vnd("lifetime"))
```

Three runnable examples ship under [`examples/`](examples/):

- [`01_alpha_research.py`](examples/01_alpha_research.py) — build, simulate, overfit-check, save.
- [`02_trading_desk.py`](examples/02_trading_desk.py) — credentials, profiles, cycles, live positions.
- [`03_marketplace.py`](examples/03_marketplace.py) — search, wallet top-up, buy, seller stats.

## Alpha research

The `Backtest` fluent builder mirrors the syntax of the web playground.
Every chain ends with `.simulate(client)`, which submits a task, polls the
simulation API, and returns a typed `SimulationResult`.

```python
from talyxion import Backtest

# Regular alpha
result = (
    Backtest(
        region="crypto_trade",
        universe="TOP19",
        decay=4,
        truncation=0.08,
        neutralization="market",
        save=True,
    )
    .alpha("rank(close - ts_mean(close, 20)) * volume", delay=1)
    .simulate(tlx)
)

# Super alpha (linear combo of existing alpha_ids)
super_result = (
    Backtest(region="crypto_trade", universe="TOP19")
    .super_alpha(["alphaA", "alphaB"], combo="0.5 * a + 0.5 * b")
    .simulate(tlx)
)

# Authoritative overfit report (Ladder-Sharpe autocorrelation, IS/OS split, …)
report = tlx.alphas.overfit(result.alpha_id)
for check in report.checks:
    print(check.label, check.passed, check.result)
print("passes_all:", report.passes_all)

# PnL series as a pandas Series (requires the [pandas] extra)
equity = tlx.alphas.pnl(result.alpha_id).to_pandas()

# Browse your saved library
for alpha in tlx.alphas.list(mine_only=True, sort="sharpe", order="desc", limit=20):
    print(alpha.id, alpha.region, alpha.sharpe)
```

## Trading desk

Credentials, profiles, and cycle history live under `client.trading`. Each
profile carries its own `mode` (`simulation` or `live`), exchange, leverage,
book size, and cycle interval. Credentials are validated server-side before
they can be attached to a live profile.

```python
# Register an exchange credential (encrypted server-side)
cred = tlx.trading.credentials.create(
    exchange="binance",
    label="main",
    api_key="...",
    api_secret="...",
)
tlx.trading.credentials.validate(cred.id)

# Create + activate a profile
profile = tlx.trading.profiles.create(
    name="my_btc_v1",
    alpha_id="alpha_xxx",
    exchange="binance",
    credential_id=cred.id,
    mode="simulation",
    leverage=2,
    book_usd=500,
    cycle_interval_sec=300,
    max_drawdown_pct=15,
).activate()

# Inspect the last N cycle runs
for cycle in profile.cycles.tail(20):
    print(cycle.started_at, cycle.outcome, cycle.trades_filled, "/", cycle.trades_attempted)

# Live positions snapshot
snap = profile.positions()
print(snap.wallet_balance, snap.unrealized_pnl, snap.position_count)
for pos in snap.positions:
    print(pos.symbol, pos.side, pos.qty, pos.entry_price, pos.unrealized_pnl)
```

## Marketplace + wallet

```python
# Search vetted listings
page = tlx.market.search(min_sharpe=2.0, sort="sharpe", limit=10)
for listing in page:
    print(listing.slug, listing.title, listing.price_vnd("lifetime"))

# Wallet balance (VND credits)
wallet = tlx.wallet.balance()
print(wallet.credits_balance, wallet.lifetime_topup_credits)

# Top-up via VietQR — returns a QR URL the user scans with their banking app
topup = tlx.wallet.topup(amount_vnd=200_000)
print(topup.qr_url, topup.memo, topup.bank)

# Buy a listing (charges your wallet in VND)
purchase = tlx.market.buy(slug="zfgPCLOE7fUq", license_type="lifetime")
print(purchase.credits_charged)

# Your library (purchased + owned + gifted)
for lic in tlx.market.library():
    print(lic.alpha_id, lic.license_type, lic.expires_at, lic.source)

# Seller stats (if you've listed alphas for sale)
print(tlx.market.seller_stats())
```

## Realtime streaming

The SDK speaks two WebSocket channels via `client.stream`:

```python
# Per-task simulation progress
for event in tlx.stream.sim_progress(task_id):
    print(event.progress, event.status, event.message)
    if event.status in ("done", "error"):
        break

# Account-wide feed events (fills, cycle starts, drawdown alerts, …)
for event in tlx.stream.feed_events():
    print(event.type, event.payload)
```

Both iterators reconnect transparently on transient failures and surface
auth/permission errors as the same exception hierarchy as the REST client.

## Interactive CLI (`talyxion`)

Run `talyxion` in a terminal for an interactive REPL with a Claude-Code-style
as-you-type slash menu. The most-used commands:

| Command           | Purpose                                                       |
| ----------------- | ------------------------------------------------------------- |
| `/login`          | OAuth-style device token flow; stores key in OS keyring.      |
| `/whoami`, `/tier`| Show authenticated user + active subscription quotas.         |
| `/add`            | Register an exchange credential (validates before storing).   |
| `/list creds`     | Show stored credentials and their server-side status.         |
| `/list profiles`  | Show all trading profiles (status, mode, exchange).           |
| `/run`            | Start the cycle daemon for one or all active profiles.        |
| `/run -d`         | Detach the daemon (`setsid` on POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows). |
| `/status`, `/logs`| Inspect the running daemon (`/logs -f` tails the log file).   |
| `/dashboard`      | Live htop-style TUI: profiles, positions, cycle outcomes.     |
| `/positions`      | One-shot positions snapshot for a profile.                    |
| `/portfolio`, `/balance` | Cross-profile aggregates.                              |
| `/order`, `/tape` | Manual order entry + tape view for a credential.              |
| `/doctor`         | Self-diagnose env / keyring / connectivity issues.            |
| `/update`         | Self-update the SDK package.                                  |
| `/help`, `/exit`  | Slash-menu help and quit.                                     |

For headless deployments (cron / systemd / launchd / Windows Task Scheduler)
use `talyxion-runner` directly:

```bash
talyxion-runner --once                  # one cycle across all active profiles
talyxion-runner --profile 42            # run a single profile in the foreground
talyxion-runner --background            # detach and survive the parent shell
talyxion-runner --dry-run               # log intended trades without placing them
```

## Resource reference

| Namespace                | Methods                                                                                                    |
| ------------------------ | ---------------------------------------------------------------------------------------------------------- |
| `client.status()`        | API health, server version, authenticated key info.                                                        |
| `client.signals`         | `list`, `history`                                                                                          |
| `client.screener`        | `run`                                                                                                      |
| `client.datafields`      | `list`, `get(key)`                                                                                         |
| `client.ticker(t)`       | `info` (latest signal, stats, win rate)                                                                    |
| `client.rates`           | `snapshot`, `series`, `suggest`, `yahoo`                                                                   |
| `client.simulations`     | `get(task_id)`, `wait(task_id)`                                                                            |
| `client.alphas`          | `list`, `get`, `overfit`, `pnl`, `delete`, `rename`                                                        |
| `client.trading.credentials` | `list`, `create`, `validate`, `delete`                                                                 |
| `client.trading.profiles`    | `list`, `create`, `get`, `update`, `activate`, `pause`, `delete`                                       |
| `profile.cycles`         | `tail(n)`, `list(...)`                                                                                     |
| `profile.positions()`    | Live positions snapshot.                                                                                   |
| `client.market`          | `search`, `get(slug)`, `buy`, `library`, `list_for_sale`, `seller_stats`                                   |
| `client.wallet`          | `balance`, `topup`, `transactions`                                                                         |
| `client.stream`          | `sim_progress(task_id)`, `feed_events()`                                                                   |

Every list method returns a typed `Page[T]` that is iterable and exposes
`.items`, `.pagination`, and `.to_dataframe()` (with the `pandas` extra).

Full request/response schemas, rate limits, and pagination semantics are
documented in the OpenAPI reference at <https://talyxion.com/api/docs/>.

## Pandas integration

```bash
pip install "talyxion[pandas]"
```

```python
signals_df  = tlx.signals.list(date="2026-04-27").to_dataframe()
equity_ser  = tlx.alphas.pnl("alpha_xxx").to_pandas()
positions_df = profile.positions().to_dataframe()
```

The pandas dependency is **optional**. Without it the SDK still returns
fully-typed Pydantic models — `.to_dataframe()` / `.to_pandas()` raise an
informative `ImportError` only when called.

## Error handling

All exceptions inherit from `TalyxionError`. Catch the base class to handle
every API-side failure uniformly, or narrow to a subclass:

| Exception                     | HTTP / Cause                                              |
| ----------------------------- | --------------------------------------------------------- |
| `TalyxionAuthError`           | 401 — missing, invalid, or expired API key.               |
| `TalyxionTierError`           | 402 — subscription tier does not include this endpoint.   |
| `TalyxionPermissionError`     | 403 — scope, IP, or per-key restriction denied the call.  |
| `TalyxionNotFoundError`       | 404 — resource id not found.                              |
| `TalyxionBadRequestError`     | 400 / 422 — validation failure (carries `.errors` dict).  |
| `TalyxionRateLimitError`      | 429 — IP or daily quota exceeded; has `.retry_after`.     |
| `TalyxionServerError`         | 5xx — backend error (already retried with backoff).       |
| `TalyxionConnectionError`     | Transport — DNS, TCP, TLS, or WebSocket failure.          |
| `TalyxionResponseError`       | Schema mismatch — server returned an unexpected payload.  |

```python
from talyxion import Talyxion, TalyxionRateLimitError, TalyxionTierError

try:
    tlx.signals.list(date="2026-04-27")
except TalyxionRateLimitError as exc:
    print(f"retry in {exc.retry_after}s")
except TalyxionTierError:
    print("upgrade subscription tier to call this endpoint")
```

## Configuration

| Constructor arg | Env var                 | Default                       |
| --------------- | ----------------------- | ----------------------------- |
| `api_key`       | `TALYXION_API_KEY`      | _(required)_                  |
| `base_url`      | `TALYXION_BASE_URL`     | `https://talyxion.com`        |
| `timeout`       | `TALYXION_TIMEOUT`      | `30.0` seconds                |
| `max_retries`   | `TALYXION_MAX_RETRIES`  | `3` (5xx + 429 only)          |
| `backoff_base`  | `TALYXION_BACKOFF_BASE` | `0.5` seconds                 |
| `transport`     | —                       | `httpx`'s default transport   |

Use `transport=` to inject a custom `httpx.BaseTransport` for testing or for
inserting middleware (telemetry, signing proxies, etc.).

```python
with Talyxion(api_key="tk_...") as tlx:        # context-managed close
    tlx.status()
```

## Type safety

The package ships a `py.typed` marker and is strict-mypy clean. Every model
returned from the API is a `pydantic.BaseModel` subclass, so editors and
type-checkers can autocomplete every field. `Backtest`, `Page[T]`, and the
resource handles are generic and preserve their item types through chains.

## Versioning + compatibility

Current release: **0.4.4** (May 2026) — first-class macOS / Linux / Windows
10+ parity for both the SDK and CLI. See [`CHANGELOG.md`](CHANGELOG.md) for
the full release history.

- **Python:** 3.10, 3.11, 3.12, 3.13
- **OS:** macOS 12+, Linux (glibc 2.31+), Windows 10+
- **Stability:** pre-1.0 — minor versions may include breaking changes; the
  changelog calls them out explicitly. Pin to `talyxion~=0.4.0` in
  production.

## Development

```bash
git clone https://github.com/khanhtrinh2003/talyxion-python
cd talyxion-python
pip install -e ".[dev,pandas,cli]"
pytest                 # full test suite (uses respx to mock the API)
mypy src/talyxion      # strict type-check
ruff check src tests   # lint
```

CI runs the test matrix on Python 3.10–3.13 across macOS, Linux, and Windows
runners. Contributions are welcome — please open an issue first for anything
larger than a bug-fix.

## License

MIT — see [LICENSE](LICENSE).

Built and maintained by the [Talyxion](https://talyxion.com) team.
Questions? <support@talyxion.com>
