# System design

The technical architecture of JARVIS, for developers who will work on (not just use) the system.

## High-level architecture

```
                    ┌────────────────────────────────────────────────┐
                    │                External services               │
                    │ yfinance · SEC EDGAR · FINRA · Alpaca · Open-  │
                    │ Router LLMs · FRED · FMP (optional)            │
                    └───────────────────┬────────────────────────────┘
                                        │
   ┌─ Layer 1 ────────────┐      ┌─ Layer 6 ─────────┐
   │ data/                │      │ execution/        │
   │  universe, prices,   │      │  broker (Alpaca), │
   │  fundamentals, SEC,  │      │  order_manager,   │
   │  insiders, 13F,      │      │  slippage         │
   │  estimates, calendar │      └────┬──────┬───────┘
   └────────┬─────────────┘           │      │
            │ writes                  │      │ writes
            ▼                         │      ▼
                                      │  ┌──────────────────┐
   ┌─ Layer 2 ─────────────┐          │  │ open_orders,     │
   │ factors/              │          │  │ order_log,       │
   │  momentum, value,     │          │  │ portfolio_*      │
   │  quality, growth,     │          │  └──────────────────┘
   │  revisions, short,    │          │
   │  insider, institutional          │
   └────────┬─────────────┘           │
            │ writes CSV              │
            ▼                         │
   ┌─ Layer 3 ─────────────┐          │
   │ analysis/             │          │
   │  filing_analyzer,     │          │
   │  earnings_analyzer,   │          │ ┌────────────────────────┐
   │  risk_analyzer,       │          │ │  fund.db (SQLite)       │
   │  insider_analyzer,    │ reads    │ │   single source of truth│
   │  sector_analysis,     │◀─────────┴─│   for ALL state         │
   │  combined_score       │ writes     │                         │
   └────────┬─────────────┘            │                         │
            │ writes report MD         │                         │
            ▼                          │                         │
   ┌─ Layer 4 ─────────────┐           │                         │
   │ portfolio/            │           │                         │
   │  optimizer (mvo +     │ writes    │                         │
   │  conviction),         │──────────▶│ position_approvals      │
   │  rebalance, beta,     │           │                         │
   │  factor_exposure      │           │                         │
   └────────┬─────────────┘            │                         │
            │ writes PENDING            │                         │
            ▼                          │                         │
   ┌─ Layer 5 ─────────────┐           │                         │
   │ risk/                 │           │                         │
   │  pre_trade,           │ reads     │                         │
   │  circuit_breakers,    │◀──────────│                         │
   │  factor_risk_model,   │ writes    │                         │
   │  correlation_monitor, │──────────▶│ veto_log, halt.lock     │
   │  tail_risk,           │           │                         │
   │  stress_test          │           │                         │
   └────────┬─────────────┘            └─────────┬───────────────┘
            │ approves/vetoes                    │
            ▼                                    │
        (back to Layer 6 above)                  │
                                                 │
   ┌─ Layer 7 ─────────────────────────────┐    │
   │ reporting/                            │ reads
   │  pnl_attribution, win_loss,           │◀───┘
   │  sector_performance, turnover,        │
   │  tear_sheet, commentary               │
   │ dashboard/                            │
   │  app.py (Streamlit, 7 tabs)           │
   └───────────────────────────────────────┘
```

## Component breakdown

| Layer | Package | Lines (approx) | Responsibility |
|-------|---------|----------------|----------------|
| 1 | `data/` | ~1,800 | External data ingestion |
| 2 | `factors/` | ~2,500 | Quantitative scoring |
| 3 | `analysis/` | ~1,500 | LLM-driven qualitative analysis |
| 4 | `portfolio/` | ~1,800 | Optimization and trade list generation |
| 5 | `risk/` | ~1,400 | Veto, breakers, factor risk model, stress |
| 6 | `execution/` | ~900 | Alpaca order submission and reconciliation |
| 7 | `reporting/` + `dashboard/` | ~1,500 | Attribution, tear sheet, weekly letter, UI |
| Util | `utils.py` | ~50 | Shared config / db / logger |
| Backtest | `run_backtest.py` | ~730 | Walk-forward simulation |

Total: ~12,000 lines of Python across 67 source files.

## Data flows

### Nightly cycle

```
run_data.py        (Layer 1) → fund.db: prices, fundamentals, filings, ...
run_scoring.py     (Layer 2) → output/scored_universe_latest.csv
run_analysis.py    (Layer 3) → analysis_cache, output/reports/<TICKER>_<DATE>.md
run_portfolio.py   (Layer 4) → position_approvals (status=PENDING)
                                ↑
                                │ human approval step (SQL UPDATE or dashboard)
                                ↓
                                position_approvals (status=APPROVED)
run_risk_check.py  (Layer 5) → risk/risk_state.json (and halt.lock if triggered)
run_execution.py   (Layer 6) → open_orders, order_log, portfolio_positions, fills
run_dashboard.py   (Layer 7) → http://localhost:8502 (read-only over fund.db)
```

### Pre-trade veto flow

Inside `run_execution.py --execute`:

```
APPROVED row in position_approvals
        │
        ▼
build limit order params (price, side, shares)
        │
        ▼
risk.pre_trade_veto(ticker, side, shares, price, portfolio_value)
        │
        ├── halt.lock exists? → REJECT, log veto
        ├── closing trade?     → APPROVE, skip checks 2-8
        ├── earnings ≤5 days?  → REJECT, log veto
        ├── trade > 5% ADV?    → REJECT, log veto
        ├── size > 1.5× max?   → REJECT, log veto
        ├── sector > limit?    → REJECT, log veto
        ├── gross > limit?     → REJECT, log veto
        ├── beta > 1.5× max?   → REJECT, log veto
        ├── correlation veto?  → REJECT, log veto
        ▼
APPROVE → broker.submit_order → Alpaca paper API
```

### Layered idempotency

Every layer is **idempotent**. Re-running `python run_data.py` does not duplicate rows; it upserts. Re-running `python run_scoring.py` overwrites the CSV. Re-running `python run_analysis.py` consults the cache and skips unchanged tickers. This means:

- A failed run can be re-run from the start without cleanup.
- Any single layer can be re-run independently if you only need to refresh that layer.
- The order shown above is the *production* order, but each layer reads only what it needs from `fund.db`. There's no streaming dependency.

## Key design decisions

### One SQLite database for everything

Every layer reads and writes the same `data/fund.db` file. There are no per-layer databases, no message queues, no event streams.

**Why:** Single-machine system. Concurrency is sequential (run scripts one at a time). No need for distributed coordination. Operability is critical — a single database means you inspect everything with one `sqlite3` command. Failure modes are obvious.

**What this gives up:** No horizontal scale. No multi-user. No real-time concurrent writes. None of those are goals.

### Layered, not streaming

Each layer is a script that runs to completion, reads its inputs, writes its outputs, exits. There's no long-running orchestrator.

**Why:** Debug-ability. A failed step is the failure of one process; you re-run it. No state to recover, no checkpoints to manage. The CLI is the API.

**What this gives up:** Real-time. The system is fundamentally end-of-day batch.

### LLM as augmentation, not authority

Layer 3 produces qualitative analysis that supplements, but does not replace, the deterministic Layer 2 quant composite. The combined score is `0.7 × quant + 0.3 × ai`. No LLM call is on the critical path of position selection.

**Why:** Determinism. Auditability. Cost. Every long position has a *quant* reason; the LLM adds color but doesn't override.

**What this gives up:** Pure-AI strategies (e.g., "let the LLM read 100 filings and pick the best company"). JARVIS can't do that and isn't designed to.

### Human-in-the-loop on approval

`run_portfolio.py --rebalance` does not submit orders. It writes `PENDING` to `position_approvals`. A human (or external script) must promote rows to `APPROVED` before `run_execution.py --execute` will act.

**Why:** Final sanity check. Any optimizer can produce a pathological output (corner case in the constraint system, weird data); a 30-second human glance catches those.

**What this gives up:** Fully autonomous trading. Intentional.

### Free models by default

Layer 3 uses `google/gemini-2.0-flash-exp:free` via OpenRouter. Real cost is $0. See [ADR-001](adr/001-openrouter-over-anthropic-api.md).

**Why:** Cost-control floor. The free tier means a runaway loop costs nothing, and a personal user can run the system indefinitely.

**What this gives up:** Best-in-class model output (Claude 3.5 Sonnet or GPT-4o would be sharper). Easy to swap by editing config — the architecture doesn't change.

## Scaling characteristics

| Dimension | Where it scales | Where it doesn't |
|-----------|------------------|--------------------|
| Universe size | Up to ~1,500 tickers (Russell 2000) within memory | Beyond that, MVO covariance matrix (1,500² × 8 bytes ≈ 18 MB) is fine, but factor-risk regression slows quadratically |
| Time horizon | Backtest up to 10+ years if `daily_prices` populated | Storage is the limit (~15 GB for full Russell 2000 over 20 years) |
| LLM throughput | 13 calls/min on free Gemini tier | Switching to a paid tier (Claude, GPT-4) gives ~50–100 calls/min, but cost rises linearly |
| Concurrent runs | One at a time | SQLite locks; running two `run_*.py` scripts simultaneously may deadlock |
| Live trading | Single account on Alpaca | No multi-account, no internal allocation logic |

## External dependencies

| Service | Required | Failure mode |
|---------|----------|--------------|
| yfinance (Yahoo) | Yes (Layer 1) | No alt — Layer 1 stalls; Layers 2+ fall back to whatever's in DB |
| SEC EDGAR | Recommended (Layers 1, 3) | Layer 3 filing_analyzer + insider_analyzer return None |
| FINRA | Recommended (Layer 1) | Short-interest factor → score 50 (sector median) |
| Alpaca | Recommended (Layer 6) | Layer 6 in `SIMULATED` mode (synthetic fills) |
| OpenRouter | Recommended (Layer 3) | Layer 3 skipped; combined = quant |
| FRED | Optional (Layer 5) | `credit_spread` and `cs_zscore` return None; VIX still works |
| FMP | Optional (Layer 1) | Earnings transcripts sparse; `earnings_analyzer` falls back to calendar metadata |

The system degrades gracefully against every optional service. Required services (yfinance) are the only hard fails.

## See also

- [ADRs](adr/) — decision records for key architectural choices
- [Database schema](../reference/database-schema.md) — every table the layers read and write
