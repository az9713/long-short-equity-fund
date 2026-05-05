"""
run_backtest.py — Meridian Capital Partners / JARVIS
Walk-forward backtesting utility for the long-short equity scoring engine.

IMPORTANT: Results have look-ahead bias (current fundamentals used) and
survivorship bias (current S&P 500 members only). Use for directional
validation only.

Examples:
  python run_backtest.py --start 2021-01-01 --end 2024-12-31
  python run_backtest.py --dev --start 2022-01-01
  python run_backtest.py --with-costs --num-longs 10 --num-shorts 10
  python run_backtest.py --full-score --dev --start 2023-01-01
"""

import os
import sys
import argparse
import time
from pathlib import Path
from datetime import datetime, timedelta, date

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from utils import get_logger, get_db, get_config

log = get_logger("run_backtest")

BIAS_CAVEATS = [
    "CAVEAT 1: yfinance fundamentals are CURRENT, not point-in-time. Value/quality factors",
    "          have look-ahead bias when used in backtest mode (--full-score).",
    "CAVEAT 2: Universe is TODAY'S S&P 500 members. Survivorship bias present —",
    "          delisted/bankrupt companies are excluded.",
    "CAVEAT 3: No transaction costs modeled by default. Use --with-costs to add",
    "          10bps round-trip estimate per rebalance.",
    "CAVEAT 4: Results are INDICATIVE only. Not predictive of live performance.",
]

MOMENTUM_NOTE = (
    "NOTE: Backtest uses momentum factor only (12-1 month price return). "
    "Full 8-factor scoring uses current data and would have look-ahead bias anyway. "
    "Use --full-score to run current composite scores (bias acknowledged)."
)


# ── Price matrix ─────────────────────────────────────────────────────────────

def _build_price_matrix(tickers: list, start: date, end: date) -> pd.DataFrame:
    # Request enough history to cover 252-day momentum lookback before start
    # plus the full backtest window
    today = date.today()
    days_needed = (today - start).days + 400  # 400-day buffer for 252-day lookback

    conn = get_db()
    placeholders = ",".join("?" * len(tickers))
    # Fetch raw rows from DB for all tickers at once
    try:
        rows = conn.execute(
            f"""
            SELECT ticker, date, adj_close
            FROM daily_prices
            WHERE ticker IN ({placeholders})
            ORDER BY date
            """,
            tickers,
        ).fetchall()
    except Exception as e:
        log.error(f"Price matrix query failed: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    df = df.pivot(index="date", columns="ticker", values="adj_close")
    df = df.sort_index()

    # Forward-fill gaps (weekends, holidays, suspended trading)
    df = df.ffill()

    return df


def _compute_spy_returns(price_matrix: pd.DataFrame) -> pd.Series:
    if "SPY" not in price_matrix.columns:
        return pd.Series(dtype=float)
    spy = price_matrix["SPY"].dropna()
    return spy.pct_change().fillna(0.0)


# ── Rebalance date generation ─────────────────────────────────────────────────

def _get_rebalance_dates(trading_dates: pd.DatetimeIndex, start: date, end: date, every_n: int) -> list:
    # Work from the trading-day index, not calendar math
    # Need at least 252 bars before a rebalance date for momentum calc
    # Find first trading date >= start that has >= 252 prior bars
    dates = trading_dates[trading_dates >= pd.Timestamp(start)]
    dates = dates[dates <= pd.Timestamp(end)]

    rebal_dates = []
    i = 0
    while i < len(dates):
        rebal_dates.append(dates[i])
        i += every_n

    return rebal_dates


# ── Momentum scoring at a point in time ──────────────────────────────────────

def _momentum_scores_at(price_matrix: pd.DataFrame, rebal_idx: int) -> pd.Series:
    # 12-1 month momentum: return from 252 bars ago to 21 bars ago
    # Skip last month to avoid short-term reversal contamination
    if rebal_idx < 252:
        return pd.Series(dtype=float)

    p_start = rebal_idx - 252
    p_end = rebal_idx - 21

    prices_start = price_matrix.iloc[p_start]
    prices_end = price_matrix.iloc[p_end]

    ret = prices_end / prices_start - 1

    # Drop tickers with missing data at either end
    valid = ret.dropna()
    return valid


# ── Metrics computation ───────────────────────────────────────────────────────

def _compute_metrics(portfolio_returns: pd.Series, spy_returns: pd.Series) -> dict:
    if portfolio_returns.empty:
        return {}

    trading_days = 252
    n = len(portfolio_returns)

    # Annualized return
    total_ret = (1 + portfolio_returns).prod() - 1
    years = n / trading_days
    ann_return = (1 + total_ret) ** (1 / max(years, 0.01)) - 1

    # Annualized volatility
    ann_vol = portfolio_returns.std() * np.sqrt(trading_days)

    # Sharpe (risk-free ~ 0 for simplicity; use 3% proxy)
    rf_daily = 0.03 / trading_days
    excess_daily = portfolio_returns - rf_daily
    sharpe = (excess_daily.mean() / portfolio_returns.std() * np.sqrt(trading_days)
              if portfolio_returns.std() > 0 else 0.0)

    # Max drawdown
    equity = (1 + portfolio_returns).cumprod()
    rolling_max = equity.cummax()
    drawdown = equity / rolling_max - 1
    max_dd = float(drawdown.min())

    # Calmar
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0.0

    # Win rate (positive return periods)
    win_rate = (portfolio_returns > 0).mean()

    best = float(portfolio_returns.max())
    worst = float(portfolio_returns.min())

    metrics = {
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "calmar": calmar,
        "win_rate": win_rate,
        "best_period": best,
        "worst_period": worst,
    }

    # Benchmark comparisons
    spy_aligned = spy_returns.reindex(portfolio_returns.index).fillna(0.0)
    if not spy_aligned.empty and spy_aligned.std() > 0:
        spy_total = (1 + spy_aligned).prod() - 1
        spy_years = len(spy_aligned) / trading_days
        spy_ann = (1 + spy_total) ** (1 / max(spy_years, 0.01)) - 1

        # Beta
        cov_matrix = np.cov(portfolio_returns.values, spy_aligned.values)
        beta = cov_matrix[0, 1] / cov_matrix[1, 1] if cov_matrix[1, 1] > 0 else 0.0

        # Correlation
        corr = portfolio_returns.corr(spy_aligned)

        # Alpha (excess return over beta-adjusted SPY)
        alpha = ann_return - (beta * spy_ann)

        # Information ratio: excess return / tracking error
        excess = portfolio_returns - spy_aligned
        ir = (excess.mean() / excess.std() * np.sqrt(trading_days)
              if excess.std() > 0 else 0.0)

        metrics.update({
            "spy_ann_return": spy_ann,
            "alpha": alpha,
            "beta": beta,
            "correlation": corr,
            "information_ratio": ir,
        })

    return metrics


# ── Monthly return aggregation ────────────────────────────────────────────────

def _daily_to_monthly(daily_returns: pd.Series) -> pd.DataFrame:
    if daily_returns.empty:
        return pd.DataFrame()

    monthly = (1 + daily_returns).resample("ME").prod() - 1
    df = monthly.reset_index()
    df.columns = ["date", "portfolio_return"]
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    return df


def _print_monthly_grid(monthly_df: pd.DataFrame, spy_monthly: pd.Series):
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    years = sorted(monthly_df["year"].unique())
    header = f"  {'Year':<6}" + "".join(f"{m:>8}" for m in month_names) + f"  {'Annual':>8}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for yr in years:
        yr_data = monthly_df[monthly_df["year"] == yr].set_index("month")["portfolio_return"]
        row_str = f"  {yr:<6}"
        annual_ret = (1 + yr_data).prod() - 1

        for m in range(1, 13):
            if m in yr_data.index and not pd.isna(yr_data[m]):
                val = yr_data[m] * 100
                row_str += f"  {val:+5.1f}%"
            else:
                row_str += f"  {'---':>6}"

        row_str += f"  {annual_ret * 100:+6.1f}%"
        print(row_str)

    print()


# ── Save outputs ──────────────────────────────────────────────────────────────

def _save_outputs(output_dir: Path, equity_curve: pd.DataFrame, monthly_returns: pd.DataFrame,
                  rebalance_log: list, summary_text: str):
    output_dir.mkdir(parents=True, exist_ok=True)

    equity_curve.to_csv(output_dir / "equity_curve.csv", index=False)
    monthly_returns.to_csv(output_dir / "monthly_returns.csv", index=False)

    if rebalance_log:
        rebal_df = pd.DataFrame(rebalance_log)
        rebal_df.to_csv(output_dir / "rebalance_log.csv", index=False)

    (output_dir / "summary.txt").write_text(summary_text)

    print(f"\n  Output saved to: {output_dir}/")
    print(f"    equity_curve.csv, monthly_returns.csv, rebalance_log.csv, summary.txt")


# ── Main backtest engine ──────────────────────────────────────────────────────

def run_backtest(
    start_date: date,
    end_date: date,
    num_longs: int,
    num_shorts: int,
    rebalance_days: int,
    with_costs: bool,
    use_full_score: bool,
    dev_mode: bool,
    output_dir: Path,
):
    from data.universe import get_universe

    print("\n" + "=" * 70)
    print("  Meridian Capital Partners / JARVIS — Historical Backtest")
    print("=" * 70)
    print()
    print("  *** KNOWN LIMITATIONS — READ BEFORE INTERPRETING RESULTS ***")
    print()
    for caveat in BIAS_CAVEATS:
        print(f"  {caveat}")
    print()
    if not use_full_score:
        print(f"  {MOMENTUM_NOTE}")
        print()
    else:
        print("  FULL-SCORE MODE: run_scoring() uses CURRENT data at every rebalance date.")
        print("  This adds significant look-ahead bias. Scores do NOT reflect historical data.")
        print()
    print("=" * 70)

    if dev_mode:
        os.environ["FORCE_DEV"] = "1"

    tickers = get_universe()
    if not tickers:
        print("ERROR: No tickers in universe. Run run_data.py first.")
        sys.exit(1)

    # Include SPY for benchmark
    all_fetch = list(set(tickers + ["SPY"]))

    print(f"\n  Universe: {len(tickers)} tickers")
    print(f"  Backtest range requested: {start_date} to {end_date}")
    print(f"  Building price matrix...")

    price_matrix = _build_price_matrix(all_fetch, start_date, end_date)

    if price_matrix.empty:
        print("ERROR: No price data found. Run run_data.py first to populate the database.")
        sys.exit(1)

    # Detect actual available date range
    available_start = price_matrix.index[0].date()
    available_end = price_matrix.index[-1].date()
    print(f"  Available price history: {available_start} to {available_end}")

    # Clip backtest window to available data
    # Need 252 bars of lookback before first rebalance date for momentum
    effective_start = max(start_date, available_start)
    effective_end = min(end_date, available_end)

    if effective_start >= effective_end:
        print("ERROR: No overlap between requested dates and available price history.")
        sys.exit(1)

    if effective_start != start_date:
        print(f"  WARNING: start_date clipped from {start_date} to {effective_start} (limited history in DB)")
    if effective_end != end_date:
        print(f"  WARNING: end_date clipped from {end_date} to {effective_end}")

    # Filter matrix to relevant tickers only (drop those with all-NaN)
    universe_cols = [t for t in tickers if t in price_matrix.columns]
    universe_matrix = price_matrix[universe_cols]
    universe_matrix = universe_matrix.dropna(axis=1, how="all")
    valid_tickers = list(universe_matrix.columns)

    full_matrix = price_matrix  # keep SPY accessible

    trading_dates = full_matrix.index
    backtest_dates = trading_dates[
        (trading_dates >= pd.Timestamp(effective_start)) &
        (trading_dates <= pd.Timestamp(effective_end))
    ]

    if len(backtest_dates) < rebalance_days * 2:
        print(f"ERROR: Too few trading days ({len(backtest_dates)}) for backtesting. Need more history.")
        sys.exit(1)

    print(f"  Effective backtest: {backtest_dates[0].date()} to {backtest_dates[-1].date()}")
    print(f"  Trading days: {len(backtest_dates)}")
    print(f"  Valid tickers with prices: {len(valid_tickers)}")

    # Generate rebalance dates from trading day index
    rebal_dates = _get_rebalance_dates(backtest_dates, effective_start, effective_end, rebalance_days)
    print(f"  Rebalance dates: {len(rebal_dates)} (every {rebalance_days} trading days)")
    print()

    # For full-score mode, run scoring once (current data)
    if use_full_score:
        print("  Running full scoring (current data, look-ahead bias acknowledged)...")
        from factors.composite import run_scoring
        scoring_result = run_scoring(valid_tickers)
        print(f"  Scoring complete: {len(scoring_result)} tickers scored")
    else:
        scoring_result = None

    # ── Walk-forward loop ─────────────────────────────────────────────────────
    rebalance_log = []
    period_returns = []  # list of (start_date, end_date, return)

    for r_idx, rebal_dt in enumerate(rebal_dates):
        # Integer position in the full trading date index
        full_idx = full_matrix.index.get_loc(rebal_dt)

        # Next rebalance date or end of backtest
        if r_idx + 1 < len(rebal_dates):
            next_rebal_dt = rebal_dates[r_idx + 1]
        else:
            next_rebal_dt = backtest_dates[-1]

        if rebal_dt == next_rebal_dt:
            continue

        next_full_idx = full_matrix.index.get_loc(next_rebal_dt)

        # Score tickers at this rebalance date
        if use_full_score and scoring_result is not None:
            # Use current composite scores (same for all periods — look-ahead)
            common = [t for t in valid_tickers if t in scoring_result.index]
            scores = scoring_result.loc[common, "composite"].dropna()
        else:
            # Momentum-only: need 252 bars lookback from this rebalance date
            if full_idx < 252:
                continue
            scores = _momentum_scores_at(universe_matrix, full_idx)
            scores = scores[scores.index.isin(valid_tickers)]

        if len(scores) < num_longs + num_shorts:
            log.warning(f"Not enough scored tickers at {rebal_dt.date()}: {len(scores)}")
            continue

        # Select longs (highest scores) and shorts (lowest scores)
        sorted_scores = scores.sort_values(ascending=False)
        selected_longs = sorted_scores.head(num_longs).index.tolist()
        selected_shorts = sorted_scores.tail(num_shorts).index.tolist()

        # Compute holding period returns for each book
        # Prices at rebalance date
        p_entry = universe_matrix.iloc[full_idx]
        p_exit = universe_matrix.iloc[next_full_idx]

        # Long book: equal-weight, long
        long_rets = []
        for t in selected_longs:
            if t in p_entry.index and t in p_exit.index:
                pe, px = p_entry[t], p_exit[t]
                if pd.notna(pe) and pd.notna(px) and pe > 0:
                    long_rets.append(px / pe - 1)

        # Short book: equal-weight, short (profit when price falls)
        short_rets = []
        for t in selected_shorts:
            if t in p_entry.index and t in p_exit.index:
                pe, px = p_entry[t], p_exit[t]
                if pd.notna(pe) and pd.notna(px) and pe > 0:
                    short_rets.append(-(px / pe - 1))

        if not long_rets or not short_rets:
            continue

        long_book_ret = float(np.mean(long_rets))
        short_book_ret = float(np.mean(short_rets))
        portfolio_ret = 0.5 * long_book_ret + 0.5 * short_book_ret

        if with_costs:
            portfolio_ret -= 0.0010  # 10bps round-trip

        period_returns.append({
            "start_date": rebal_dt.date(),
            "end_date": next_rebal_dt.date(),
            "long_book_ret": long_book_ret,
            "short_book_ret": short_book_ret,
            "portfolio_ret": portfolio_ret,
        })

        rebalance_log.append({
            "rebalance_date": str(rebal_dt.date()),
            "longs": ",".join(selected_longs),
            "shorts": ",".join(selected_shorts),
            "period_return": round(portfolio_ret, 6),
            "long_book_return": round(long_book_ret, 6),
            "short_book_return": round(short_book_ret, 6),
        })

    if not period_returns:
        print("ERROR: No rebalance periods completed. Insufficient price history.")
        sys.exit(1)

    print(f"  Completed {len(period_returns)} rebalance periods")

    # ── Build daily equity curve ──────────────────────────────────────────────
    # For each holding period, compute daily returns from the actual price moves
    # of the held book (true daily P&L, not linear interpolation)
    daily_port_returns = pd.Series(dtype=float)
    daily_long_returns = pd.Series(dtype=float)
    daily_short_returns = pd.Series(dtype=float)

    for period in period_returns:
        start_dt = pd.Timestamp(period["start_date"])
        end_dt = pd.Timestamp(period["end_date"])

        start_pos = full_matrix.index.get_loc(start_dt)
        end_pos = full_matrix.index.get_loc(end_dt)

        hold_dates = full_matrix.index[start_pos:end_pos + 1]

        if len(hold_dates) < 2:
            continue

        # Get the held tickers for this period from rebalance_log
        period_log = next((r for r in rebalance_log if r["rebalance_date"] == str(period["start_date"])), None)
        if not period_log:
            continue

        held_longs = [t for t in period_log["longs"].split(",") if t in universe_matrix.columns]
        held_shorts = [t for t in period_log["shorts"].split(",") if t in universe_matrix.columns]

        if not held_longs or not held_shorts:
            continue

        # Slice prices for held tickers over holding period
        long_prices = universe_matrix.loc[hold_dates, held_longs].ffill()
        short_prices = universe_matrix.loc[hold_dates, held_shorts].ffill()

        # Daily returns for each book (equal weight)
        long_daily = long_prices.pct_change().iloc[1:].mean(axis=1)
        short_daily = -short_prices.pct_change().iloc[1:].mean(axis=1)

        period_daily = 0.5 * long_daily + 0.5 * short_daily

        if with_costs and len(period_daily) > 0:
            # Apply cost at first day of period
            period_daily.iloc[0] -= 0.0010

        daily_port_returns = pd.concat([daily_port_returns, period_daily])
        daily_long_returns = pd.concat([daily_long_returns, long_daily])
        daily_short_returns = pd.concat([daily_short_returns, short_daily])

    # Remove duplicate dates (overlap at period boundaries)
    daily_port_returns = daily_port_returns[~daily_port_returns.index.duplicated(keep="first")]
    daily_long_returns = daily_long_returns[~daily_long_returns.index.duplicated(keep="first")]
    daily_short_returns = daily_short_returns[~daily_short_returns.index.duplicated(keep="first")]
    daily_port_returns = daily_port_returns.sort_index()
    daily_long_returns = daily_long_returns.sort_index()
    daily_short_returns = daily_short_returns.sort_index()

    # SPY daily returns aligned to same dates
    spy_daily = _compute_spy_returns(full_matrix)
    spy_daily = spy_daily.reindex(daily_port_returns.index).fillna(0.0)

    # ── Equity curves ─────────────────────────────────────────────────────────
    equity_curve = (1 + daily_port_returns).cumprod()
    spy_curve = (1 + spy_daily).cumprod()
    rolling_max = equity_curve.cummax()
    drawdown_series = equity_curve / rolling_max - 1

    # ── Metrics ───────────────────────────────────────────────────────────────
    metrics = _compute_metrics(daily_port_returns, spy_daily)

    trading_days = 252
    long_ann = (1 + daily_long_returns).prod() ** (trading_days / max(len(daily_long_returns), 1)) - 1
    short_ann = (1 + daily_short_returns).prod() ** (trading_days / max(len(daily_short_returns), 1)) - 1

    # Monthly returns for grid
    monthly_df = _daily_to_monthly(daily_port_returns)
    spy_monthly = (1 + spy_daily).resample("ME").prod() - 1

    # Monthly hit rates for factor analysis
    period_df = pd.DataFrame(period_returns)
    long_hit_rate = (period_df["long_book_ret"] > 0).mean() if not period_df.empty else 0.0
    spy_monthly_aligned = spy_monthly.resample("ME").last() if not spy_monthly.empty else pd.Series()

    # Short hit rate: short book return positive = underlying stock fell
    short_hit_rate = (period_df["short_book_ret"] > 0).mean() if not period_df.empty else 0.0

    # ── Build summary text ────────────────────────────────────────────────────
    lines = []
    lines.append("=" * 70)
    lines.append(f"  BACKTEST RESULTS: {effective_start} to {effective_end}")
    lines.append(f"  Meridian Capital Partners / JARVIS")
    lines.append("=" * 70)
    lines.append("")
    lines.append("  KNOWN LIMITATIONS:")
    for c in BIAS_CAVEATS:
        lines.append(f"  {c}")
    lines.append("")
    lines.append(f"  Scoring method: {'Full composite (LOOK-AHEAD BIAS)' if use_full_score else 'Momentum only (price-based, minimal look-ahead)'}")
    lines.append(f"  Transaction costs: {'10bps/rebalance' if with_costs else 'Not modeled'}")
    lines.append("")
    lines.append("  PERFORMANCE SUMMARY")
    lines.append(f"  {'Annualized Return:':30s} {metrics.get('ann_return', 0) * 100:+7.2f}%")
    lines.append(f"  {'Annualized Volatility:':30s} {metrics.get('ann_vol', 0) * 100:7.2f}%")
    lines.append(f"  {'Sharpe Ratio:':30s} {metrics.get('sharpe', 0):7.2f}")
    lines.append(f"  {'Max Drawdown:':30s} {metrics.get('max_dd', 0) * 100:+7.2f}%")
    lines.append(f"  {'Calmar Ratio:':30s} {metrics.get('calmar', 0):7.2f}")
    lines.append(f"  {'Win Rate (periods):':30s} {metrics.get('win_rate', 0) * 100:7.1f}%")
    lines.append(f"  {'Best Period:':30s} {metrics.get('best_period', 0) * 100:+7.2f}%")
    lines.append(f"  {'Worst Period:':30s} {metrics.get('worst_period', 0) * 100:+7.2f}%")
    lines.append("")
    lines.append("  vs BENCHMARK (SPY)")
    if "spy_ann_return" in metrics:
        lines.append(f"  {'SPY Annualized Return:':30s} {metrics['spy_ann_return'] * 100:+7.2f}%")
        lines.append(f"  {'Excess Return (Alpha):':30s} {metrics.get('alpha', 0) * 100:+7.2f}%")
        lines.append(f"  {'Beta to SPY:':30s} {metrics.get('beta', 0):7.2f}")
        lines.append(f"  {'Correlation to SPY:':30s} {metrics.get('correlation', 0):7.2f}")
        lines.append(f"  {'Information Ratio:':30s} {metrics.get('information_ratio', 0):7.2f}")
    else:
        lines.append("  SPY data not available for benchmark comparison")
    lines.append("")
    lines.append("  FACTOR ANALYSIS")
    lines.append(f"  {'Long Book Return (ann.):':30s} {long_ann * 100:+7.2f}%")
    lines.append(f"  {'Short Book Return (ann.):':30s} {short_ann * 100:+7.2f}%")
    lines.append(f"  {'Long Hit Rate:':30s} {long_hit_rate * 100:7.1f}%  (periods: long book > 0)")
    lines.append(f"  {'Short Hit Rate:':30s} {short_hit_rate * 100:7.1f}%  (periods: short book profitable)")
    lines.append(f"  {'Rebalance Periods:':30s} {len(period_returns):7d}")
    lines.append("")

    summary_text = "\n".join(lines)

    # ── Print to console ──────────────────────────────────────────────────────
    print()
    print(summary_text)

    # Monthly grid printed to console only (too wide for summary.txt nicely)
    if not monthly_df.empty:
        print("  MONTHLY RETURNS GRID")
        _print_monthly_grid(monthly_df, spy_monthly)

    # ── Build output DataFrames ───────────────────────────────────────────────
    equity_df = pd.DataFrame({
        "date": equity_curve.index.strftime("%Y-%m-%d"),
        "portfolio_value": equity_curve.values.round(6),
        "spy_value": spy_curve.reindex(equity_curve.index).ffill().values.round(6),
        "drawdown": drawdown_series.values.round(6),
    })

    # Build monthly returns DataFrame with SPY column
    if not monthly_df.empty:
        monthly_out = monthly_df[["year", "month", "portfolio_return"]].copy()
        spy_m_df = spy_monthly.reset_index()
        spy_m_df.columns = ["date", "spy_return"]
        spy_m_df["year"] = spy_m_df["date"].dt.year
        spy_m_df["month"] = spy_m_df["date"].dt.month
        monthly_out = monthly_out.merge(
            spy_m_df[["year", "month", "spy_return"]], on=["year", "month"], how="left"
        )
        monthly_out["excess_return"] = monthly_out["portfolio_return"] - monthly_out["spy_return"].fillna(0)
        monthly_out = monthly_out.round(6)
    else:
        monthly_out = pd.DataFrame()

    # Save
    _save_outputs(output_dir, equity_df, monthly_out, rebalance_log, summary_text)

    return {
        "metrics": metrics,
        "daily_returns": daily_port_returns,
        "equity_curve": equity_curve,
        "monthly_returns": monthly_df,
        "rebalance_log": rebalance_log,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Backtest the Jarvis long-short scoring engine on historical data.\n\n"
            "IMPORTANT: Results have look-ahead bias (current fundamentals used) and\n"
            "survivorship bias (current S&P 500 members only). Use for directional\n"
            "validation only.\n\n"
            "Examples:\n"
            "  python run_backtest.py --start 2021-01-01 --end 2024-12-31\n"
            "  python run_backtest.py --dev --start 2022-01-01\n"
            "  python run_backtest.py --with-costs --num-longs 10 --num-shorts 10\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    default_start = (date.today() - timedelta(days=365 * 3)).strftime("%Y-%m-%d")
    default_end = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    parser.add_argument("--start", default=default_start, metavar="YYYY-MM-DD",
                        help=f"Backtest start date (default: 3 years ago = {default_start})")
    parser.add_argument("--end", default=default_end, metavar="YYYY-MM-DD",
                        help=f"Backtest end date (default: yesterday = {default_end})")
    parser.add_argument("--method", choices=["mvo", "conviction"], default="conviction",
                        help="Portfolio construction method (default: conviction)")
    parser.add_argument("--num-longs", type=int, default=20,
                        help="Number of long positions (default: 20)")
    parser.add_argument("--num-shorts", type=int, default=20,
                        help="Number of short positions (default: 20)")
    parser.add_argument("--rebalance-days", type=int, default=21,
                        help="Rebalance frequency in trading days (default: 21 = monthly)")
    parser.add_argument("--with-costs", action="store_true",
                        help="Add 10bps round-trip transaction cost per rebalance")
    parser.add_argument("--dev", action="store_true",
                        help="Use dev_tickers only (10 tickers, fast)")
    parser.add_argument("--output-dir", default=None, metavar="PATH",
                        help="Output directory (default: output/backtest/)")
    parser.add_argument("--full-score", action="store_true",
                        help="Use full 8-factor composite scoring (WARNING: significant look-ahead bias)")

    return parser.parse_args()


def main():
    args = parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()

    if start_date >= end_date:
        print("ERROR: --start must be before --end")
        sys.exit(1)

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = ROOT / "output" / "backtest"

    run_backtest(
        start_date=start_date,
        end_date=end_date,
        num_longs=args.num_longs,
        num_shorts=args.num_shorts,
        rebalance_days=args.rebalance_days,
        with_costs=args.with_costs,
        use_full_score=args.full_score,
        dev_mode=args.dev,
        output_dir=output_dir,
    )


if __name__ == "__main__":
    main()
