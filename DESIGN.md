# Talyxion Python SDK — Design

Audience: quant researchers building alphas + traders deploying them on
real exchanges via Talyxion's trading desk.

## Goals

1. **Alpha research feels like sklearn** — declarative `Alpha`/`SuperAlpha`
   objects, `.simulate()`/`.overfit_checks()` methods, pandas Series for PnL.
2. **Trading desk is one Python script** — pick an alpha, point at a
   credential, set risk caps, `.activate()`. Stream cycles + positions live.
3. **Marketplace is one method** — `tlx.market.search()` returns listings;
   `tlx.market.buy(slug)` returns a `License` you can use right away.
4. **Type-safe by default** — Pydantic v2 models for every payload; IDE
   autocomplete works in VS Code/Cursor without docs.
5. **Server-side enforcement preserved** — every SDK call hits the same
   gated `/api/v1/` endpoints used by the web UI, so quotas, tier checks,
   and license verification still apply. No back-doors.

## Architecture

```
sdk/python/src/talyxion/
├── client.py              # Talyxion(api_key=…) — root client, holds resources
├── _config.py             # env-var resolver (TALYXION_API_KEY, TALYXION_BASE_URL)
├── _http.py               # httpx + retry + auth + error mapping
├── streaming.py           # WebSocket / SSE for live cycles, listings
├── errors.py              # exception hierarchy
├── models/                # Pydantic models per domain
│   ├── alphas.py          # Alpha, AlphaDetail, OverfitCheck, SimulationResult
│   ├── trading.py         # Profile, Credential, CycleRun, OrderEvent, Position
│   ├── market.py          # Listing, License, Purchase, ResearcherProfile
│   └── …                  # signals, screener, ticker (already exist)
├── resources/
│   ├── alphas.py          # tlx.alphas.list/get/simulate_regular/simulate_super
│   ├── trading.py         # tlx.trading.profiles.* + tlx.trading.credentials.*
│   ├── market.py          # tlx.market.search/buy/library/list_for_sale
│   └── …                  # signals, screener, datafields (already exist)
└── _builders/             # high-level wrappers: Alpha, Profile, Listing
    ├── alpha.py
    ├── profile.py
    └── listing.py
```

The **resource** layer mirrors REST endpoints 1:1 — `client.alphas.list()`
returns a `Page[Alpha]`. The **builder** layer wraps them with fluent helpers
— `Alpha("rank(close)").simulate()` creates the request, calls the resource,
and returns a parsed result with helper properties (`.passes_overfit`,
`.pnl_series`).

## API surface — by use case

### Use case 1: Alpha research

```python
from talyxion import Talyxion, Alpha

tlx = Talyxion()  # reads TALYXION_API_KEY

# Fluent builder
alpha = (
    Alpha("rank(close - ts_mean(close, 20)) * volume")
    .region("crypto_trade")
    .universe("TOP19")
    .delay(1)
    .decay(4)
    .truncation(0.08)
    .neutralization("market")
)

result = alpha.simulate(client=tlx)        # SimulationResult
print(result.alpha_id, result.sharpe, result.fitness)
print(result.overfit_checks)               # list[OverfitCheck]
print(result.passes_overfit())             # bool
df = result.pnl.to_dataframe()             # pandas DataFrame [date, equity, drawdown]

# Save to your alpha library (auto-attaches author=current_user)
saved = result.save()                      # AlphaDetail with id

# Browse your alphas
for a in tlx.alphas.list(mine_only=True, min_sharpe=2.0, limit=50):
    print(a.id, a.sharpe, a.region, a.tags)

# Get full detail
detail = tlx.alphas.get("aPE6COnE")
print(detail.code, detail.pnl_series, detail.overfit_payload)

# Super alpha (combo of Regular alphas)
from talyxion import SuperAlpha
super_a = SuperAlpha(
    selections=["aPE6COnE", "TWs0pveY"],
    combo="0.6 * a + 0.4 * b",
).region("crypto_trade")
super_result = super_a.simulate(client=tlx)
```

### Use case 2: Trading desk

```python
from talyxion import Talyxion

tlx = Talyxion()

# List existing trading credentials (Binance, Bybit, Hyperliquid)
for cred in tlx.trading.credentials.list():
    print(cred.id, cred.exchange, cred.label, cred.validation_status)

# Add a new Binance credential
cred = tlx.trading.credentials.create(
    exchange="binance",
    label="main",
    api_key="…",
    api_secret="…",   # encrypted server-side, never round-trips
)
cred.validate()                            # probe exchange + persist status

# Create + activate a trading profile
profile = tlx.trading.profiles.create(
    name="btc_meanrev_v1",
    alpha_id="aPE6COnE",                   # from your library OR purchased
    credential_id=cred.id,
    exchange="binance",
    market_type="futures",
    mode="simulation",                     # or "live" if PRO_PLUS+
    leverage=2,
    book_usd=500,
    cycle_interval_sec=300,
    max_drawdown_pct=15,
)
profile.activate()                         # status: draft → active

# Stream live cycles
for cycle in profile.cycles.stream():      # yields CycleRun events
    print(cycle.started_at, cycle.outcome, cycle.pnl_realized)
    if cycle.outcome == "auth_fail":
        profile.pause(reason="manual")
        break

# Read current positions (calls exchange)
positions = profile.positions()
for pos in positions:
    print(pos.symbol, pos.side, pos.size, pos.unrealized_pnl)

# Lifecycle
profile.pause(reason="manual")
profile.resume()
profile.archive()
```

### Use case 3: Marketplace

```python
from talyxion import Talyxion

tlx = Talyxion()

# Browse public listings
for listing in tlx.market.search(
    region="crypto",
    min_sharpe=2.0,
    max_price_vnd=20_000_000,
    license="lifetime",
    sort="popular",
    limit=20,
):
    print(listing.title, listing.snapshot.sharpe, listing.price_vnd("lifetime"))

# Get details (incl. PnL chart data)
detail = tlx.market.get("zfgPCLOE7fUq")    # by slug
print(detail.description_md, detail.snapshot, detail.reviews)

# Buy
purchase = tlx.market.buy(
    slug="zfgPCLOE7fUq",
    license_type="lifetime",                # or "monthly", "yearly"
)
print(purchase.license.alpha_id, purchase.credits_charged)

# Browse my licenses (own + purchased + admin-granted)
for lic in tlx.market.library():
    print(lic.alpha_id, lic.source, lic.expires_at)

# List my own alpha for sale
listing = tlx.market.list_for_sale(
    alpha_id="aPE6COnE",
    title="BTC mean-reversion (Sharpe 2.7)",
    description_md="…markdown…",
    tags="btc,futures,mean-rev",
    lifetime_price_vnd=10_000_000,
    monthly_price_vnd=2_000_000,
)
print(listing.slug, listing.url)

# Seller dashboard data
stats = tlx.market.seller.stats()
print(stats.total_revenue_vnd, stats.lifetime_sales, stats.pending_payout_vnd)
```

### Use case 4: Wallet (credits)

```python
balance = tlx.wallet.balance()             # WalletAccount
print(balance.credits_balance)

# Top up (returns QR url + memo for VietQR scan)
topup = tlx.wallet.topup(amount_vnd=1_000_000)
print(topup.qr_url, topup.memo)            # display QR to user

# Recent ledger
for tx in tlx.wallet.transactions(limit=30):
    print(tx.kind, tx.amount, tx.balance_after)
```

## Authentication & quota model

- All endpoints require `Authorization: Bearer tk_…` (API tier or higher).
- Per-key scope flags: `signals`, `alphas`, `trading`, `market`, `wallet`.
- Rate limit: 60 req/min default, configurable per key.
- Tier gates re-checked server-side: simulating an alpha requires PRO; live
  trading profiles require PRO_PLUS; etc.

## Error handling

```python
from talyxion import (
    TalyxionAuthError,        # 401 — bad/expired key
    TalyxionPermissionError,  # 403 — scope or IP block
    TalyxionTierError,        # 402 — paywall (e.g. tier=FREE on PRO endpoint)
    TalyxionNotFoundError,    # 404
    TalyxionBadRequestError,  # 400 — validation
    TalyxionRateLimitError,   # 429 — has .retry_after
    TalyxionServerError,      # 5xx — auto-retried with exp backoff
    TalyxionConnectionError,  # network
)

try:
    profile = tlx.trading.profiles.create(...)
except TalyxionTierError as e:
    print("Need to upgrade:", e.required_tier)
except TalyxionBadRequestError as e:
    print("Validation:", e.field_errors)  # {"alpha_id": ["No active license"]}
```

## Server-side API endpoints (v1)

All under `/api/v1/`, all Bearer-authed, all return `{data, meta}` envelope.

### Alphas
```
GET    /api/v1/alphas/                       — list (filter, sort, paginate)
GET    /api/v1/alphas/{id}/                  — detail (incl. pnl, overfit)
POST   /api/v1/alphas/simulate-regular/      — sync simulate (job id for long)
POST   /api/v1/alphas/simulate-super/        — sync super simulate
GET    /api/v1/alphas/{id}/pnl/              — PnL series JSON
GET    /api/v1/alphas/{id}/overfit/          — overfit payload + checks
```

### Trading desk
```
GET    /api/v1/trading/credentials/          — list
POST   /api/v1/trading/credentials/          — create (encrypt + validate)
POST   /api/v1/trading/credentials/{id}/validate/

GET    /api/v1/trading/profiles/             — list mine
POST   /api/v1/trading/profiles/             — create
GET    /api/v1/trading/profiles/{id}/        — detail
POST   /api/v1/trading/profiles/{id}/activate/
POST   /api/v1/trading/profiles/{id}/pause/
POST   /api/v1/trading/profiles/{id}/resume/
POST   /api/v1/trading/profiles/{id}/archive/
GET    /api/v1/trading/profiles/{id}/cycles/  — recent cycles (paginated)
GET    /api/v1/trading/profiles/{id}/positions/ — live positions (proxies exchange)
GET    /api/v1/trading/profiles/{id}/orders/  — order events
```

### Marketplace
```
GET    /api/v1/market/listings/              — search (filter + sort)
GET    /api/v1/market/listings/{slug}/       — detail
POST   /api/v1/market/listings/{slug}/buy/   — purchase via wallet
POST   /api/v1/market/listings/               — list for sale (sellers)
PATCH  /api/v1/market/listings/{slug}/       — edit listing
GET    /api/v1/market/library/               — buyer's licenses
GET    /api/v1/market/seller/stats/          — seller dashboard data
```

### Wallet
```
GET    /api/v1/wallet/                       — balance + lifetime totals
GET    /api/v1/wallet/transactions/          — ledger
POST   /api/v1/wallet/topup/                 — request (returns QR url)
GET    /api/v1/wallet/topup/{id}/            — status
POST   /api/v1/wallet/payout/                — seller cashout request
```

## Streaming (SSE/WebSocket)

```python
# Listen for cycle events on a profile (SSE)
for event in tlx.stream.profile(profile_id=42, types=["cycle", "order"]):
    print(event.type, event.payload)

# Listen for new market listings
for listing in tlx.stream.market_new_listings(region="crypto"):
    if listing.snapshot.sharpe > 2.5:
        print("Alert: hot listing", listing.slug)
```

Underlying transport: existing Channels WebSocket at `/ws/sdk-stream/`.
Authenticated via `?api_key=tk_...` query param (rotated, scoped).

## Testing the SDK

- `tests/test_*.py` use httpx mocks to verify resource methods → request shape.
- Integration tests (opt-in via `TALYXION_INTEGRATION=1`) hit a staging URL.
- Examples in `examples/` are self-contained scripts; CI runs them against
  staging to catch breaking changes.

## Versioning

- SDK uses semver. v0.x: breaking changes possible per minor.
- Server bumps `/api/v1/` only on backwards-compatible adds. Deprecations
  carry `Deprecation` + `Sunset` HTTP headers and a `meta.deprecation` body
  field for 90 days.

## Out of scope (v1)

- Async SDK (`AsyncTalyxion`) — Phase 2 once sync is stable.
- Notebook helpers (matplotlib auto-render PnL) — Phase 2 in `talyxion.lab`.
- CLI (`talyxion alphas list`) — Phase 3 once SDK stabilizes.
