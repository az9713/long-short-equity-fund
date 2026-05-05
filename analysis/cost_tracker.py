from datetime import datetime
from utils import get_db, get_logger, get_config

log = get_logger(__name__)

# Reference prices for cost estimation (informational only — free tier is $0)
_INPUT_PRICE_PER_M = 0.075
_OUTPUT_PRICE_PER_M = 0.300

# In-process running totals (reset each Python process)
_run_input_tokens = 0
_run_output_tokens = 0


def _create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_cost_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            analyzer TEXT NOT NULL,
            ticker TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            model TEXT NOT NULL
        )
    """)
    conn.commit()


def log_call(analyzer: str, ticker: str, input_tokens: int, output_tokens: int):
    global _run_input_tokens, _run_output_tokens

    cfg = get_config()
    model = cfg.get("ai", {}).get("model", "unknown")

    _run_input_tokens += input_tokens
    _run_output_tokens += output_tokens

    conn = get_db()
    try:
        _create_table(conn)
        with conn:
            conn.execute(
                """
                INSERT INTO ai_cost_log
                (timestamp, analyzer, ticker, input_tokens, output_tokens, model)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (datetime.utcnow().isoformat(), analyzer, ticker,
                 input_tokens, output_tokens, model),
            )
    except Exception as e:
        log.error(f"Failed to log AI call: {e}")
    finally:
        conn.close()

    total = _run_input_tokens + _run_output_tokens
    est_cost = (_run_input_tokens / 1_000_000 * _INPUT_PRICE_PER_M +
                _run_output_tokens / 1_000_000 * _OUTPUT_PRICE_PER_M)
    print(f"  [tokens] {analyzer}/{ticker}  in={input_tokens:,}  out={output_tokens:,}  "
          f"run_total={total:,}  est_cost=${est_cost:.4f} (free tier)")

    check_ceiling()


def get_total_tokens() -> dict:
    return {
        "input": _run_input_tokens,
        "output": _run_output_tokens,
        "total": _run_input_tokens + _run_output_tokens,
    }


def check_ceiling():
    cfg = get_config()
    ceiling = cfg.get("ai", {}).get("cost_ceiling_usd", 25.0)
    est_cost = (_run_input_tokens / 1_000_000 * _INPUT_PRICE_PER_M +
                _run_output_tokens / 1_000_000 * _OUTPUT_PRICE_PER_M)
    if est_cost > ceiling:
        log.warning(
            f"Estimated cost ${est_cost:.4f} exceeds ceiling ${ceiling:.2f} "
            f"(free tier — no actual charge)"
        )
