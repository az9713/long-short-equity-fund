import time
import pandas as pd
import yfinance as yf
from datetime import datetime
from utils import get_db, get_logger

log = get_logger(__name__)


def _create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals (
            ticker TEXT NOT NULL,
            period_end TEXT NOT NULL,
            report_date TEXT,
            ni REAL,
            revenue REAL,
            gross_profit REAL,
            operating_income REAL,
            ebit REAL,
            ebitda REAL,
            cfo REAL,
            capex REAL,
            fcf REAL,
            total_assets REAL,
            total_equity REAL,
            total_debt REAL,
            current_assets REAL,
            current_liabilities REAL,
            working_capital REAL,
            retained_earnings REAL,
            shares_outstanding REAL,
            dividends_paid REAL,
            buybacks REAL,
            r_and_d_expense REAL,
            asset_turnover REAL,
            PRIMARY KEY (ticker, period_end)
        )
    """)
    conn.commit()


def _safe_val(df: pd.DataFrame, *keys) -> float | None:
    """Try multiple key names and return first match, or None."""
    for key in keys:
        if key in df.index:
            vals = df.loc[key].dropna()
            if not vals.empty:
                return float(vals.iloc[0])
    return None


def _extract_quarterly(ticker_obj, ticker: str) -> list[dict]:
    """Extract quarterly fundamentals from yfinance Ticker object."""
    records = []
    try:
        inc = ticker_obj.quarterly_income_stmt
        bal = ticker_obj.quarterly_balance_sheet
        cf = ticker_obj.quarterly_cashflow
    except Exception as e:
        log.warning(f"Failed to fetch statements for {ticker}: {e}")
        return records

    if inc is None or inc.empty:
        log.warning(f"Empty income statement for {ticker}")
        return records

    for col in inc.columns:
        try:
            period_end = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)[:10]

            def sv(*keys):
                for df in [inc, bal, cf]:
                    if df is None or df.empty:
                        continue
                    for k in keys:
                        if k in df.index and col in df.columns:
                            v = df.loc[k, col]
                            if pd.notna(v):
                                return float(v)
                return None

            ni = sv("Net Income", "Net Income Common Stockholders")
            revenue = sv("Total Revenue")
            gross_profit = sv("Gross Profit")
            operating_income = sv("Operating Income", "EBIT")
            ebit = sv("EBIT", "Operating Income")
            ebitda = sv("EBITDA", "Normalized EBITDA")
            cfo = sv("Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
            capex_raw = sv("Capital Expenditure", "Capital Expenditures")
            # capex from yfinance is usually negative; take absolute
            capex = abs(capex_raw) if capex_raw is not None else None
            fcf = (cfo - capex) if (cfo is not None and capex is not None) else None
            total_assets = sv("Total Assets")
            total_equity = sv("Stockholders Equity", "Total Equity Gross Minority Interest", "Common Stock Equity")
            total_debt = sv("Total Debt", "Long Term Debt And Capital Lease Obligation")
            current_assets = sv("Current Assets")
            current_liabilities = sv("Current Liabilities")
            working_capital = (
                (current_assets - current_liabilities)
                if (current_assets is not None and current_liabilities is not None)
                else None
            )
            retained_earnings = sv("Retained Earnings")
            shares_outstanding = sv("Diluted Average Shares", "Ordinary Shares Number", "Share Issued")
            dividends_paid_raw = sv("Cash Dividends Paid")
            dividends_paid = abs(dividends_paid_raw) if dividends_paid_raw is not None else None
            buybacks_raw = sv("Repurchase Of Capital Stock", "Common Stock Repurchase")
            buybacks = abs(buybacks_raw) if buybacks_raw is not None else None
            r_and_d = sv("Research And Development", "Research Development")

            # Asset turnover = revenue / avg_total_assets (use single period as proxy)
            asset_turnover = None
            if revenue and total_assets and total_assets != 0:
                asset_turnover = revenue / total_assets

            records.append({
                "ticker": ticker,
                "period_end": period_end,
                "report_date": period_end,  # yfinance doesn't separate these cleanly
                "ni": ni,
                "revenue": revenue,
                "gross_profit": gross_profit,
                "operating_income": operating_income,
                "ebit": ebit,
                "ebitda": ebitda,
                "cfo": cfo,
                "capex": capex,
                "fcf": fcf,
                "total_assets": total_assets,
                "total_equity": total_equity,
                "total_debt": total_debt,
                "current_assets": current_assets,
                "current_liabilities": current_liabilities,
                "working_capital": working_capital,
                "retained_earnings": retained_earnings,
                "shares_outstanding": shares_outstanding,
                "dividends_paid": dividends_paid,
                "buybacks": buybacks,
                "r_and_d_expense": r_and_d,
                "asset_turnover": asset_turnover,
            })
        except Exception as e:
            log.warning(f"Failed to extract period {col} for {ticker}: {e}")

    return records


def update_fundamentals(tickers: list[str]):
    if not tickers:
        return

    conn = get_db()
    _create_table(conn)

    for ticker in tickers:
        try:
            tk = yf.Ticker(ticker)
            records = _extract_quarterly(tk, ticker)
            if not records:
                log.warning(f"No fundamental records for {ticker}")
                time.sleep(0.3)
                continue

            with conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO fundamentals
                    (ticker, period_end, report_date, ni, revenue, gross_profit,
                     operating_income, ebit, ebitda, cfo, capex, fcf,
                     total_assets, total_equity, total_debt, current_assets,
                     current_liabilities, working_capital, retained_earnings,
                     shares_outstanding, dividends_paid, buybacks, r_and_d_expense,
                     asset_turnover)
                    VALUES
                    (:ticker, :period_end, :report_date, :ni, :revenue, :gross_profit,
                     :operating_income, :ebit, :ebitda, :cfo, :capex, :fcf,
                     :total_assets, :total_equity, :total_debt, :current_assets,
                     :current_liabilities, :working_capital, :retained_earnings,
                     :shares_outstanding, :dividends_paid, :buybacks, :r_and_d_expense,
                     :asset_turnover)
                    """,
                    records,
                )
            log.info(f"Stored {len(records)} fundamental periods for {ticker}")
        except Exception as e:
            log.error(f"Failed fundamentals for {ticker}: {e}")

        time.sleep(0.3)

    conn.close()
    log.info("Fundamentals update complete")


def get_fundamentals(ticker: str, quarters: int = 8) -> pd.DataFrame:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM fundamentals
            WHERE ticker=?
            ORDER BY period_end DESC
            LIMIT ?
            """,
            (ticker, quarters),
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["period_end"] = pd.to_datetime(df["period_end"])
        return df.set_index("period_end").sort_index()
    except Exception as e:
        log.error(f"get_fundamentals failed for {ticker}: {e}")
        return pd.DataFrame()
    finally:
        conn.close()
