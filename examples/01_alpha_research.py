"""Alpha research — build, simulate, check overfit, save.

Prereq:
    export TALYXION_API_KEY=tk_...
    pip install -e .
"""
from talyxion import Backtest, Talyxion


def main() -> None:
    tlx = Talyxion()

    # 1. Build a Regular alpha with the fluent builder
    print("\n=== Regular alpha simulation ===")
    result = (
        Backtest(
            region="crypto_trade",
            universe="TOP19",
            decay=4,
            truncation=0.08,
            neutralization="market",
            save=True,
        )
        .alpha("rank(close - ts_mean(close, 20)) * volume", delay=1)
        .simulate(tlx)
    )
    print(f"  alpha_id  = {result.alpha_id}")
    print(f"  saved     = {result.saved}")
    print(f"  Sharpe    = {result.sharpe}")
    print(f"  Fitness   = {result.fitness}")
    print(f"  Turnover  = {result.turnover}")
    print(f"  Drawdown  = {result.drawdown}")
    print(f"  passes_overfit() = {result.passes_overfit()}")

    # 2. Authoritative overfit check (incl. Ladder Sharpe autocorr p-value)
    if result.alpha_id:
        report = tlx.alphas.overfit(result.alpha_id)
        print("\n  Overfit checks (authoritative):")
        for c in report.checks:
            mark = "✓" if c.passed else "✗"
            print(f"    {mark} {c.label:30s} {c.result}")
        print(f"  passes_all = {report.passes_all}")

    # 3. Browse my library — top 5 by Sharpe
    print("\n=== My alpha library ===")
    page = tlx.alphas.list(mine_only=True, sort="sharpe", order="desc", limit=5)
    for a in page:
        print(f"  {a.id}  sharpe={a.sharpe:.2f if a.sharpe else 'N/A'}  region={a.region}")
    print(f"  ({page.pagination.total} total)")

    # 4. Get equity curve as pandas Series
    if result.alpha_id:
        series = tlx.alphas.pnl(result.alpha_id).to_pandas()
        print(f"\n=== PnL series ({len(series)} points) ===")
        print(series.head())
        print(f"  end equity = {series.iloc[-1]:.4f}")

    # 5. Super alpha (combo of two existing alphas)
    if page.pagination.total >= 2:
        ids = [a.id for a in page.items[:2]]
        print(f"\n=== Super alpha (combo of {ids}) ===")
        sa_result = (
            Backtest(region="crypto_trade", universe="TOP19")
            .super_alpha(ids, combo="0.5 * a + 0.5 * b")
            .simulate(tlx)
        )
        print(f"  super alpha_id = {sa_result.alpha_id}  Sharpe={sa_result.sharpe}")


if __name__ == "__main__":
    main()
