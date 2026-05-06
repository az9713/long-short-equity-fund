"""
run_risk_check.py — Meridian Capital Partners / JARVIS
Layer 5 Risk Management entry point.

Usage:
  python run_risk_check.py              # full risk check, print dashboard
  python run_risk_check.py --stress     # run all 6 stress scenarios
  python run_risk_check.py --tail-only  # just VIX + credit spread check
  python run_risk_check.py --clear-halt # remove halt.lock file
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Meridian Capital Partners — JARVIS Risk Management"
    )
    parser.add_argument("--stress", action="store_true", help="Run all 6 stress scenarios")
    parser.add_argument("--tail-only", action="store_true", help="VIX + credit spread check only")
    parser.add_argument("--clear-halt", action="store_true", help="Remove halt.lock file")
    return parser.parse_args()


def _build_weights(positions_df, portfolio_value: float) -> dict:
    weights = {}
    if positions_df.empty:
        return weights
    for _, row in positions_df.iterrows():
        ticker = row["ticker"]
        shares = float(row.get("shares", 0) or 0)
        price = float(row.get("current_price") or row.get("entry_price", 0) or 0)
        mv = shares * price
        w = mv / portfolio_value if portfolio_value > 0 else 0.0
        if row.get("side") == "SHORT":
            w = -w
        weights[ticker] = w
    return weights


def _load_scored_df():
    import pandas as pd
    path = ROOT / "output" / "scored_universe_latest.csv"
    if path.exists():
        return pd.read_csv(path)
    return None


def _print_dashboard(portfolio_value: float = 100_000.0):
    from portfolio.state import get_positions, update_current_prices
    from risk.circuit_breakers import check_circuit_breakers, get_daily_pnl, get_weekly_pnl, get_peak_value
    from risk.tail_risk import check_tail_risk
    from risk.factor_monitor import check_factor_monitor
    from risk.correlation_monitor import check_correlations
    from risk.risk_state import load_risk_state, update_risk_state
    from utils import get_config

    cfg = get_config().get("risk", {})

    print(f"\nRISK DASHBOARD - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    print("=" * 44)

    # Update prices first
    try:
        update_current_prices()
        positions = get_positions()
    except Exception as e:
        print(f"  WARNING: Could not load positions: {e}")
        positions = None

    import pandas as pd
    if positions is None:
        positions = pd.DataFrame()

    weights = _build_weights(positions, portfolio_value)

    # Update risk state (populates MCTR, factor decomposition, alerts)
    scored_df = _load_scored_df()
    try:
        update_risk_state(portfolio_value, scored_df, weights)
    except Exception as e:
        print(f"  WARNING: risk state update failed: {e}")

    # --- Circuit Breakers ---
    cb_results = check_circuit_breakers(portfolio_value)
    daily_pnl = get_daily_pnl(portfolio_value)
    weekly_pnl = get_weekly_pnl(portfolio_value)
    peak = get_peak_value()
    drawdown = (portfolio_value - peak) / peak if peak > 0 else 0.0

    daily_limit = cfg.get("daily_loss_limit", 0.015)
    weekly_limit = cfg.get("weekly_loss_limit", 0.04)
    drawdown_limit = cfg.get("drawdown_limit", 0.08)

    cb_status = "ALL GREEN" if not cb_results else f"{len(cb_results)} BREAKER(S) TRIGGERED"
    print(f"\nCircuit Breakers:  {cb_status}")
    print(f"  Daily P&L:      {daily_pnl:+.1%} (limit: {daily_limit:.1%})")
    print(f"  Weekly P&L:     {weekly_pnl:+.1%} (limit: {weekly_limit:.1%})")
    print(f"  Drawdown:       {drawdown:+.1%} (limit: {drawdown_limit:.1%})")

    if cb_results:
        print("\n  TRIGGERED:")
        for cb in cb_results:
            print(f"    [{cb['level']}] {cb['action']}: {cb['reason']}")

    # --- Tail Risk ---
    print("\nTail Risk:")
    try:
        tail = check_tail_risk()
        regime = tail.get("vix_regime", "UNKNOWN")
        vix = tail.get("vix", 0)
        cs = tail.get("credit_spread")
        cs_str = f"{cs:.2f}" if cs is not None else "N/A"
        print(f"  VIX: {vix:.1f} ({regime} regime)")
        print(f"  Credit Spread: {cs_str}")
        if tail.get("action") != "OK":
            print(f"  ACTION: {tail['action']} — {tail.get('message', '')}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # --- Factor Monitor ---
    print("\nFactor Monitor:")
    try:
        scored_df = _load_scored_df()
        if scored_df is not None:
            factor_alerts = check_factor_monitor(scored_df)
            if not factor_alerts:
                print("  No factor alerts")
            else:
                for alert in factor_alerts:
                    print(f"  [{alert['priority']}] {alert['message']}")
        else:
            print("  No scored universe available (run run_scoring.py)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # --- Correlation Monitor ---
    print("\nCorrelation:")
    try:
        corr_result = check_correlations(positions)
        avg_l = corr_result.get("avg_long_correlation")
        avg_s = corr_result.get("avg_short_correlation")
        eff = corr_result.get("effective_bets")
        print(f"  Long book avg correlation:  {avg_l:.2f}" if avg_l is not None else "  Long book: N/A")
        print(f"  Short book avg correlation: {avg_s:.2f}" if avg_s is not None else "  Short book: N/A")
        print(f"  Effective bets: {eff:.1f}" if eff is not None else "  Effective bets: N/A")
        for alert in corr_result.get("alerts", []):
            print(f"  ALERT: {alert}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # --- Risk Decomposition ---
    print("\nRisk Decomposition:")
    try:
        state = load_risk_state()
        decomp = state.get("risk_decomposition", {})
        factor_pct = decomp.get("factor_pct", 0.0)
        specific_pct = decomp.get("specific_pct", 1.0)
        print(f"  Factor risk:   {factor_pct:.1%}")
        print(f"  Specific risk: {specific_pct:.1%}")

        # MCTR — show top 3 disproportionate
        mctr = state.get("mctr", {})
        disproportionate = [
            (t, info) for t, info in mctr.items()
            if isinstance(info, dict) and info.get("disproportionate")
        ]
        if disproportionate:
            disproportionate.sort(key=lambda x: abs(x[1].get("mctr", 0)), reverse=True)
            print(f"\nMCTR (top {min(3, len(disproportionate))} disproportionate):")
            for ticker, info in disproportionate[:3]:
                mctr_val = info.get("mctr", 0)
                weight = info.get("weight", 0)
                print(f"  {ticker}: MCTR {mctr_val:.2%} vs weight {weight:.2%}")
        elif mctr:
            print("  All MCTR within normal range")
        else:
            print("  (Run with scored_df to compute MCTR — use update_risk_state)")
    except Exception as e:
        print(f"  ERROR: {e}")

    print()


def _print_stress(portfolio_value: float = 100_000.0):
    from portfolio.state import get_positions, update_current_prices
    from risk.stress_test import run_stress_tests

    try:
        update_current_prices()
        positions = get_positions()
    except Exception as e:
        print(f"  WARNING: {e}")
        import pandas as pd
        positions = pd.DataFrame()

    weights = _build_weights(positions, portfolio_value)

    print(f"\nSTRESS TEST RESULTS - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    print("=" * 56)

    if not weights:
        # Fall back to scored signals at the per-position cap so stress can run
        # pre-execution. This approximates what the optimizer would build.
        try:
            import pandas as pd
            from utils import ROOT, get_config
            scored_path = ROOT / "output" / "scored_universe_latest.csv"
            if scored_path.exists():
                scored = pd.read_csv(scored_path)
                cap = get_config().get("portfolio", {}).get("max_position_pct", 0.05)
                hypo = {}
                for _, r in scored.iterrows():
                    if r.get("signal") == "LONG":
                        hypo[r["ticker"]] = cap
                    elif r.get("signal") == "SHORT":
                        hypo[r["ticker"]] = -cap
                if hypo:
                    print(f"  No live positions — stressing hypothetical book "
                          f"({sum(1 for v in hypo.values() if v>0)}L / "
                          f"{sum(1 for v in hypo.values() if v<0)}S at {cap:.0%} each).")
                    weights = hypo
        except Exception as e:
            log = __import__('utils').get_logger('run_risk_check')
            log.warning(f"Hypothetical-book fallback failed: {e}")

    if not weights:
        print("  No positions and no scored signals — nothing to stress.")
        return

    results = run_stress_tests(weights)
    if not results:
        print("  No results (stress runner returned empty)")
        return

    for r in results:
        name = r["scenario_name"]
        total = r["total_pnl_pct"]
        long_p = r["long_pnl_pct"]
        short_p = r["short_pnl_pct"]
        sign = "+" if total >= 0 else ""
        print(f"\n  {name}")
        print(f"    Total P&L:  {sign}{total:.1%}  (long: {long_p:+.1%}, short: {short_p:+.1%})")
        worst = r.get("worst_contributors", [])
        if worst:
            print(f"    Worst contributors:")
            for w in worst:
                print(f"      {w['ticker']}: {w['contribution']:+.2%}")
    print()


def _print_tail_only():
    from risk.tail_risk import check_tail_risk
    print(f"\nTAIL RISK CHECK - {datetime.utcnow().strftime('%Y-%m-%d')}")
    print("=" * 40)
    try:
        tail = check_tail_risk()
        print(f"  VIX:           {tail['vix']:.1f} ({tail['vix_regime']})")
        cs = tail.get("credit_spread")
        csz = tail.get("cs_zscore")
        print(f"  Credit Spread: {cs:.4f}" if cs is not None else "  Credit Spread: N/A")
        if csz is not None:
            print(f"  CS Z-score:    {csz:.2f}")
        print(f"  Action:        {tail['action']}")
        print(f"  Message:       {tail['message']}")
    except Exception as e:
        print(f"  ERROR: {e}")
    print()


def main():
    args = parse_args()

    from utils import get_logger
    log = get_logger("run_risk_check")

    print("=" * 60)
    print("  Meridian Capital Partners — JARVIS Risk Management")
    print("=" * 60)

    portfolio_value = 100_000.0

    if args.clear_halt:
        from risk.circuit_breakers import clear_halt_lock
        clear_halt_lock()
        print("  Halt lock cleared.")
        return

    if args.tail_only:
        _print_tail_only()
        return

    if args.stress:
        _print_stress(portfolio_value)
        return

    # Full risk check
    _print_dashboard(portfolio_value)


if __name__ == "__main__":
    main()
