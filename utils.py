import os
import yaml
import sqlite3
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent

def get_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)

def get_db(name: str = "fund.db") -> sqlite3.Connection:
    db_path = ROOT / "data" / name
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(name)

def sec_headers() -> dict:
    email = os.getenv("SEC_USER_AGENT_EMAIL", "user@example.com")
    name = os.getenv("SEC_USER_AGENT_NAME", "LS_Equity_Research")
    return {"User-Agent": f"{name} {email}"}

def is_dev_mode() -> bool:
    return get_config().get("dev_mode", True)

def get_universe_tickers() -> list[str]:
    cfg = get_config()
    if cfg.get("dev_mode", True):
        return cfg.get("dev_tickers", [])
    return None  # signals full S&P 500 pull
