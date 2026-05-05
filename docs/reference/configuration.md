# Configuration reference

Every field in `config.yaml`. Defaults are shown as they appear in the shipped `config.yaml`.

The file is read on every invocation by `utils.get_config()`. Edits take effect on the next process start — no service to restart.

## fund

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `"Meridian Capital Partners"` | Public-facing fund name. Used in dashboard branding and weekly letter header. |
| `analyst` | string | `"JARVIS"` | The AI analyst persona name. Used in analyzer prompts and dashboard. |

## universe

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `benchmark_tickers` | list[string] | `["SPY", "QQQ", "IWM", "DIA", "XLK", ..., "XLU"]` | Benchmarks fetched alongside the main universe. Used by Layer 4 (beta) and Layer 7 (relative performance). |

## dev_mode + dev_tickers

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `dev_mode` | bool | `true` | When true, every layer operates on `dev_tickers` instead of the full S&P 500. |
| `dev_tickers` | list[string] | `["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "JPM", "JNJ", "UNH", "XOM", "V"]` | Ten large-cap tickers used in dev mode. Edit to test other names. |

## portfolio

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `num_longs` | int | 20 | Target number of long positions (conviction optimizer; MVO uses as a soft target) |
| `num_shorts` | int | 20 | Target number of short positions |
| `max_position_pct` | float | 0.05 | Hard cap on absolute weight per position |
| `max_sector_pct` | float | 0.25 | Hard cap on net sector exposure |
| `gross_limit` | float | 1.50 | Max sum of absolute weights (e.g., 0.75 long + 0.75 short = 1.50) |
| `net_limit` | float | 0.10 | Max signed sum of weights (long − short) |
| `max_beta` | float | 0.15 | Max absolute net portfolio beta |
| `turnover_budget` | float | 0.30 | Soft monthly turnover target. Exceeding logs a warning; does not block. |
| `mvo_risk_aversion` | float | 1.0 | λ in `−w·μ + λ·w·Σ·w`. Higher = more conservative. |
| `optimize_method` | enum | `"mvo"` | Which optimizer to use. Values: `mvo` or `conviction`. |

## risk

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `daily_loss_limit` | float | 0.015 | DAILY_WARN threshold (−1.5%). Reduce sizes; no halt. |
| `daily_halt_limit` | float | 0.025 | DAILY_KILL threshold (−2.5%). Writes halt lock. |
| `weekly_loss_limit` | float | 0.04 | WEEKLY_WARN threshold (−4%). Reduce sizes. |
| `drawdown_limit` | float | 0.08 | DRAWDOWN_KILL threshold from peak (−8%). Writes halt lock. |
| `single_position_nav_limit` | float | 0.03 | Single position alert threshold (3% of NAV). Trim alert; no halt. |
| `correlation_alert` | float | 0.60 | Avg book correlation that triggers a warning. |
| `correlation_veto` | float | 0.80 | Pairwise correlation that vetoes a new trade. |
| `factor_zscore_alert` | float | 1.5 | Z-score on factor exposure that fires a factor alert. |
| `vix_reduce_threshold` | float | 25 | VIX level that triggers `REDUCE` action (halve new sizes). |
| `vix_halt_threshold` | float | 33 | VIX level that triggers `HALT` action (write halt lock). |

## scoring

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `regime_conditional_weights` | bool | `false` | If true, factor weights vary by VIX regime (see `factors/regime_weights.py`). |
| `weights.momentum` | float | 0.20 | Composite weight for momentum factor |
| `weights.quality` | float | 0.20 | Composite weight for quality factor |
| `weights.value` | float | 0.15 | Composite weight for value factor |
| `weights.revisions` | float | 0.15 | Composite weight for revisions factor |
| `weights.insider` | float | 0.10 | Composite weight for insider factor |
| `weights.growth` | float | 0.10 | Composite weight for growth factor |
| `weights.short_interest` | float | 0.05 | Composite weight for short-interest factor |
| `weights.institutional` | float | 0.05 | Composite weight for institutional factor |

> **Tip:** Weights do not need to sum to 1.0; the composite is computed as a literal weighted sum. To remove a factor, set its weight to 0.

## ai

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | `"openrouter"` | Informational. Only `model` and `base_url` matter at runtime. |
| `model` | string | `"google/gemini-2.0-flash-exp:free"` | OpenRouter model slug. Free tier with 15 RPM. |
| `base_url` | string | `"https://openrouter.ai/api/v1"` | API base URL. Set to `https://api.anthropic.com/v1` or `https://api.openai.com/v1` for direct provider access. |
| `cost_ceiling_usd` | float | 25.0 | Soft cost ceiling. Cost tracker logs warning when exceeded; does not abort. |
| `cache_ttl_days` | int | 30 | TTL for `analysis_cache` rows. After expiry, analyzer is re-called. |

## execution

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | enum | `"paper"` | `paper` or `live`. Live also requires `ALPACA_LIVE_CONFIRMED=YES_I_UNDERSTAND_THE_RISKS` env var. |
| `alpaca_base_url` | string | `"https://paper-api.alpaca.markets"` | Alpaca API base. The `alpaca-py` SDK uses the `paper` flag, so this is informational. |
| `slippage_spread_bps` | int | 5 | Estimated half-spread in bps. Used by transaction-cost model and pre-trade estimate. |
| `market_impact_coeff` | float | 0.10 | Coefficient in `impact_bps = coeff × sqrt(trade_usd / adv_usd) × 10_000`. |
| `max_order_pct_adv` | float | 0.02 | Loose execution check (2% ADV). Pre-trade veto uses a stricter 5%. |
| `time_in_force` | enum | `"gtc"` | Alpaca order TIF. `gtc`, `day`, `ioc`, `fok`. |

## reporting

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `weekly_commentary_day` | string | `"friday"` | Day of the week the weekly letter is auto-generated (lowercase day name). |
| `short_term_tax_rate` | float | 0.37 | Federal short-term cap-gains rate. Used in tear-sheet net-of-tax projection. |
| `long_term_tax_rate` | float | 0.20 | Federal long-term cap-gains rate. |

## dashboard

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `port` | int | 8502 | Streamlit port. `run_dashboard.py` passes this as `--server.port`. |

## See also

- [Environment variables](env-vars.md) — `.env` keys (separate from this file)
- [CLI](cli.md) — flags that can override certain config values per-run
