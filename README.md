# Talyxion Python SDK

Official sync Python client for the [Talyxion](https://talyxion.com) REST API and realtime streams.

```bash
pip install talyxion
```

## Quick start

```python
from talyxion import Talyxion

client = Talyxion(api_key="tk_...")  # or set TALYXION_API_KEY

# Daily trading signals
page = client.signals.list(date="2026-04-27", asset_class="crypto", min_conviction=0.7)
for sig in page.items:
    print(sig.ticker, sig.side, sig.conviction)

# Ticker snapshot
info = client.ticker("VIC").info()
print(info.latest_signal.entry_price, info.stats.win_rate)

# Rates terminal
snapshot = client.rates.snapshot()
ten_year = client.rates.series("DGS10")

# Run an alpha simulation and stream progress
for event in client.stream.sim_progress(task_id):
    print(event.progress, event.message)
    if event.status in ("done", "error"):
        break
```

## Authentication

Pass your API key via constructor or the `TALYXION_API_KEY` env var. Keys are
issued from the Talyxion dashboard and require an `api` or `institutional`
subscription tier.

## Resources (v0.1)

| Namespace | Methods |
|---|---|
| `client.status()` | API health + key info |
| `client.signals` | `list`, `history` |
| `client.screener` | `run` |
| `client.datafields` | `list`, `get(key)` |
| `client.ticker(t)` | `info` |
| `client.rates` | `snapshot`, `series`, `suggest`, `yahoo` |
| `client.simulations` | `get(task_id)`, `wait(task_id)` |
| `client.stream` | `sim_progress(task_id)`, `feed_events()` |

## Errors

All exceptions inherit from `TalyxionError`. Common subclasses:

- `TalyxionAuthError` — missing/invalid/expired key (401)
- `TalyxionTierError` — subscription tier insufficient (402)
- `TalyxionPermissionError` — scope or IP denied (403)
- `TalyxionNotFoundError` — resource missing (404)
- `TalyxionRateLimitError` — IP or daily quota exceeded (429); has `.retry_after`
- `TalyxionServerError` — backend 5xx

## Optional pandas helpers

```bash
pip install talyxion[pandas]
```

```python
df = client.signals.list(date="2026-04-27").to_dataframe()
```

## License

MIT — see [LICENSE](LICENSE).
