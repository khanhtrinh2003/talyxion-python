"""talyxion-cli — terminal client for the Talyxion thin trader.

Entry point: ``python -m talyxion.cli`` or the installed ``talyxion`` command.

The CLI talks to two parties:
  * Talyxion server (``https://talyxion.com`` by default) — via a device
    token in the OS keyring, fetches profile configs + segment targets,
    posts heartbeat + cycle reports.
  * The user's exchange (Binance/Bybit/OKX/Hyperliquid) — via ``ccxt``
    using API keys also in the OS keyring. Keys never leave the
    machine; only a SHA-256 fingerprint is registered with Talyxion.

See ``cli/main.py`` for the command router.
"""
from talyxion.cli._version import __cli_version__

__all__ = ["__cli_version__"]
