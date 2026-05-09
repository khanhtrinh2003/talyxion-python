"""``/api/v1/market/`` — listings, library, buy, seller stats."""
from __future__ import annotations

from typing import Any

from ..models.common import Page
from ..models.market import (
    CreditTopUp,
    License,
    Listing,
    ListingDetail,
    Purchase,
    TopUpResponse,
    WalletAccount,
    WalletTransaction,
)
from ._base import Resource, build_page, extract_data


class MarketResource(Resource):
    """``client.market.*`` — browse, buy, list-for-sale, seller stats."""

    def search(
        self,
        *,
        q: str | None = None,
        region: str | None = None,
        license: str | None = None,
        min_sharpe: float | None = None,
        max_price_vnd: int | None = None,
        sort: str = "newest",
        limit: int = 20,
        offset: int = 0,
    ) -> Page[Listing]:
        """``GET /api/v1/market/listings/``"""
        params: dict[str, Any] = {
            "q": q, "region": region, "license": license,
            "min_sharpe": min_sharpe, "max_price_vnd": max_price_vnd,
            "sort": sort, "limit": limit, "offset": offset,
        }
        body = self._http.get("/api/v1/talyxion/market/listings/", params=params)
        page: Page[Listing] = build_page(body, Listing, list(extract_data(body) or []))

        def _loader(lim: int, off: int) -> Page[Listing]:
            return self.search(
                q=q, region=region, license=license,
                min_sharpe=min_sharpe, max_price_vnd=max_price_vnd,
                sort=sort, limit=lim, offset=off,
            )
        return page.with_loader(_loader)

    def get(self, slug: str) -> ListingDetail:
        body = self._http.get(f"/api/v1/talyxion/market/listings/{slug}/")
        return ListingDetail.model_validate(extract_data(body))

    def buy(self, slug: str, *, license_type: str = "lifetime") -> Purchase:
        """Purchase via wallet credits. Raises ``TalyxionError`` if insufficient funds."""
        body = self._http.post(
            f"/api/v1/talyxion/market/listings/{slug}/buy/",
            json={"license_type": license_type},
        )
        return Purchase.model_validate(extract_data(body))

    def list_for_sale(
        self,
        *,
        alpha_id: str,
        title: str,
        description_md: str = "",
        tags: str = "",
        category: str = "",
        lifetime_price_vnd: int | None = None,
        lifetime_price_usd: float | None = None,
        monthly_price_vnd: int | None = None,
        monthly_price_usd: float | None = None,
        yearly_price_vnd: int | None = None,
        yearly_price_usd: float | None = None,
    ) -> ListingDetail:
        body = self._http.post("/api/v1/talyxion/market/listings/create/", json={
            "alpha_id": alpha_id, "title": title,
            "description_md": description_md, "tags": tags, "category": category,
            "lifetime_price_vnd": lifetime_price_vnd,
            "lifetime_price_usd": lifetime_price_usd,
            "monthly_price_vnd": monthly_price_vnd,
            "monthly_price_usd": monthly_price_usd,
            "yearly_price_vnd": yearly_price_vnd,
            "yearly_price_usd": yearly_price_usd,
        })
        return ListingDetail.model_validate(extract_data(body))

    def edit(self, slug: str, **fields: Any) -> ListingDetail:
        body = self._http.patch(f"/api/v1/talyxion/market/listings/{slug}/edit/", json=fields)
        return ListingDetail.model_validate(extract_data(body))

    def library(self) -> list[License]:
        """My licenses (own + purchased + admin-granted)."""
        body = self._http.get("/api/v1/talyxion/market/library/")
        return [License.model_validate(item) for item in extract_data(body) or []]

    def seller_stats(self) -> dict[str, Any]:
        body = self._http.get("/api/v1/talyxion/market/seller/stats/")
        data: dict[str, Any] = extract_data(body) or {}
        data["listings"] = [Listing.model_validate(item) for item in data.get("listings") or []]
        data["wallet"] = WalletAccount.model_validate(data.get("wallet") or {})
        return data


class WalletResource(Resource):
    """``client.wallet.*`` — balance, ledger, top-up flow."""

    def balance(self) -> WalletAccount:
        body = self._http.get("/api/v1/talyxion/wallet/")
        return WalletAccount.model_validate(extract_data(body))

    def transactions(self, *, limit: int = 30) -> list[WalletTransaction]:
        body = self._http.get("/api/v1/talyxion/wallet/transactions/", params={"limit": limit})
        return [WalletTransaction.model_validate(t) for t in extract_data(body) or []]

    def topup(self, amount_vnd: int, *, note: str = "") -> TopUpResponse:
        """Create a top-up request. Returns a VietQR URL the user can scan."""
        body = self._http.post("/api/v1/talyxion/wallet/topup/", json={
            "amount_credits": int(amount_vnd),
            "note": note,
        })
        data = extract_data(body)
        return TopUpResponse(
            topup=CreditTopUp.model_validate(data["topup"]),
            qr_url=data["qr_url"],
            memo=data["memo"],
            bank=data.get("bank") or {},
        )

    def topup_status(self, topup_id: int) -> CreditTopUp:
        body = self._http.get(f"/api/v1/talyxion/wallet/topup/{topup_id}/")
        return CreditTopUp.model_validate(extract_data(body))
