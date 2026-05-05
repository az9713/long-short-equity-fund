import math
import time
from datetime import datetime
from utils import get_db, get_logger, get_config
from data.market_data import get_prices, get_adv
from risk.pre_trade import pre_trade_veto
from execution.short_check import is_shortable
from execution.broker import broker
from portfolio.state import log_trade

log = get_logger(__name__)
cfg = get_config().get("execution", {})

# Internal side  →  Alpaca OrderSide mapping:
#   BUY   → OrderSide.BUY   (open long)
#   SELL  → OrderSide.SELL  (close long)
#   SHORT → OrderSide.SELL  (open short — Alpaca uses SELL for both close-long and open-short)
#   COVER → OrderSide.BUY   (close short)

MAX_ORDER_PCT_ADV = cfg.get("max_order_pct_adv", 0.02)
POLL_INTERVAL_S = 5
MAX_POLLS = 24       # 24 × 5s = 120s timeout
MAX_RETRIES = 3


def _init_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            ticker TEXT,
            side TEXT,
            shares REAL,
            limit_price REAL,
            fill_price REAL,
            slippage_bps REAL,
            status TEXT,
            portfolio_value_at_trade REAL
        )
    """)
    conn.commit()


def _log_order(
    ticker: str,
    side: str,
    shares: float,
    limit_price: float,
    fill_price: float | None,
    slippage_bps: float | None,
    status: str,
    portfolio_value: float,
) -> int:
    conn = get_db()
    try:
        _init_tables(conn)
        now = datetime.utcnow().isoformat()
        with conn:
            cur = conn.execute(
                """
                INSERT INTO order_log
                (timestamp, ticker, side, shares, limit_price, fill_price, slippage_bps, status, portfolio_value_at_trade)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (now, ticker, side, shares, limit_price, fill_price, slippage_bps, status, portfolio_value),
            )
        return cur.lastrowid
    except Exception as e:
        log.error(f"_log_order failed: {e}")
        return -1
    finally:
        conn.close()


def _update_order_status(order_id: int, fill_price: float, slippage_bps: float, status: str):
    conn = get_db()
    try:
        with conn:
            conn.execute(
                "UPDATE order_log SET fill_price=?, slippage_bps=?, status=? WHERE id=?",
                (fill_price, slippage_bps, status, order_id),
            )
    except Exception as e:
        log.error(f"_update_order_status failed for order {order_id}: {e}")
    finally:
        conn.close()


def _get_signal_price(ticker: str, signal_price: float | None) -> float:
    if signal_price is not None:
        return signal_price
    try:
        px = get_prices(ticker, 2)
        if not px.empty:
            return float(px.iloc[-1]["adj_close"])
    except Exception as e:
        log.warning(f"Could not fetch signal price for {ticker}: {e}")
    return 0.0


def _calc_limit_price(close: float, side: str) -> float:
    if side in ("BUY", "COVER"):
        return round(close * 1.001, 2)
    return round(close * 0.999, 2)


def _alpaca_side(side: str):
    try:
        from alpaca.trading.enums import OrderSide
        if side in ("BUY", "COVER"):
            return OrderSide.BUY
        return OrderSide.SELL  # SELL and SHORT both map to OrderSide.SELL
    except ImportError:
        return None


def _place_alpaca_order(ticker: str, side: str, shares: float, limit_price: float) -> str | None:
    # Returns alpaca order id string, or None if simulated/failed
    client = broker.get_client()
    if client is None:
        log.info(f"SIMULATED order: {side} {shares:.2f} {ticker} @ {limit_price:.2f}")
        return None

    try:
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderType

        alpaca_side = _alpaca_side(side)
        tif_str = cfg.get("time_in_force", "gtc").upper()
        tif = TimeInForce.GTC if tif_str == "GTC" else TimeInForce.DAY

        req = LimitOrderRequest(
            symbol=ticker,
            qty=shares,
            side=alpaca_side,
            type=OrderType.LIMIT,
            limit_price=limit_price,
            time_in_force=tif,
        )
        order = client.submit_order(req)
        alpaca_id = str(order.id)
        log.info(f"Order submitted: {alpaca_id} {side} {shares:.2f} {ticker} @ {limit_price:.2f}")
        # Register in open_orders so --status can surface live Alpaca orders
        try:
            from execution.order_manager import _register_open_order
            _register_open_order(alpaca_id, ticker, side, shares, limit_price)
        except Exception as _e:
            log.warning(f"Could not register open order {alpaca_id}: {_e}")
        return alpaca_id
    except Exception as e:
        log.error(f"submit_order failed for {ticker}: {e}")
        return None


def _poll_for_fill(alpaca_order_id: str, max_polls: int = MAX_POLLS) -> tuple[str, float]:
    # Returns (status, fill_price). status: 'FILLED' | 'CANCELLED' | 'PENDING' | 'SIMULATED'
    if alpaca_order_id is None:
        # Simulated — treat as instant fill at limit price
        return "SIMULATED", 0.0

    client = broker.get_client()
    if client is None:
        return "SIMULATED", 0.0

    for _ in range(max_polls):
        try:
            order = client.get_order_by_id(alpaca_order_id)
            status = str(order.status).lower()
            if status == "filled":
                fill_price = float(order.filled_avg_price) if order.filled_avg_price else 0.0
                return "FILLED", fill_price
            if status in ("cancelled", "canceled", "expired"):
                return "CANCELLED", 0.0
        except Exception as e:
            log.warning(f"Poll error for order {alpaca_order_id}: {e}")
        time.sleep(POLL_INTERVAL_S)

    return "PENDING", 0.0


def _cancel_alpaca_order(alpaca_order_id: str):
    if alpaca_order_id is None:
        return
    client = broker.get_client()
    if client is None:
        return
    try:
        client.cancel_order_by_id(alpaca_order_id)
        log.info(f"Cancelled order: {alpaca_order_id}")
    except Exception as e:
        log.warning(f"Cancel failed for {alpaca_order_id}: {e}")


def _execute_single_chunk(
    ticker: str,
    side: str,
    shares: float,
    close: float,
    signal_price: float,
    portfolio_value: float,
) -> dict | None:
    # Single chunk attempt with retry loop
    for attempt in range(1, MAX_RETRIES + 1):
        limit_price = _calc_limit_price(close, side)

        order_id = _log_order(
            ticker, side, shares, limit_price,
            fill_price=None, slippage_bps=None,
            status="PENDING", portfolio_value=portfolio_value,
        )

        alpaca_oid = _place_alpaca_order(ticker, side, shares, limit_price)
        status, fill_price = _poll_for_fill(alpaca_oid)

        if status == "SIMULATED":
            # Paper sim: treat limit_price as fill
            fill_price = limit_price
            slippage_bps = 0.0
            if signal_price and signal_price != 0:
                raw = (fill_price - signal_price) / signal_price * 10_000
                if side in ("SELL", "SHORT", "COVER"):
                    raw = -raw
                slippage_bps = round(raw, 2)
            _update_order_status(order_id, fill_price, slippage_bps, "SIMULATED")
            log.info(
                f"SIMULATED fill: {side} {shares:.2f} {ticker} limit={limit_price:.2f} "
                f"slippage={slippage_bps:.1f}bps"
            )
            return {
                "order_id": order_id,
                "ticker": ticker,
                "side": side,
                "shares": shares,
                "limit_price": limit_price,
                "fill_price": fill_price,
                "slippage_bps": slippage_bps,
                "status": "SIMULATED",
            }

        if status == "FILLED":
            slippage_bps = 0.0
            if signal_price and signal_price != 0:
                raw = (fill_price - signal_price) / signal_price * 10_000
                if side in ("SELL", "SHORT", "COVER"):
                    raw = -raw
                slippage_bps = round(raw, 2)
            _update_order_status(order_id, fill_price, slippage_bps, "FILLED")
            log.info(
                f"FILLED: {side} {shares:.2f} {ticker} @ {fill_price:.2f} "
                f"slippage={slippage_bps:.1f}bps"
            )
            return {
                "order_id": order_id,
                "ticker": ticker,
                "side": side,
                "shares": shares,
                "limit_price": limit_price,
                "fill_price": fill_price,
                "slippage_bps": slippage_bps,
                "status": "FILLED",
            }

        # Timed out or cancelled — cancel and retry with fresh price
        _cancel_alpaca_order(alpaca_oid)
        _update_order_status(order_id, 0.0, None, "CANCELLED")
        log.warning(f"Order timeout/cancel {ticker} attempt {attempt}/{MAX_RETRIES}")

        # Refresh close price for next retry
        try:
            px = get_prices(ticker, 2)
            if not px.empty:
                close = float(px.iloc[-1]["close"])
        except Exception:
            pass

    log.error(f"All {MAX_RETRIES} attempts failed for {ticker} {side}")
    return None


def execute_trade(
    ticker: str,
    side: str,
    shares: float,
    portfolio_value: float,
    signal_price: float = None,
) -> dict | None:

    # Step 1: pre-trade risk veto
    try:
        px = get_prices(ticker, 2)
        if px.empty:
            log.warning(f"No price data for {ticker} — cannot execute")
            return None
        close = float(px.iloc[-1]["close"])
    except Exception as e:
        log.error(f"Price fetch failed for {ticker}: {e}")
        return None

    approved, reason = pre_trade_veto(ticker, side, shares, close, portfolio_value)
    if not approved:
        log.warning(f"Pre-trade veto: {ticker} {side} — {reason}")
        return None

    # Step 2: short availability check
    if side == "SHORT":
        if not is_shortable(ticker):
            return None

    # Step 3: resolve signal price
    sig_price = _get_signal_price(ticker, signal_price)

    # Step 4: chunking — split if order exceeds max_order_pct_adv of ADV
    try:
        adv_usd = get_adv(ticker)
        max_chunk_usd = MAX_ORDER_PCT_ADV * adv_usd
        chunk_shares = math.floor(max_chunk_usd / close) if close > 0 and adv_usd > 0 else 0
    except Exception as e:
        log.warning(f"ADV fetch failed for {ticker}: {e}")
        adv_usd = 0.0
        chunk_shares = 0

    trade_usd = shares * close
    needs_chunking = adv_usd > 0 and trade_usd > MAX_ORDER_PCT_ADV * adv_usd and chunk_shares > 0

    if needs_chunking:
        n_chunks = math.ceil(shares / chunk_shares)
        log.info(f"Chunking {ticker}: {shares:.2f} shares into {n_chunks} chunks of ~{chunk_shares:.0f}")
        last_result = None
        remaining = shares
        for i in range(n_chunks):
            this_chunk = min(chunk_shares, remaining)
            if this_chunk <= 0:
                break
            result = _execute_single_chunk(ticker, side, this_chunk, close, sig_price, portfolio_value)
            if result is None:
                log.error(f"Chunk {i+1}/{n_chunks} failed for {ticker} — aborting remaining chunks")
                break
            last_result = result
            remaining -= this_chunk
            if remaining > 0:
                time.sleep(1)

        if last_result is None:
            return None

        # Log the aggregate trade to portfolio state
        if last_result["status"] in ("FILLED", "SIMULATED"):
            # BUY → LONG, SHORT → SHORT, SELL closes a LONG, COVER closes a SHORT
            port_side = "LONG" if side in ("BUY", "SELL") else "SHORT"
            log_trade(
                ticker=ticker,
                side=port_side,
                shares=shares - remaining,
                price=last_result["fill_price"],
                action=side,
                reason="chunked execution",
            )
        return last_result

    # Single-chunk execution
    result = _execute_single_chunk(ticker, side, shares, close, sig_price, portfolio_value)

    if result and result["status"] in ("FILLED", "SIMULATED"):
        # Map execution side to portfolio state action/side
        action = side  # BUY | SELL | SHORT | COVER
        port_side = "LONG" if side in ("BUY", "SELL") else "SHORT"
        log_trade(
            ticker=ticker,
            side=port_side,
            shares=shares,
            price=result["fill_price"],
            action=action,
            reason="executed",
        )

    return result
