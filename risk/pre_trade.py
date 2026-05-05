import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
from utils import ROOT, get_db, get_logger, get_config
from data.market_data import get_prices, get_adv
from data.earnings_calendar import days_to_earnings
from portfolio.state import get_positions
from portfolio.beta import get_beta, get_portfolio_beta

log = get_logger(__name__)
cfg = get_config()
risk_cfg = cfg.get("risk", {})
portfolio_cfg = cfg.get("portfolio", {})

HALT_LOCK_PATH = ROOT / "risk" / "halt.lock"


def _init_veto_log(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS veto_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            shares REAL,
            price REAL,
            reason TEXT
        )
    """)
    conn.commit()


def _log_veto(ticker: str, side: str, shares: float, price: float, reason: str):
    log.warning(f"VETO: {ticker} {side} - {reason}")
    try:
        conn = get_db()
        _init_veto_log(conn)
        with conn:
            conn.execute(
                """
                INSERT INTO veto_log (timestamp, ticker, side, shares, price, reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (datetime.utcnow().isoformat(), ticker, side, shares, price, reason),
            )
        conn.close()
    except Exception as e:
        log.error(f"Failed to write veto_log: {e}")


def _is_closing_trade(ticker: str, side: str, shares: float) -> bool:
    # A trade is a close if it reduces an existing opposite-side position to zero.
    # SELL closes LONG; COVER closes SHORT.
    # Side conventions from state: LONG positions are closed by SELL, SHORT by COVER.
    # We detect closing by checking if the incoming side is the opposite of what's in portfolio.
    try:
        positions = get_positions()
        if positions.empty:
            return False
        existing = positions[positions["ticker"] == ticker]
        if existing.empty:
            return False
        existing_side = existing.iloc[0]["side"]
        existing_shares = float(existing.iloc[0].get("shares", 0) or 0)
        # Closing: LONG position being sold (side=SHORT or explicit SELL) OR
        # SHORT position being covered. We treat SELL/COVER as side values too.
        if existing_side == "LONG" and side in ("SHORT", "SELL"):
            return True
        if existing_side == "SHORT" and side in ("LONG", "COVER"):
            return True
        # Also: if same side but shares >= existing shares → closing/reducing
        if existing_side == side and shares >= existing_shares * 0.95:
            return True
    except Exception as e:
        log.warning(f"_is_closing_trade check failed: {e}")
    return False


def _get_sector_exposure_after(
    ticker: str, side: str, trade_value: float, portfolio_value: float
) -> float:
    try:
        positions = get_positions()
        sector = "Unknown"
        if not positions.empty and ticker in positions["ticker"].values:
            row = positions[positions["ticker"] == ticker].iloc[0]
            sector = row.get("sector", "Unknown")
        if sector == "Unknown":
            # Try to get from yfinance
            try:
                import yfinance as yf
                info = yf.Ticker(ticker).info
                sector = info.get("sector", "Unknown")
            except Exception:
                pass

        if positions.empty or portfolio_value == 0:
            return abs(trade_value) / portfolio_value

        sector_net = 0.0
        for _, row in positions.iterrows():
            if row.get("sector", "") == sector:
                shares = float(row.get("shares", 0) or 0)
                price = float(row.get("current_price") or row.get("entry_price", 0) or 0)
                mv = shares * price / portfolio_value
                if row.get("side") == "LONG":
                    sector_net += mv
                else:
                    sector_net -= mv

        # Add the new trade
        if side == "LONG":
            sector_net += trade_value / portfolio_value
        else:
            sector_net -= trade_value / portfolio_value

        return abs(sector_net)
    except Exception as e:
        log.warning(f"Sector exposure check failed: {e}")
        return 0.0


def _get_gross_after(
    ticker: str, side: str, trade_value: float, portfolio_value: float
) -> float:
    try:
        positions = get_positions()
        if positions.empty:
            return abs(trade_value) / portfolio_value

        gross = 0.0
        for _, row in positions.iterrows():
            shares = float(row.get("shares", 0) or 0)
            price = float(row.get("current_price") or row.get("entry_price", 0) or 0)
            gross += abs(shares * price) / portfolio_value

        gross += abs(trade_value) / portfolio_value
        return gross
    except Exception as e:
        log.warning(f"Gross exposure check failed: {e}")
        return 0.0


def _get_net_beta_after(
    ticker: str, side: str, trade_value: float, portfolio_value: float
) -> float:
    try:
        positions = get_positions()
        longs = []
        shorts = []
        weights = {}

        if not positions.empty:
            for _, row in positions.iterrows():
                t = row["ticker"]
                t_side = row.get("side", "")
                shares = float(row.get("shares", 0) or 0)
                price = float(row.get("current_price") or row.get("entry_price", 0) or 0)
                w = shares * price / portfolio_value
                if t_side == "LONG":
                    longs.append(t)
                    weights[t] = w
                elif t_side == "SHORT":
                    shorts.append(t)
                    weights[t] = -w

        # Add new ticker
        new_weight = trade_value / portfolio_value
        if side == "LONG":
            longs.append(ticker)
            weights[ticker] = new_weight
        else:
            shorts.append(ticker)
            weights[ticker] = -new_weight

        result = get_portfolio_beta(longs, shorts, {t: abs(w) for t, w in weights.items()})
        return abs(result.get("net_beta", 0.0))
    except Exception as e:
        log.warning(f"Beta check failed: {e}")
        return 0.0


def _get_max_correlation(
    ticker: str, portfolio_value: float
) -> tuple[float, str]:
    # Compute 60-day Pearson correlation of ticker with each existing position
    try:
        positions = get_positions()
        if positions.empty:
            return 0.0, ""

        px_new = get_prices(ticker, days=70)
        if px_new.empty or "close" not in px_new.columns:
            return 0.0, ""
        ret_new = px_new["close"].pct_change().dropna()

        max_corr = 0.0
        max_ticker = ""
        for _, row in positions.iterrows():
            t = row["ticker"]
            if t == ticker:
                continue
            px_t = get_prices(t, days=70)
            if px_t.empty or "close" not in px_t.columns:
                continue
            ret_t = px_t["close"].pct_change().dropna()

            # Align
            combined = pd.concat([ret_new, ret_t], axis=1, join="inner").dropna()
            combined.columns = ["new", "existing"]
            if len(combined) < 20:
                continue
            combined = combined.tail(60)
            corr = float(combined["new"].corr(combined["existing"]))
            if abs(corr) > abs(max_corr):
                max_corr = corr
                max_ticker = t

        return max_corr, max_ticker
    except Exception as e:
        log.warning(f"Correlation check failed: {e}")
        return 0.0, ""


def pre_trade_veto(
    ticker: str,
    side: str,
    shares: float,
    price: float,
    portfolio_value: float,
) -> tuple[bool, str]:

    trade_value = abs(shares * price)

    # Check 1: Halt lock — always checked, even for closes
    if HALT_LOCK_PATH.exists():
        reason = "System halted"
        _log_veto(ticker, side, shares, price, reason)
        return False, reason

    # Detect closing trade — if closing, skip checks 2–8
    closing = _is_closing_trade(ticker, side, shares)
    if closing:
        return True, "closing trade approved"

    # Check 2: Earnings blackout
    try:
        dte = days_to_earnings(ticker)
        if dte is not None and dte <= 5:
            reason = f"Earnings in {dte} days (blackout)"
            _log_veto(ticker, side, shares, price, reason)
            return False, reason
    except Exception as e:
        log.warning(f"Earnings check failed for {ticker}: {e}")

    # Check 3: Liquidity — trade value > 5% of ADV
    try:
        adv = get_adv(ticker)
        if adv > 0 and trade_value > 0.05 * adv:
            reason = f"Trade ${trade_value:,.0f} exceeds 5% ADV ${adv * 0.05:,.0f}"
            _log_veto(ticker, side, shares, price, reason)
            return False, reason
    except Exception as e:
        log.warning(f"Liquidity check failed for {ticker}: {e}")

    # Check 4: Position size
    max_pos_pct = portfolio_cfg.get("max_position_pct", 0.05)
    if portfolio_value > 0 and (trade_value / portfolio_value) > 1.5 * max_pos_pct:
        reason = f"Oversized: {trade_value / portfolio_value:.1%} > 1.5x max {max_pos_pct:.1%}"
        _log_veto(ticker, side, shares, price, reason)
        return False, reason

    # Check 5: Sector exposure
    max_sector_pct = portfolio_cfg.get("max_sector_pct", 0.25)
    sector_exp = _get_sector_exposure_after(ticker, side, trade_value, portfolio_value)
    if sector_exp > max_sector_pct:
        reason = f"Sector limit: would reach {sector_exp:.1%} (limit {max_sector_pct:.1%})"
        _log_veto(ticker, side, shares, price, reason)
        return False, reason

    # Check 6: Gross exposure
    gross_limit = portfolio_cfg.get("gross_limit", 1.65)
    gross_after = _get_gross_after(ticker, side, trade_value, portfolio_value)
    if gross_after > gross_limit:
        reason = f"Gross limit: would reach {gross_after:.2f}x (limit {gross_limit:.2f}x)"
        _log_veto(ticker, side, shares, price, reason)
        return False, reason

    # Check 7: Net beta
    max_beta = portfolio_cfg.get("max_beta", 0.15)
    beta_after = _get_net_beta_after(ticker, side, trade_value, portfolio_value)
    if beta_after > max_beta * 1.5:
        reason = f"Beta limit: net beta {beta_after:.3f} > {max_beta * 1.5:.3f}"
        _log_veto(ticker, side, shares, price, reason)
        return False, reason

    # Check 8: Correlation veto
    corr_veto = risk_cfg.get("correlation_veto", 0.80)
    try:
        max_corr, corr_ticker = _get_max_correlation(ticker, portfolio_value)
        if max_corr > corr_veto:
            reason = f"High correlation ({max_corr:.3f}) with {corr_ticker}"
            _log_veto(ticker, side, shares, price, reason)
            return False, reason
    except Exception as e:
        log.warning(f"Correlation veto check failed for {ticker}: {e}")

    return True, "approved"
