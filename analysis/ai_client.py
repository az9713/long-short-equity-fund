import os
import re
import json
import time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from utils import get_logger, get_config

log = get_logger(__name__)

# Module-level rate-limiter: 13 calls/min stays within 15 RPM limit
_last_call_ts: float = 0.0
_MIN_CALL_INTERVAL = 4.5  # seconds


def _should_retry(exc) -> bool:
    """Retry on rate-limit or server errors; not on auth errors."""
    try:
        from openai import RateLimitError, APIStatusError, APIConnectionError, AuthenticationError
        if isinstance(exc, AuthenticationError):
            return False
        if isinstance(exc, (RateLimitError, APIConnectionError)):
            return True
        if isinstance(exc, APIStatusError) and exc.status_code >= 500:
            return True
    except ImportError:
        pass
    return False


def _get_client():
    """Build OpenAI-SDK client pointing to OpenRouter."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        cfg = get_config()
        base_url = cfg.get("ai", {}).get("base_url", "https://openrouter.ai/api/v1")
        return OpenAI(base_url=base_url, api_key=api_key)
    except Exception as e:
        log.error(f"Failed to build OpenAI client: {e}")
        return None


def extract_json(text: str) -> dict | None:
    if not text:
        return None

    # 1. Try raw text as JSON
    try:
        return json.loads(text.strip())
    except Exception:
        pass

    # 2. Look for ```json ... ``` fence
    fence = re.search(r"```json\s*([\s\S]+?)\s*```", text, re.IGNORECASE)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass

    # 3. Extract first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass

    log.warning("extract_json: all strategies failed")
    return None


def estimate_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3)


@retry(
    retry=retry_if_exception(_should_retry),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
)
def _call_with_retry(client, model: str, system: str, user: str, temperature: float) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    return response.choices[0].message.content


def call_llm(
    system: str,
    user: str,
    temperature: float = 0.1,
    analyzer: str = "unknown",
    ticker: str = "unknown",
) -> str | None:
    global _last_call_ts

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        log.error("OPENROUTER_API_KEY not set — skipping LLM call")
        return None

    client = _get_client()
    if client is None:
        return None

    cfg = get_config()
    model = cfg.get("ai", {}).get("model", "google/gemini-2.0-flash-exp:free")

    # Rate limiting: enforce minimum interval between actual API calls
    now = time.time()
    wait = _MIN_CALL_INTERVAL - (now - _last_call_ts)
    if wait > 0:
        time.sleep(wait)

    try:
        text = _call_with_retry(client, model, system, user, temperature)
        _last_call_ts = time.time()

        input_tokens = estimate_tokens(system + user)
        output_tokens = estimate_tokens(text) if text else 0

        # Import here to avoid circular import at module load time
        from analysis.cost_tracker import log_call
        log_call(analyzer, ticker, input_tokens, output_tokens)

        return text
    except Exception as e:
        log.error(f"LLM call failed: {e}")
        _last_call_ts = time.time()
        return None
