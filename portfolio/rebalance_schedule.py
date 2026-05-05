import pandas as pd
from datetime import date, timedelta
from utils import get_logger
from data.earnings_calendar import days_to_earnings

log = get_logger(__name__)

# FOMC meeting dates for 2026 (both days of each meeting)
_FOMC_DATES_2026 = [
    date(2026, 1, 28), date(2026, 1, 29),
    date(2026, 3, 18), date(2026, 3, 19),
    date(2026, 4, 29), date(2026, 4, 30),
    date(2026, 6, 17), date(2026, 6, 18),
    date(2026, 7, 29), date(2026, 7, 30),
    date(2026, 9, 16), date(2026, 9, 17),
    date(2026, 10, 28), date(2026, 10, 29),
    date(2026, 12, 16), date(2026, 12, 17),
]


def _third_friday(year: int, month: int) -> date:
    # Find first day of month, advance to first Friday, add 14 days
    d = date(year, month, 1)
    # weekday(): Monday=0 ... Friday=4
    days_to_first_friday = (4 - d.weekday()) % 7
    first_friday = d + timedelta(days=days_to_first_friday)
    return first_friday + timedelta(weeks=2)


def _days_to_fomc(today: date) -> int | None:
    future = [d for d in _FOMC_DATES_2026 if d >= today]
    if not future:
        return None
    return (min(future) - today).days


def _days_to_opex(today: date) -> int:
    # Check this month and next
    candidates = []
    for offset in range(3):
        year = today.year
        month = today.month + offset
        if month > 12:
            month -= 12
            year += 1
        opex = _third_friday(year, month)
        if opex >= today:
            candidates.append(opex)
    if not candidates:
        return 999
    return (min(candidates) - today).days


def get_rebalance_warnings(scored_df: pd.DataFrame) -> list[str]:
    today = pd.Timestamp.utcnow().date()
    warnings = []

    # Earnings warnings for LONG + SHORT candidates only
    if "ticker" in scored_df.columns:
        tickers_col = scored_df["ticker"]
        signals_col = scored_df.get("signal", pd.Series(["NEUTRAL"] * len(scored_df)))
    elif scored_df.index.name == "ticker":
        tickers_col = scored_df.index.to_series()
        signals_col = scored_df.get("signal", pd.Series(["NEUTRAL"] * len(scored_df), index=scored_df.index))
    else:
        tickers_col = pd.Series(dtype=str)
        signals_col = pd.Series(dtype=str)

    for ticker, signal in zip(tickers_col, signals_col):
        if signal not in ("LONG", "SHORT"):
            continue
        dte = days_to_earnings(ticker)
        if dte is not None and 0 <= dte <= 2:
            warnings.append(f"WARN: {ticker} earnings in {dte} days")

    # FOMC warning
    days_fomc = _days_to_fomc(today)
    if days_fomc is not None and 0 <= days_fomc <= 5:
        warnings.append(
            f"WARN: FOMC meeting in {days_fomc} days, consider reduced sizing"
        )

    # Options expiration warning
    days_opex = _days_to_opex(today)
    if 0 <= days_opex <= 3:
        warnings.append(f"WARN: OpEx in {days_opex} days")

    return warnings
