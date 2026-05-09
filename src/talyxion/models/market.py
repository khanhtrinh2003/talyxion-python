"""Pydantic models for marketplace + wallet endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ListingSnapshot(BaseModel):
    """Metrics frozen at listing creation time."""

    model_config = ConfigDict(extra="allow")

    sharpe: float | None = None
    fitness: float | None = None
    turnover: float | None = None
    drawdown: float | None = None
    returns_pct: float | None = None
    region: str = ""
    universe: str = ""
    taken_at: str | None = None


class ListingPricing(BaseModel):
    model_config = ConfigDict(extra="allow")

    lifetime_vnd: int | None = None
    lifetime_usd: float | None = None
    monthly_vnd: int | None = None
    monthly_usd: float | None = None
    yearly_vnd: int | None = None
    yearly_usd: float | None = None


class ListingStats(BaseModel):
    model_config = ConfigDict(extra="allow")

    view_count: int = 0
    purchase_count: int = 0
    rating_avg: float = 0.0
    rating_count: int = 0


class Listing(BaseModel):
    """Marketplace listing summary (search results)."""

    model_config = ConfigDict(extra="allow")

    slug: str
    alpha_id: str
    title: str
    tags: list[str] = []
    category: str | None = None
    status: str = "active"
    snapshot: ListingSnapshot
    pricing: ListingPricing
    stats: ListingStats
    created_at: str

    def price_vnd(self, license_type: str = "lifetime") -> int | None:
        return getattr(self.pricing, f"{license_type}_vnd", None)

    def price_usd(self, license_type: str = "lifetime") -> float | None:
        return getattr(self.pricing, f"{license_type}_usd", None)


class ListingDetail(Listing):
    description_md: str = ""
    seller: dict[str, Any] = {}
    owner_stats: dict[str, Any] | None = None  # only populated for seller / admin


class License(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    alpha_id: str
    source: str  # "owner" | "purchased" | "admin_grant"
    license_type: str  # "owner" | "lifetime" | "monthly" | "yearly"
    granted_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    is_active: bool = True
    listing_slug: str | None = None


class Purchase(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    buyer_id: int
    listing_slug: str | None = None
    alpha_id: str | None = None
    license_type_chosen: str
    credits_charged: int
    platform_fee_credits: int
    seller_credits: int
    status: str
    license_id: int | None = None
    refundable_until: str | None = None
    created_at: str
    completed_at: str | None = None


# ── Wallet ────────────────────────────────────────────────────────────


class WalletAccount(BaseModel):
    model_config = ConfigDict(extra="allow")

    credits_balance: int
    lifetime_topup_credits: int
    lifetime_spent_credits: int
    updated_at: str | None = None


class WalletTransaction(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    kind: str
    amount: int
    balance_after: int
    note: str = ""
    created_at: str
    related_topup_id: int | None = None
    related_purchase_id: int | None = None
    related_payout_id: int | None = None


class CreditTopUp(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    amount_credits: int
    currency: str = "VND"
    payment_method: str = ""
    proof_reference: str | None = None
    bank_info_used: str | None = None
    status: str = "pending"
    created_at: str
    succeeded_at: str | None = None


class TopUpResponse(BaseModel):
    """``client.wallet.topup(...)`` response — topup record + scannable QR url."""

    model_config = ConfigDict(extra="allow")

    topup: CreditTopUp
    qr_url: str
    memo: str
    bank: dict[str, Any]
