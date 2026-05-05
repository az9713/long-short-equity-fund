"""
run_data.py — Meridian Capital Partners / JARVIS
Layer 1 data refresh entry point.

Usage:
  python run_data.py [--no-filings] [--no-13f] [--dev]
"""

import os
import sys
import argparse
import time
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def parse_args():
    parser = argparse.ArgumentParser(description="Meridian Capital Partners — Data Layer Refresh")
    parser.add_argument("--no-filings", action="store_true", help="Skip 10-K/10-Q/8-K SEC filings")
    parser.add_argument("--no-13f", action="store_true", help="Skip institutional 13F filings")
    parser.add_argument("--dev", action="store_true", help="Force dev mode (10 tickers)")
    return parser.parse_args()


def main():
    args = parse_args()

    # Set FORCE_DEV before importing data modules so universe.get_universe() picks it up
    if args.dev:
        os.environ["FORCE_DEV"] = "1"

    from utils import get_logger, get_db
    from data import universe, market_data, fundamentals, sec_data, institutional
    from data import short_interest, estimates, earnings_calendar

    log = get_logger("run_data")
    log.info("=" * 60)
    log.info("Meridian Capital Partners — JARVIS Data Layer Refresh")
    log.info("=" * 60)

    if args.dev or os.getenv("FORCE_DEV") == "1":
        log.info("Mode: DEV (10 tickers)")
    else:
        log.info("Mode: FULL (S&P 500)")

    start_time = time.time()

    # ── Step 1: Universe ──────────────────────────────────────────────────────
    log.info("[1/8] Updating universe...")
    tickers = universe.get_universe()
    log.info(f"Universe: {len(tickers)} tickers")

    if not tickers:
        log.error("No tickers in universe — aborting")
        sys.exit(1)

    # ── Step 2: Market Data ───────────────────────────────────────────────────
    log.info("[2/8] Updating market prices...")
    market_data.update_prices(tickers)

    # ── Step 3: Fundamentals ─────────────────────────────────────────────────
    log.info("[3/8] Updating fundamentals...")
    fundamentals.update_fundamentals(tickers)

    # ── Step 4: SEC Data ─────────────────────────────────────────────────────
    log.info("[4/8] Updating SEC data...")
    sec_data.update_sec_data(tickers, no_filings=args.no_filings)

    # ── Step 5: Institutional ─────────────────────────────────────────────────
    log.info("[5/8] Updating institutional holdings...")
    institutional.update_institutional(tickers, skip=args.no_13f)

    # ── Step 6: Short Interest ────────────────────────────────────────────────
    log.info("[6/8] Updating short interest...")
    short_interest.update_short_interest(tickers)

    # ── Step 7: Analyst Estimates ─────────────────────────────────────────────
    log.info("[7/8] Updating analyst estimates...")
    estimates.update_estimates(tickers)

    # ── Step 8: Earnings Calendar ─────────────────────────────────────────────
    log.info("[8/8] Updating earnings calendar...")
    earnings_calendar.update_earnings_calendar(tickers)

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = time.time() - start_time

    conn = get_db()
    try:
        price_count = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        insider_count = conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0]
        filings_count = conn.execute("SELECT COUNT(*) FROM sec_filings").fetchone()[0]
    except Exception:
        price_count = insider_count = filings_count = 0
    finally:
        conn.close()

    log.info("=" * 60)
    log.info("JARVIS Data Refresh Complete")
    log.info(f"  Tickers updated:        {len(tickers)}")
    log.info(f"  Price bars stored:      {price_count:,}")
    log.info(f"  Insider transactions:   {insider_count:,}")
    log.info(f"  Filings cached:         {filings_count:,}")
    log.info(f"  Elapsed time:           {elapsed:.1f}s")
    log.info("=" * 60)

    print(f"\nSummary:")
    print(f"  Tickers updated:      {len(tickers)}")
    print(f"  Price bars:           {price_count:,}")
    print(f"  Insider transactions: {insider_count:,}")
    print(f"  Filings cached:       {filings_count:,}")
    print(f"  Elapsed:              {elapsed:.1f}s")


if __name__ == "__main__":
    main()
