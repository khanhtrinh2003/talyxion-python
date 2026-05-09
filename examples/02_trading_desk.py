"""Trading desk — credentials, profile, activate, monitor cycles.

Prereq:
    export TALYXION_API_KEY=tk_...
"""
from talyxion import Talyxion


def main() -> None:
    tlx = Talyxion()

    # 1. List existing credentials
    print("=== Credentials ===")
    creds = tlx.trading.credentials.list()
    for c in creds:
        print(f"  #{c.id}  {c.exchange:12s}  {c.label:10s}  status={c.validation_status}")

    if not creds:
        print("  No credentials. Add one via:")
        print("    tlx.trading.credentials.create(")
        print("        exchange='binance', label='main',")
        print("        api_key='...', api_secret='...',")
        print("    )")
        return

    cred = creds[0]
    if cred.validation_status != "ok":
        print(f"\n  Re-validating credential #{cred.id}...")
        result = tlx.trading.credentials.validate(cred.id)
        print(f"  → status={result['result'].get('status')}")
        cred = result["credential"]

    # 2. List my profiles
    print("\n=== My trading profiles ===")
    profiles = tlx.trading.profiles.list()
    for p in profiles:
        print(f"  #{p.id}  {p.name:20s}  {p.exchange:10s}  {p.mode:10s}  {p.status}")

    # 3. Inspect a profile's recent cycles + live positions
    if profiles:
        profile = profiles[0]
        print(f"\n=== Cycles for profile #{profile.id} ({profile.name}) ===")
        for cycle in profile.cycles.tail(5):
            ts = cycle.started_at[:19] if cycle.started_at else "?"
            print(f"  {ts}  {cycle.outcome:14s}  trades={cycle.trades_filled}/{cycle.trades_attempted}")

        if profile.status == "active":
            print(f"\n=== Live positions for profile #{profile.id} ===")
            try:
                snap = profile.positions()
                print(f"  Wallet balance: ${snap.wallet_balance:.2f}")
                print(f"  Unrealized PnL: ${snap.unrealized_pnl:.2f}")
                print(f"  Position count: {snap.position_count}")
                for pos in snap.positions[:5]:
                    print(f"    {pos.symbol:12s} {pos.side:5s} qty={pos.qty} @ {pos.entry_price}")
            except Exception as exc:
                print(f"  positions unavailable: {exc}")

    # 4. Create a new profile (commented — uncomment after you have an alpha id)
    # alpha_id = "REPLACE_WITH_LICENSED_ALPHA_ID"
    # new_profile = tlx.trading.profiles.create(
    #     name="my_btc_v1", alpha_id=alpha_id,
    #     exchange="binance", credential_id=cred.id,
    #     mode="simulation", leverage=1, book_usd=500,
    #     cycle_interval_sec=300,
    #     max_drawdown_pct=15,
    # ).activate()
    # print(f"\n  Created + activated profile #{new_profile.id}")


if __name__ == "__main__":
    main()
