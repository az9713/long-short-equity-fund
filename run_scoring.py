"""
run_scoring.py — Meridian Capital Partners / JARVIS
Layer 2 scoring engine entry point.

Usage:
  python run_scoring.py                       # score full universe
  python run_scoring.py --ticker AAPL         # single stock (print all subfactor scores)
  python run_scoring.py --sector Technology   # single sector
"""

import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def parse_args():
    parser = argparse.ArgumentParser(description="Meridian Capital Partners — JARVIS Scoring Engine")
    parser.add_argument("--ticker", type=str, default=None, help="Score a single ticker")
    parser.add_argument("--sector", type=str, default=None, help="Score a single sector")
    return parser.parse_args()


def _print_single_ticker(ticker: str, result):
    """Print detailed subfactor breakdown for a single ticker."""
    if ticker not in result.index:
        print(f"Ticker {ticker} not found in results.")
        return

    row = result.loc[ticker]
    print(f"\n{'='*60}")
    print(f"  JARVIS Scoring Detail: {ticker}")
    print(f"  Sector: {row.get('sector', 'N/A')}")
    print(f"{'='*60}")

    factor_cols = ["momentum", "value", "quality", "growth",
                   "revisions", "short_interest", "insider", "institutional"]

    print("\n  Factor Scores (0-100, sector-relative):")
    for f in factor_cols:
        val = row.get(f, None)
        bar = "#" * int((val or 0) / 5) if val is not None else ""
        print(f"  {f:<18} {val:>6.1f}  {bar}" if val is not None else f"  {f:<18}    N/A")

    print(f"\n  Composite Score:  {row.get('composite', 'N/A')}")
    print(f"  Signal:           {row.get('signal', 'N/A')}")
    print(f"\n  Quality Diagnostics:")
    print(f"    Piotroski F:    {row.get('piotroski_f', 'N/A')}")
    print(f"    Altman Z:       {row.get('altman_z', 'N/A')}  ({row.get('altman_label', 'N/A')})")
    print()


def main():
    args = parse_args()

    from utils import get_logger
    from data.universe import get_universe
    from factors.base import get_sector_map
    from factors.composite import run_scoring
    from factors.crowding import detect_crowding

    log = get_logger("run_scoring")
    log.info("=" * 60)
    log.info("Meridian Capital Partners — JARVIS Scoring Engine")
    log.info("=" * 60)

    # Determine ticker set
    tickers = get_universe()

    if args.ticker:
        target = args.ticker.upper()
        if target not in tickers:
            tickers = tickers + [target]  # add it even if not in universe
        tickers = [target]
        log.info(f"Single ticker mode: {target}")

    elif args.sector:
        sector_map = get_sector_map(tickers)
        sector_tickers = [t for t, s in sector_map.items() if args.sector.lower() in s.lower()]
        if not sector_tickers:
            print(f"No tickers found for sector '{args.sector}'")
            sys.exit(1)
        tickers = sector_tickers
        log.info(f"Sector mode: {args.sector} ({len(tickers)} tickers)")

    # Run scoring
    result = run_scoring(tickers)

    if result.empty:
        print("No results produced. Ensure data has been populated via run_data.py first.")
        sys.exit(1)

    # Single ticker detail
    if args.ticker:
        _print_single_ticker(args.ticker.upper(), result)
        return

    # Summary output
    longs = result[result["signal"] == "LONG"].sort_values("composite", ascending=False)
    shorts = result[result["signal"] == "SHORT"].sort_values("composite", ascending=True)

    print(f"\n{'='*60}")
    print("  JARVIS — Scoring Summary")
    print(f"{'='*60}")
    print(f"\n  Universe scored:  {len(result)} tickers")
    print(f"  LONG candidates:  {len(longs)}")
    print(f"  SHORT candidates: {len(shorts)}")

    print(f"\n  Top 5 LONG Candidates:")
    print(f"  {'Ticker':<8} {'Score':>7}  {'Sector'}")
    print(f"  {'-'*50}")
    for t, row in longs.head(5).iterrows():
        print(f"  {t:<8} {row['composite']:>7.1f}  {row['sector']}")

    print(f"\n  Top 5 SHORT Candidates:")
    print(f"  {'Ticker':<8} {'Score':>7}  {'Sector'}")
    print(f"  {'-'*50}")
    for t, row in shorts.head(5).iterrows():
        print(f"  {t:<8} {row['composite']:>7.1f}  {row['sector']}")

    # Crowding warnings
    print(f"\n  Crowding Detection:")
    try:
        crowding = detect_crowding()
        if not crowding:
            print("  No crowding data yet (need >= 60 days of history)")
        else:
            warned = False
            for pair_key, info in crowding.items():
                if info.get("is_crowded"):
                    print(f"  WARNING: {info['warning_message']}")
                    warned = True
            if not warned:
                print("  No crowding detected")
    except Exception as e:
        print(f"  Crowding check failed: {e}")

    print(f"\n  Output saved to: output/scored_universe_latest.csv")
    print()


if __name__ == "__main__":
    main()
