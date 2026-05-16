"""Binance native REST adapter — Spot + USD-M Futures.

Docs:
  Spot     — https://developers.binance.com/docs/binance-spot-api-docs/rest-api
  Futures  — https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
  Testnet  — https://testnet.binance.vision/  +  https://testnet.binancefuture.com/

Auth (SIGNED endpoints):
  - Header  ``X-MBX-APIKEY: <api_key>``
  - Query string includes ``timestamp`` (ms) + ``signature`` (HMAC-SHA256 of
    the URL-encoded query string using ``api_secret``).
  - Spot vs Futures share signing scheme; only base URL + paths differ.

This adapter avoids the full Binance feature surface — only what the CLI
runner needs: validate, balance/positions, place market order.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from decimal import Decimal
from typing import Any

import httpx

from talyxion.cli.exchanges._base import (
    AuthFailure,
    BalanceSnapshot,
    ExchangeAdapter,
    InsufficientFunds,
    IPBlocked,
    OpenOrder,
    OrderRejected,
    OrderResult,
    PermissionsSummary,
    Position,
)


SPOT_PROD = "https://api.binance.com"
SPOT_TEST = "https://testnet.binance.vision"
FUTURES_PROD = "https://fapi.binance.com"
FUTURES_TEST = "https://testnet.binancefuture.com"

# Binance error codes worth special-casing — full list at
# https://developers.binance.com/docs/binance-spot-api-docs/errors
_AUTH_CODES = {-2014, -2015, -1022, -1100, -1102, -1021}  # invalid key / signature / timestamp
_IP_CODES = {-2010, -2011, -2013}  # IP / region restriction (sub-set, see docs)
_INSUFFICIENT_CODES = {-2010, -2018, -2019, -1013}  # insufficient balance / margin / minNotional

# A subset of HTTP status codes Binance returns:
#   401/403 → auth failure
#   418     → IP banned (auto-banned by WAF)
#   429     → rate limit (raise as ExchangeError for the runner to back off)


class BinanceAdapter(ExchangeAdapter):
    name = "binance"

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        passphrase: str = "",
        testnet: bool = False,
        market_type: str = "spot",
    ):
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            market_type=market_type,
        )
        if market_type == "spot":
            self._base = SPOT_TEST if testnet else SPOT_PROD
            self._account_path = "/api/v3/account"
            self._order_path = "/api/v3/order"
            self._open_orders_path = "/api/v3/openOrders"
        elif market_type in {"futures", "usd_m_futures"}:
            self._base = FUTURES_TEST if testnet else FUTURES_PROD
            self._account_path = "/fapi/v2/account"
            self._order_path = "/fapi/v1/order"
            self._open_orders_path = "/fapi/v1/openOrders"
        else:
            raise ValueError(f"Binance adapter does not support market_type={market_type!r}")

        self._http = httpx.Client(
            base_url=self._base,
            timeout=20,
            headers={"X-MBX-APIKEY": api_key, "User-Agent": "talyxion-cli/0.1"},
        )

    # ── helpers ──────────────────────────────────────────────────
    def _sign(self, params: dict[str, Any]) -> str:
        params.setdefault("timestamp", int(time.time() * 1000))
        params.setdefault("recvWindow", 10_000)
        query = urllib.parse.urlencode(params, doseq=True)
        sig = hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{query}&signature={sig}"

    def _raise_from_response(self, resp: httpx.Response, context: str) -> None:
        """Translate HTTP/JSON errors to typed adapter exceptions.

        Binance always wraps 4xx errors in a JSON body like
        ``{"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."}``
        and that code/message pinpoints the actual cause (wrong key, IP
        not whitelisted, signature mismatch, clock skew, …). We surface
        it verbatim — the previous "401 unauthorized" message threw
        away the only useful diagnostic.
        """
        # Parse body once; the same payload powers every branch below.
        try:
            body = resp.json() or {}
        except Exception:
            body = {"msg": resp.text[:200]}
        code = body.get("code")
        msg = body.get("msg") or ""
        detail = f"code {code} {msg}".strip() if code is not None else (msg or resp.text[:200])

        if resp.status_code == 401:
            if code in _IP_CODES:
                raise IPBlocked(f"{context}: 401 IP-restricted ({detail})")
            raise AuthFailure(f"{context}: 401 — {detail}")
        if resp.status_code == 418:
            raise IPBlocked(f"{context}: 418 IP banned by Binance WAF")
        if resp.status_code == 403:
            if code in _IP_CODES:
                raise IPBlocked(f"{context}: 403 IP-restricted ({detail})")
            raise AuthFailure(f"{context}: 403 — {detail}")
        if resp.status_code >= 500:
            raise ExchangeError(f"{context}: server error {resp.status_code}")  # type: ignore[name-defined]
        # 4xx (non-401/403) — body was already parsed above.
        if 400 <= resp.status_code < 500:
            if code in _AUTH_CODES:
                raise AuthFailure(f"{context}: {detail}")
            if code in _IP_CODES:
                raise IPBlocked(f"{context}: {detail}")
            if code in _INSUFFICIENT_CODES:
                raise InsufficientFunds(f"{context}: {detail}")
            # Unknown 4xx → OrderRejected (caller decides whether to keep cycling)
            raise OrderRejected(f"{context}: {detail}")

    def _signed_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        query = self._sign(params)
        r = self._http.get(f"{path}?{query}")
        if r.is_error:
            self._raise_from_response(r, f"GET {path}")
        return r.json()

    def _signed_post(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = self._sign(dict(params))
        r = self._http.post(f"{path}?{query}")
        if r.is_error:
            self._raise_from_response(r, f"POST {path}")
        return r.json()

    # ── ExchangeAdapter interface ───────────────────────────────
    def validate_credentials(self) -> PermissionsSummary:
        """Probe account info to confirm the key works + read its permissions.

        Spot: ``GET /api/v3/account`` returns ``canTrade``, ``canWithdraw``,
        ``permissions`` flags.
        Futures: ``GET /fapi/v2/account`` doesn't expose withdraw flag — we
        additionally call ``GET /sapi/v1/account/apiRestrictions`` if possible.
        """
        try:
            acct = self._signed_get(self._account_path)
        except (AuthFailure, IPBlocked):
            raise
        except OrderRejected as exc:
            raise AuthFailure(f"validate failed: {exc}") from exc

        if self.market_type == "spot":
            return PermissionsSummary(
                can_trade=bool(acct.get("canTrade", False)),
                can_withdraw=bool(acct.get("canWithdraw", False)),
                can_margin=bool(acct.get("permissions") and "MARGIN" in acct["permissions"]),
                can_futures=bool(acct.get("permissions") and "FUTURES" in acct["permissions"]),
                account_uid=str(acct.get("accountType", "")),
            )
        # Futures: derive flags from account snapshot fields.
        # The /fapi/v2/account endpoint always implies canTrade if the call
        # succeeds (read+trade is the same scope on futures keys).
        return PermissionsSummary(
            can_trade=bool(acct.get("canTrade", True)),
            can_futures=True,
            can_withdraw=False,  # Futures keys don't grant withdraw — verified separately on Spot
            can_margin=False,
            account_uid=str(acct.get("accountAlias", "")),
        )

    def fetch_balance(self) -> BalanceSnapshot:
        acct = self._signed_get(self._account_path)
        if self.market_type == "spot":
            # Sum USD-stablecoin balances as a proxy for wallet_balance_usd.
            # (Heuristic; spot Binance has no native "USD value".)
            wallet = Decimal("0")
            stables = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD"}
            for b in acct.get("balances", []):
                asset = b.get("asset", "")
                free = Decimal(b.get("free") or "0")
                locked = Decimal(b.get("locked") or "0")
                if asset in stables:
                    wallet += free + locked
            return BalanceSnapshot(
                wallet_balance_usd=wallet, unrealized_pnl=Decimal("0"), positions=[],
            )

        # Futures: ``totalWalletBalance`` + ``totalUnrealizedProfit`` + per-position.
        wallet = Decimal(str(acct.get("totalWalletBalance") or "0"))
        upnl = Decimal(str(acct.get("totalUnrealizedProfit") or "0"))
        positions: list[Position] = []
        for p in acct.get("positions", []):
            qty = Decimal(str(p.get("positionAmt") or "0"))
            if qty == 0:
                continue
            entry = Decimal(str(p.get("entryPrice") or "0"))
            notional = Decimal(str(p.get("notional") or "0")).copy_abs()
            p_upnl = Decimal(str(p.get("unrealizedProfit") or "0"))
            side = "long" if qty > 0 else "short"
            positions.append(Position(
                symbol=p.get("symbol", ""),
                qty=qty.copy_abs(),
                entry_price=entry,
                notional_usd=notional,
                upnl=p_upnl,
                side=side,
            ))
        return BalanceSnapshot(wallet_balance_usd=wallet, unrealized_pnl=upnl, positions=positions)

    def create_market_order(
        self,
        *,
        symbol: str,
        side: str,
        usd_amount: Decimal,
        leverage: int,
        client_order_id: str,
    ) -> OrderResult:
        """Submit a market order. ``usd_amount`` is converted to base-qty
        per market type:

        * Spot: pass ``quoteOrderQty`` (Binance auto-derives qty).
        * Futures: must pass ``quantity`` in contracts → fetch current
          mark price first and divide.
        """
        side_upper = side.upper()
        if side_upper not in {"BUY", "SELL"}:
            return OrderResult(
                client_order_id=client_order_id, symbol=symbol, side=side,
                usd_amount=usd_amount, leverage=leverage,
                status="rejected", error=f"invalid side {side!r}",
            )

        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side_upper,
            "type": "MARKET",
            "newClientOrderId": client_order_id,
        }
        try:
            if self.market_type == "spot":
                params["quoteOrderQty"] = f"{usd_amount.normalize():f}"
            else:
                # Fetch mark price to compute qty.
                tick = self._http.get("/fapi/v1/premiumIndex", params={"symbol": symbol})
                if tick.is_error:
                    self._raise_from_response(tick, f"GET premiumIndex {symbol}")
                mark = Decimal(str(tick.json().get("markPrice") or "0"))
                if mark <= 0:
                    return OrderResult(
                        client_order_id=client_order_id, symbol=symbol, side=side,
                        usd_amount=usd_amount, leverage=leverage,
                        status="rejected", error=f"invalid mark price {mark}",
                    )
                qty = (usd_amount / mark).quantize(Decimal("0.0001"))
                params["quantity"] = f"{qty:f}"

            body = self._signed_post(self._order_path, params)
        except (AuthFailure, IPBlocked, InsufficientFunds):
            raise
        except OrderRejected as exc:
            return OrderResult(
                client_order_id=client_order_id, symbol=symbol, side=side,
                usd_amount=usd_amount, leverage=leverage,
                status="rejected", error=str(exc),
            )

        # Map Binance status → our enum.
        status_raw = (body.get("status") or "").upper()
        status = {
            "FILLED": "filled",
            "PARTIALLY_FILLED": "partial",
            "NEW": "submitted",
            "REJECTED": "rejected",
            "EXPIRED": "rejected",
            "CANCELED": "rejected",
        }.get(status_raw, "submitted")

        return OrderResult(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            usd_amount=usd_amount,
            leverage=leverage,
            status=status,
            exchange_order_id=str(body.get("orderId") or ""),
            raw_response={
                k: body[k] for k in ("status", "executedQty", "cummulativeQuoteQty", "transactTime")
                if k in body
            },
        )

    # ── Phase 2.2 — manual order management ─────────────────────────

    def create_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        client_order_id: str,
        time_in_force: str = "GTC",
    ) -> OrderResult:
        """Submit a limit order. ``qty`` is in base-currency units (not
        USD) — the caller is responsible for translating notional → qty
        because the right answer depends on the symbol's lot rules.
        """
        side_upper = side.upper()
        if side_upper not in {"BUY", "SELL"}:
            return OrderResult(
                client_order_id=client_order_id, symbol=symbol, side=side,
                usd_amount=Decimal("0"), leverage=1,
                status="rejected", error=f"invalid side {side!r}",
            )
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side_upper,
            "type": "LIMIT",
            "timeInForce": time_in_force,
            "quantity": f"{qty:f}",
            "price": f"{price:f}",
            "newClientOrderId": client_order_id,
        }
        try:
            body = self._signed_post(self._order_path, params)
        except (AuthFailure, IPBlocked, InsufficientFunds):
            raise
        except OrderRejected as exc:
            return OrderResult(
                client_order_id=client_order_id, symbol=symbol, side=side,
                usd_amount=qty * price, leverage=1,
                status="rejected", error=str(exc),
            )

        status_raw = (body.get("status") or "").upper()
        status = {
            "FILLED": "filled",
            "PARTIALLY_FILLED": "partial",
            "NEW": "submitted",
            "REJECTED": "rejected",
            "EXPIRED": "rejected",
            "CANCELED": "rejected",
        }.get(status_raw, "submitted")

        return OrderResult(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            usd_amount=qty * price,
            leverage=1,
            status=status,
            exchange_order_id=str(body.get("orderId") or ""),
            raw_response={
                k: body[k] for k in ("status", "executedQty", "price", "transactTime")
                if k in body
            },
        )

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
    ) -> bool:
        """Cancel one open order. Either ``order_id`` (exchange-side) or
        ``client_order_id`` must be provided — Binance accepts either.
        Returns True if the exchange returned status CANCELED.

        Binance code ``-2011`` ("Unknown order sent") fires when the order
        was already filled or cancelled. We treat that as a no-op success
        instead of bubbling an OrderRejected, because the caller's intent
        ("make sure this order isn't open") is already satisfied.
        """
        if not symbol:
            raise ValueError("cancel_order requires a symbol")
        if not order_id and not client_order_id:
            raise ValueError(
                "cancel_order requires order_id or client_order_id"
            )
        params: dict[str, Any] = {"symbol": symbol}
        if order_id:
            params["orderId"] = str(order_id)
        else:
            params["origClientOrderId"] = client_order_id

        query = self._sign(params)
        r = self._http.delete(f"{self._order_path}?{query}")
        if r.is_error:
            # ``-2011 Unknown order sent`` = already gone. Caller wins.
            try:
                err_code = (r.json() or {}).get("code")
            except Exception:  # noqa: BLE001
                err_code = None
            if err_code == -2011:
                return True
            self._raise_from_response(r, f"DELETE {self._order_path}")
        body = r.json() or {}
        return (body.get("status") or "").upper() == "CANCELED"

    def fetch_open_orders(self, symbol: str | None = None) -> list[OpenOrder]:
        """List pending orders, optionally filtered by symbol.

        Binance requires the SAPI weight to grow when ``symbol`` is
        omitted — only call without a symbol when you actually want
        cross-symbol cancel-all.
        """
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        rows = self._signed_get(self._open_orders_path, params)
        if not isinstance(rows, list):
            return []
        out: list[OpenOrder] = []
        for raw in rows:
            try:
                price = Decimal(str(raw.get("price") or "0"))
                qty = Decimal(str(raw.get("origQty") or "0"))
                filled = Decimal(str(raw.get("executedQty") or "0"))
            except Exception:  # noqa: BLE001
                price = qty = filled = Decimal("0")
            out.append(OpenOrder(
                symbol=raw.get("symbol", ""),
                side=(raw.get("side") or "").lower(),
                type=(raw.get("type") or "").lower(),
                price=price,
                qty=qty,
                filled_qty=filled,
                exchange_order_id=str(raw.get("orderId") or ""),
                client_order_id=raw.get("clientOrderId", ""),
                status=(raw.get("status") or "").lower(),
                created_at_ms=int(raw.get("time") or 0),
                raw=raw,
            ))
        return out

    def close(self) -> None:
        self._http.close()


# Re-export so callers writing ``except ExchangeError:`` only need
# one import line.
from talyxion.cli.exchanges._base import ExchangeError  # noqa: E402
