"""Marketplace — search, buy, list-for-sale, wallet top-up via VietQR.

Prereq:
    export TALYXION_API_KEY=tk_...
"""
from talyxion import Talyxion


def main() -> None:
    tlx = Talyxion()

    # 1. Browse top alphas
    print("=== Marketplace: top alphas by Sharpe ===")
    page = tlx.market.search(min_sharpe=2.0, sort="sharpe", limit=5)
    for l in page:
        price = l.price_vnd("lifetime")
        print(f"  {l.slug:14s}  {l.title[:40]:40s}  Sharpe={l.snapshot.sharpe:.2f}  {price:>12,} VND" if price else f"  {l.slug:14s}  {l.title[:40]:40s}")

    # 2. Wallet balance
    print("\n=== Wallet ===")
    wallet = tlx.wallet.balance()
    print(f"  Balance:        {wallet.credits_balance:,} VND")
    print(f"  Lifetime topup: {wallet.lifetime_topup_credits:,} VND")
    print(f"  Lifetime spent: {wallet.lifetime_spent_credits:,} VND")

    # 3. Top up flow (returns a VietQR URL the user scans with their banking app)
    print("\n=== Top up 200,000 VND ===")
    topup_resp = tlx.wallet.topup(amount_vnd=200_000)
    print(f"  Topup #{topup_resp.topup.id}  status={topup_resp.topup.status}")
    print(f"  Memo:    {topup_resp.memo}")
    print(f"  QR URL:  {topup_resp.qr_url}")
    print(f"  Bank:    {topup_resp.bank.get('name')} · {topup_resp.bank.get('account')} · {topup_resp.bank.get('holder')}")
    print("  → Open the QR URL in a browser or banking app to pay.")

    # 4. My licenses
    print("\n=== My library ===")
    for lic in tlx.market.library()[:10]:
        kind = "🛒" if lic.source == "purchased" else "✨" if lic.source == "owner" else "🎁"
        exp = f"expires {lic.expires_at[:10]}" if lic.expires_at else "lifetime"
        print(f"  {kind}  {lic.alpha_id:12s}  {lic.license_type:10s}  {exp}")

    # 5. Buy a listing (commented — un-comment after you've topped up)
    # purchase = tlx.market.buy(slug="zfgPCLOE7fUq", license_type="lifetime")
    # print(f"\n  Bought purchase #{purchase.id}  charged={purchase.credits_charged:,} VND")

    # 6. Seller stats (if you've listed alphas for sale)
    print("\n=== Seller stats ===")
    try:
        stats = tlx.market.seller_stats()
        print(f"  Total revenue:  {stats['total_revenue_credits']:,} VND")
        print(f"  Lifetime sales: {stats['lifetime_sales']}")
        print(f"  Pending payout: {stats['pending_payout_credits']:,} VND")
        print(f"  Active listings: {len(stats['listings'])}")
    except Exception as exc:
        print(f"  (no seller account or error: {exc})")


if __name__ == "__main__":
    main()
