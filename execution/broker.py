import os
import time
from utils import get_config, get_logger
from portfolio.state import _init_tables as _init_state_tables, get_positions, log_trade

log = get_logger(__name__)
cfg = get_config().get("execution", {})

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    _ALPACA_AVAILABLE = True
except ImportError:
    log.warning("alpaca-py not installed — broker running in SIMULATED mode")
    TradingClient = None
    _ALPACA_AVAILABLE = False

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    _TENACITY_AVAILABLE = True
except ImportError:
    log.warning("tenacity not installed — no retry logic on connection errors")
    _TENACITY_AVAILABLE = False


def _is_live_mode() -> bool:
    mode = cfg.get("mode", "paper")
    confirmed = os.getenv("ALPACA_LIVE_CONFIRMED", "")
    return mode == "live" and confirmed == "YES_I_UNDERSTAND_THE_RISKS"


def _make_client() -> "TradingClient | None":
    if not _ALPACA_AVAILABLE:
        return None

    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")

    if not api_key or not secret_key:
        log.warning("ALPACA_API_KEY / ALPACA_SECRET_KEY not set — broker in SIMULATED mode")
        return None

    paper = not _is_live_mode()

    if paper:
        log.info("PAPER TRADING MODE")
    else:
        log.warning("LIVE TRADING MODE - REAL MONEY")

    def _create():
        return TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)

    if _TENACITY_AVAILABLE:
        from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=8),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )
        def _create_with_retry():
            return _create()

        try:
            return _create_with_retry()
        except Exception as e:
            log.error(f"Alpaca client init failed after retries: {e}")
            return None
    else:
        # Manual exponential backoff without tenacity
        for attempt in range(3):
            try:
                return _create()
            except Exception as e:
                wait = 2 ** attempt
                log.warning(f"Alpaca connect attempt {attempt+1}/3 failed: {e}. Waiting {wait}s")
                if attempt < 2:
                    time.sleep(wait)
        log.error("Alpaca client init failed after 3 attempts")
        return None


class BrokerClient:
    def __init__(self):
        self.client = _make_client()
        self._synced = False

    def get_client(self) -> "TradingClient | None":
        return self.client

    def get_account(self) -> dict:
        if self.client is None:
            return {"cash": 0.0, "portfolio_value": 100_000.0, "buying_power": 100_000.0}
        try:
            acct = self.client.get_account()
            return {
                "cash": float(acct.cash),
                "portfolio_value": float(acct.portfolio_value),
                "buying_power": float(acct.buying_power),
            }
        except Exception as e:
            log.error(f"get_account failed: {e}")
            return {"cash": 0.0, "portfolio_value": 100_000.0, "buying_power": 100_000.0}

    def get_alpaca_positions(self) -> list[dict]:
        if self.client is None:
            return []
        try:
            positions = self.client.get_all_positions()
            result = []
            for p in positions:
                side = "LONG" if float(p.qty) > 0 else "SHORT"
                result.append({
                    "ticker": p.symbol,
                    "side": side,
                    "shares": abs(float(p.qty)),
                    "current_price": float(p.current_price) if p.current_price else 0.0,
                    "avg_entry_price": float(p.avg_entry_price) if p.avg_entry_price else 0.0,
                    "unrealized_pnl": float(p.unrealized_pl) if p.unrealized_pl else 0.0,
                })
            return result
        except Exception as e:
            log.error(f"get_alpaca_positions failed: {e}")
            return []

    def sync_with_alpaca(self):
        # Reconcile our SQLite state with what Alpaca actually holds.
        # We trust Alpaca as the source of truth for fill prices/quantities.
        if self.client is None:
            log.info("sync_with_alpaca skipped — no broker connection")
            return

        alpaca_positions = self.get_alpaca_positions()
        if not alpaca_positions:
            log.info("No open positions in Alpaca to sync")
            return

        local_positions = get_positions()
        local_tickers = set(local_positions["ticker"].tolist()) if not local_positions.empty else set()
        alpaca_tickers = {p["ticker"] for p in alpaca_positions}

        # Log positions in Alpaca but not in our SQLite
        missing_locally = alpaca_tickers - local_tickers
        for ticker in missing_locally:
            pos = next(p for p in alpaca_positions if p["ticker"] == ticker)
            log.warning(
                f"SYNC: {ticker} held in Alpaca ({pos['side']} {pos['shares']:.2f} @ "
                f"{pos['avg_entry_price']:.2f}) but not in local state — adding"
            )
            log_trade(
                ticker=ticker,
                side=pos["side"],
                shares=pos["shares"],
                price=pos["avg_entry_price"],
                action="BUY" if pos["side"] == "LONG" else "SHORT",
                reason="sync_with_alpaca",
            )

        # Log positions in our SQLite but not in Alpaca (may have been closed externally)
        extra_locally = local_tickers - alpaca_tickers
        for ticker in extra_locally:
            log.warning(
                f"SYNC: {ticker} in local state but NOT in Alpaca — removing stale position"
            )
            local_row = local_positions[local_positions["ticker"] == ticker].iloc[0]
            close_action = "SELL" if local_row["side"] == "LONG" else "COVER"
            log_trade(
                ticker=ticker,
                side=local_row["side"],
                shares=float(local_row.get("shares", 0) or 0),
                price=float(local_row.get("current_price") or local_row.get("entry_price", 0) or 0),
                action=close_action,
                reason="sync_with_alpaca — removed stale",
            )

        log.info(f"sync_with_alpaca complete: {len(alpaca_positions)} Alpaca positions reconciled")
        self._synced = True


# Module-level singleton — imported by executor, order_manager
broker = BrokerClient()
