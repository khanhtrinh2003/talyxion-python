# Changelog

## 0.1.0 — initial release

- Sync `Talyxion` client with API key auth.
- Resources: `signals`, `screener`, `datafields`, `ticker`, `rates`, `simulations`, `status`.
- Streaming: `sim_progress`, `feed_events` via WebSocket.
- Pydantic v2 models with optional pandas conversion.
- Typed exceptions mapped from backend error codes.
- Built-in retry with exponential backoff for 5xx + connection errors.
