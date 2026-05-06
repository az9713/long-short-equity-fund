"""
run_analysis.py — Meridian Capital Partners / JARVIS
Layer 3 AI Analysis engine entry point.

Usage:
  python run_analysis.py --estimate-cost       # print cost estimate (~$0) and exit
  python run_analysis.py --ticker AAPL         # analyze single ticker
  python run_analysis.py --sector Technology   # analyze entire sector
  python run_analysis.py                       # full run (top 20 long + top 20 short)
"""

import os
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Meridian Capital Partners — JARVIS AI Analysis Engine"
    )
    parser.add_argument("--estimate-cost", action="store_true",
                        help="Print cost estimate and exit without running analysis")
    parser.add_argument("--ticker", type=str, default=None,
                        help="Analyze a single ticker")
    parser.add_argument("--sector", type=str, default=None,
                        help="Analyze all tickers in a sector")
    return parser.parse_args()


def _check_api_key() -> bool:
    if not os.getenv("OPENROUTER_API_KEY"):
        print("  WARNING: OPENROUTER_API_KEY not set.")
        print("  AI analysis will be skipped. Combined score = 100% quant.")
        return False
    return True


def _run_ticker_analysis(ticker: str) -> dict:
    from analysis.earnings_analyzer import analyze_earnings
    from analysis.filing_analyzer import analyze_filing
    from analysis.risk_analyzer import analyze_risk
    from analysis.insider_analyzer import analyze_insider

    results = {}
    print(f"  Analyzing {ticker}...")

    for name, fn in [
        ("earnings", analyze_earnings),
        ("filing", analyze_filing),
        ("risk", analyze_risk),
        ("insider", analyze_insider),
    ]:
        try:
            r = fn(ticker)
            results[name] = r
            status = "ok" if r else "no data"
            print(f"    [{name}] {status}")
        except Exception as e:
            results[name] = None
            print(f"    [{name}] ERROR: {e}")

    return results


def _load_scored_universe() -> "pd.DataFrame":
    import pandas as pd
    path = ROOT / "output" / "scored_universe_latest.csv"
    if not path.exists():
        print(f"  ERROR: {path} not found.")
        print("  Run 'python run_scoring.py' first to generate the scored universe.")
        sys.exit(1)
    df = pd.read_csv(path)
    print(f"  Loaded scored universe: {len(df)} tickers")
    return df


def main():
    args = parse_args()

    from utils import get_logger, get_config
    log = get_logger("run_analysis")

    print("=" * 60)
    print("  Meridian Capital Partners — JARVIS AI Analysis Engine")
    print("=" * 60)

    # --- Estimate cost mode ---
    if args.estimate_cost:
        cfg = get_config()
        model = cfg.get("ai", {}).get("model", "unknown")
        print(f"\n  Model: {model}")
        is_free = ":free" in (model or "")
        tier_label = "Free tier" if is_free else "Paid tier"
        print(f"  {tier_label}: estimated cost ~$0.00 for ~160 calls" if is_free
              else f"  {tier_label}: cost depends on model pricing for ~160 calls")
        print("  Rate limit: 15 RPM. 40 tickers x 4 analyzers = 160 calls @ 13/min = ~12 min")
        print("  Cache hits reduce actual calls further.")
        print()
        return

    has_key = _check_api_key()

    # --- Single ticker mode ---
    if args.ticker:
        ticker = args.ticker.upper()
        print(f"\n  Mode: single ticker [{ticker}]")

        if has_key:
            _run_ticker_analysis(ticker)

        # Combine with a synthetic scored row if no CSV exists
        try:
            df = _load_scored_universe()
            if "ticker" in df.columns:
                row_df = df[df["ticker"] == ticker]
            else:
                row_df = df[df.index == ticker]

            if not row_df.empty:
                from analysis.combined_score import run_combined_scoring
                from analysis.report_generator import run_report_generation
                combined_df = run_combined_scoring(row_df)
                run_report_generation(combined_df)
            else:
                print(f"  {ticker} not in scored universe — report generation skipped")
        except SystemExit:
            print("  Skipping combined score / report (no scored universe CSV)")

        _print_cost_summary()
        return

    # --- Sector mode ---
    if args.sector:
        sector_target = args.sector
        print(f"\n  Mode: sector [{sector_target}]")
        df = _load_scored_universe()

        sector_df = df[df["sector"].str.lower() == sector_target.lower()]
        if sector_df.empty:
            # Try partial match
            sector_df = df[df["sector"].str.lower().str.contains(sector_target.lower(), na=False)]

        if sector_df.empty:
            print(f"  No tickers found for sector '{sector_target}'")
            print(f"  Available sectors: {sorted(df['sector'].unique())}")
            sys.exit(1)

        tickers = sector_df["ticker"].tolist()
        print(f"  Sector has {len(tickers)} tickers: {tickers}")

        ticker_results = {}
        if has_key:
            for ticker in tickers:
                ticker_results[ticker] = _run_ticker_analysis(ticker)

            # Sector-level analysis
            if ticker_results:
                from analysis.sector_analysis import analyze_sector
                combined_for_sector = {
                    t: r for t, r in ticker_results.items() if any(v for v in r.values())
                }
                if combined_for_sector:
                    print(f"\n  Running sector analysis for {sector_target}...")
                    sector_result = analyze_sector(sector_target, combined_for_sector)
                    if sector_result:
                        print(f"  Sector outlook: {sector_result.get('sector_outlook', 'N/A')}")
                        print(f"  Top long idea: {sector_result.get('top_long_idea', 'N/A')}")
                        print(f"  Top short idea: {sector_result.get('top_short_idea', 'N/A')}")

        from analysis.combined_score import run_combined_scoring
        from analysis.report_generator import run_report_generation
        combined_df = run_combined_scoring(sector_df)
        run_report_generation(combined_df)

        _print_cost_summary()
        return

    # --- Full run ---
    print("\n  Mode: full run (top 20 LONG + top 20 SHORT)")
    df = _load_scored_universe()

    longs = df[df["signal"] == "LONG"].nlargest(20, "composite")
    shorts = df[df["signal"] == "SHORT"].nsmallest(20, "composite")
    candidates = _concat_dfs(longs, shorts)

    print(f"  Candidates: {len(longs)} LONG, {len(shorts)} SHORT = {len(candidates)} total")

    # Step 1: Run all analyzers per ticker
    if has_key:
        for _, row in candidates.iterrows():
            ticker = row["ticker"]
            _run_ticker_analysis(ticker)

    # Step 2: Sector analysis per sector
    if has_key:
        _run_sector_analyses(candidates, df)

    # Step 3: Combined scoring
    from analysis.combined_score import run_combined_scoring
    combined_df = run_combined_scoring(candidates)

    # Step 4: Generate reports
    from analysis.report_generator import run_report_generation
    run_report_generation(combined_df)

    # Step 5: Print summary
    _print_run_summary(combined_df, longs, shorts)
    _print_cost_summary()


def _concat_dfs(longs, shorts):
    import pandas as pd
    return pd.concat([longs, shorts], ignore_index=True)


def _run_sector_analyses(candidates, full_df):
    from analysis.sector_analysis import analyze_sector
    from analysis.cache import get_latest_for_ticker

    sectors = candidates["sector"].unique()
    for sector in sectors:
        sector_tickers = candidates[candidates["sector"] == sector]["ticker"].tolist()
        ticker_results = {}
        for ticker in sector_tickers:
            cached = get_latest_for_ticker(ticker)
            if cached:
                ticker_results[ticker] = cached

        if ticker_results:
            print(f"\n  Sector analysis: {sector} ({len(ticker_results)} tickers)")
            try:
                result = analyze_sector(sector, ticker_results)
                if result:
                    print(f"    Outlook: {result.get('sector_outlook', 'N/A')}  "
                          f"Long: {result.get('top_long_idea', 'N/A')}  "
                          f"Short: {result.get('top_short_idea', 'N/A')}")
            except Exception as e:
                print(f"    Sector analysis failed for {sector}: {e}")


def _print_run_summary(combined_df, longs, shorts):
    print(f"\n{'='*60}")
    print("  JARVIS — AI Analysis Summary")
    print(f"{'='*60}")
    print(f"  Tickers analyzed:   {len(combined_df)}")
    ai_count = combined_df["has_ai_analysis"].sum() if "has_ai_analysis" in combined_df.columns else 0
    print(f"  With AI analysis:   {ai_count}")

    print(f"\n  Top 5 LONG (combined score):")
    print(f"  {'Ticker':<8} {'Quant':>7} {'Combined':>10}  {'Sector'}")
    print(f"  {'-'*55}")
    top_longs = combined_df[combined_df["signal"] == "LONG"].nlargest(5, "combined_score") \
        if "combined_score" in combined_df.columns else combined_df[combined_df["signal"] == "LONG"].head(5)
    for _, row in top_longs.iterrows():
        print(f"  {row['ticker']:<8} {row.get('composite', 'N/A'):>7.1f} "
              f"{row.get('combined_score', 'N/A'):>10.1f}  {row.get('sector', 'N/A')}")

    print(f"\n  Top 5 SHORT (combined score):")
    print(f"  {'Ticker':<8} {'Quant':>7} {'Combined':>10}  {'Sector'}")
    print(f"  {'-'*55}")
    top_shorts = combined_df[combined_df["signal"] == "SHORT"].nsmallest(5, "combined_score") \
        if "combined_score" in combined_df.columns else combined_df[combined_df["signal"] == "SHORT"].head(5)
    for _, row in top_shorts.iterrows():
        print(f"  {row['ticker']:<8} {row.get('composite', 'N/A'):>7.1f} "
              f"{row.get('combined_score', 'N/A'):>10.1f}  {row.get('sector', 'N/A')}")
    print()


def _print_cost_summary():
    from analysis.cost_tracker import get_total_tokens
    tokens = get_total_tokens()
    if tokens["total"] == 0:
        return

    input_cost = tokens["input"] / 1_000_000 * 0.075
    output_cost = tokens["output"] / 1_000_000 * 0.300
    total_cost = input_cost + output_cost

    print(f"\n  Token Usage Summary")
    print(f"  {'Input tokens:':<20} {tokens['input']:>12,}")
    print(f"  {'Output tokens:':<20} {tokens['output']:>12,}")
    print(f"  {'Total tokens:':<20} {tokens['total']:>12,}")
    print(f"  {'Est. cost (ref):':<20} ${total_cost:.4f}  (free tier — actual: $0.00)")
    print()


if __name__ == "__main__":
    main()
