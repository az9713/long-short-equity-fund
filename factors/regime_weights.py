from utils import get_config, get_logger
from data.providers import get_vix

log = get_logger(__name__)

_LOW_VOL_WEIGHTS = {
    "momentum": 0.30,
    "value": 0.10,
    "quality": 0.20,
    "growth": 0.10,
    "revisions": 0.15,
    "short_interest": 0.05,
    "insider": 0.05,
    "institutional": 0.05,
}

_HIGH_VOL_WEIGHTS = {
    "momentum": 0.10,
    "value": 0.20,
    "quality": 0.28,
    "growth": 0.10,
    "revisions": 0.12,
    "short_interest": 0.05,
    "insider": 0.10,
    "institutional": 0.05,
}


def get_weights() -> dict:
    cfg = get_config()
    default_weights = cfg.get("scoring", {}).get("weights", {})

    if not cfg.get("scoring", {}).get("regime_conditional_weights", False):
        return default_weights

    try:
        vix = get_vix()
    except Exception as e:
        log.warning(f"VIX fetch failed for regime weights: {e}. Using default weights.")
        return default_weights

    if vix < 15:
        log.info(f"VIX={vix:.1f}: Low-vol regime weights active")
        return _LOW_VOL_WEIGHTS
    elif vix > 25:
        log.info(f"VIX={vix:.1f}: High-vol regime weights active")
        return _HIGH_VOL_WEIGHTS
    else:
        log.info(f"VIX={vix:.1f}: Normal regime, using config weights")
        return default_weights
