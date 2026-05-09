"""``/api/v1/trading/`` — credentials + profiles + cycles + positions."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .._http import HttpClient
from ..models.common import Page
from ..models.trading import (
    Credential,
    CycleRun,
    PositionsSnapshot,
    Profile,
)
from ._base import Resource, build_page, extract_data


class CredentialsResource(Resource):
    """``client.trading.credentials.*`` — exchange API credentials."""

    def list(self) -> list[Credential]:
        body = self._http.get("/api/v1/talyxion/trading/credentials/")
        return [Credential.model_validate(c) for c in extract_data(body) or []]

    def create(
        self,
        *,
        exchange: str,
        label: str = "main",
        api_key: str | None = None,
        api_secret: str | None = None,
        api_passphrase: str | None = None,
        hl_wallet_address: str | None = None,
        hl_private_key: str | None = None,
        hl_vault_address: str | None = None,
    ) -> Credential:
        payload = {
            "exchange": exchange,
            "label": label,
            "api_key": api_key,
            "api_secret": api_secret,
            "api_passphrase": api_passphrase,
            "hl_wallet_address": hl_wallet_address,
            "hl_private_key": hl_private_key,
            "hl_vault_address": hl_vault_address,
        }
        body = self._http.post("/api/v1/talyxion/trading/credentials/create/", json=payload)
        return Credential.model_validate(extract_data(body))

    def validate(self, cred_id: int) -> dict[str, Any]:
        """Re-probe the exchange. Returns ``{"credential": Credential, "result": {...}}``."""
        body = self._http.post(f"/api/v1/talyxion/trading/credentials/{cred_id}/validate/")
        data = extract_data(body)
        return {
            "credential": Credential.model_validate(data["credential"]),
            "result": data.get("result", {}),
        }


class ProfileCyclesResource:
    """Sub-resource on a Profile instance — paginated cycle history."""

    def __init__(self, http: HttpClient, profile_id: int) -> None:
        self._http = http
        self._profile_id = profile_id

    def list(self, *, limit: int = 50, offset: int = 0) -> Page[CycleRun]:
        body = self._http.get(
            f"/api/v1/talyxion/trading/profiles/{self._profile_id}/cycles/",
            params={"limit": limit, "offset": offset},
        )
        page: Page[CycleRun] = build_page(body, CycleRun, list(extract_data(body) or []))

        def _loader(lim: int, off: int) -> Page[CycleRun]:
            return self.list(limit=lim, offset=off)
        return page.with_loader(_loader)

    def tail(self, n: int = 10) -> Sequence[CycleRun]:
        return self.list(limit=n).items


class ProfileHandle:
    """Wrapper around a Profile model that exposes lifecycle + sub-resources.

    Returned by ``client.trading.profiles.create(...)`` and ``.get(...)``.
    Behaves like the Pydantic model (``handle.name``, ``handle.status``)
    but adds methods that hit the API.
    """

    def __init__(self, http: HttpClient, profile: Profile) -> None:
        self._http = http
        self._profile = profile
        self.cycles = ProfileCyclesResource(http, profile.id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._profile, name)

    def __repr__(self) -> str:
        return f"<Profile #{self._profile.id} {self._profile.name} status={self._profile.status}>"

    def refresh(self) -> ProfileHandle:
        body = self._http.get(f"/api/v1/talyxion/trading/profiles/{self._profile.id}/")
        self._profile = Profile.model_validate(extract_data(body))
        return self

    def _action(self, action: str, **body: Any) -> ProfileHandle:
        resp = self._http.post(
            f"/api/v1/talyxion/trading/profiles/{self._profile.id}/{action}/",
            json=body or {},
        )
        self._profile = Profile.model_validate(extract_data(resp))
        return self

    def activate(self) -> ProfileHandle:
        return self._action("activate")

    def pause(self, *, reason: str = "manual") -> ProfileHandle:
        return self._action("pause", reason=reason)

    def resume(self) -> ProfileHandle:
        return self._action("resume")

    def archive(self) -> ProfileHandle:
        return self._action("archive")

    def positions(self) -> PositionsSnapshot:
        body = self._http.get(f"/api/v1/talyxion/trading/profiles/{self._profile.id}/positions/")
        return PositionsSnapshot.model_validate(extract_data(body))


class ProfilesResource(Resource):
    """``client.trading.profiles.*`` — trading profiles CRUD + lifecycle."""

    def list(self, *, include_archived: bool = False) -> list[ProfileHandle]:
        body = self._http.get(
            "/api/v1/talyxion/trading/profiles/",
            params={"include_archived": "1" if include_archived else None},
        )
        rows = [Profile.model_validate(p) for p in extract_data(body) or []]
        return [ProfileHandle(self._http, p) for p in rows]

    def get(self, profile_id: int) -> ProfileHandle:
        body = self._http.get(f"/api/v1/talyxion/trading/profiles/{profile_id}/")
        return ProfileHandle(self._http, Profile.model_validate(extract_data(body)))

    def create(
        self,
        *,
        name: str,
        alpha_id: str,
        exchange: str,
        credential_id: int,
        mode: str = "simulation",
        leverage: int = 1,
        book_usd: float | None = None,
        market_type: str = "futures",
        position_mode: str = "one_way",
        margin_mode: str = "cross",
        region: str = "crypto_trade",
        universe: str = "",
        data_exchange: str = "",
        cycle_interval_sec: int = 600,
        max_drawdown_pct: float | None = None,
        max_position_usd: float | None = None,
        volume_usd_divisor: int = 10,
    ) -> ProfileHandle:
        body = self._http.post("/api/v1/talyxion/trading/profiles/create/", json={
            "name": name, "alpha_id": alpha_id, "exchange": exchange,
            "credential_id": credential_id, "mode": mode, "leverage": leverage,
            "book_usd": book_usd, "market_type": market_type,
            "position_mode": position_mode, "margin_mode": margin_mode,
            "region": region, "universe": universe, "data_exchange": data_exchange,
            "cycle_interval_sec": cycle_interval_sec,
            "max_drawdown_pct": max_drawdown_pct,
            "max_position_usd": max_position_usd,
            "volume_usd_divisor": volume_usd_divisor,
        })
        return ProfileHandle(self._http, Profile.model_validate(extract_data(body)))


class TradingResource:
    """Composite resource: ``client.trading.credentials`` + ``client.trading.profiles``."""

    def __init__(self, http: HttpClient) -> None:
        self.credentials = CredentialsResource(http)
        self.profiles = ProfilesResource(http)
