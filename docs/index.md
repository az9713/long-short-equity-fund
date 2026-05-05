# JARVIS — Meridian Capital Partners

A seven-layer long-short equity research and paper-trading system. Quantitative scoring, AI analysis, MVO portfolio construction, risk monitoring, and Alpaca execution — all running locally from the command line.

---

## Documentation

| Section | What's inside |
|---------|--------------|
| [Overview](overview/what-is-this.md) | What JARVIS is, the seven layers, how the pieces fit together |
| [Key concepts](overview/key-concepts.md) | Glossary of every term used elsewhere in the docs |
| [Getting started](getting-started/quickstart.md) | Install, populate data, score a 10-ticker universe in 15 minutes |
| [Onboarding](getting-started/onboarding.md) | Conceptual zero-to-hero for someone new to long-short equity |
| Concepts | Deep dives — one per layer |
| &nbsp;&nbsp;&nbsp;&nbsp;[Data layer](concepts/data-layer.md) | Universe, prices, fundamentals, SEC, insiders, 13F |
| &nbsp;&nbsp;&nbsp;&nbsp;[Scoring engine](concepts/scoring-engine.md) | 8 factors, 27 subfactors, sector-relative ranking, crowding |
| &nbsp;&nbsp;&nbsp;&nbsp;[AI analysis](concepts/ai-analysis.md) | OpenRouter analyzers, sector synthesis, combined score |
| &nbsp;&nbsp;&nbsp;&nbsp;[Portfolio construction](concepts/portfolio-construction.md) | MVO and conviction-tilt, beta and sector neutrality |
| &nbsp;&nbsp;&nbsp;&nbsp;[Risk management](concepts/risk-management.md) | Pre-trade veto, circuit breakers, factor model, stress tests |
| &nbsp;&nbsp;&nbsp;&nbsp;[Execution](concepts/execution.md) | Alpaca paper trading, limit orders, slippage tracking |
| &nbsp;&nbsp;&nbsp;&nbsp;[Reporting and dashboard](concepts/reporting-and-dashboard.md) | Tear sheet, attribution, weekly letter, Streamlit UI |
| &nbsp;&nbsp;&nbsp;&nbsp;[Backtesting](concepts/backtesting.md) | Walk-forward simulation, bias caveats |
| Guides | Task-oriented how-tos |
| &nbsp;&nbsp;&nbsp;&nbsp;[Run the full pipeline](guides/run-the-full-pipeline.md) | End-to-end nightly cycle |
| &nbsp;&nbsp;&nbsp;&nbsp;[Run a backtest](guides/run-a-backtest.md) | Walk-forward simulation with caveats |
| &nbsp;&nbsp;&nbsp;&nbsp;[Analyze a single ticker](guides/analyze-a-single-ticker.md) | Score and AI-analyze one stock |
| &nbsp;&nbsp;&nbsp;&nbsp;[Handle a circuit breaker](guides/handle-a-circuit-breaker.md) | Diagnose, clear halt, resume |
| &nbsp;&nbsp;&nbsp;&nbsp;[Switch to the full universe](guides/switch-to-full-universe.md) | Move from dev mode to S&P 500 |
| Reference | Authoritative lookup |
| &nbsp;&nbsp;&nbsp;&nbsp;[CLI](reference/cli.md) | Every `run_*.py` script and flag |
| &nbsp;&nbsp;&nbsp;&nbsp;[Configuration](reference/configuration.md) | Every `config.yaml` field |
| &nbsp;&nbsp;&nbsp;&nbsp;[Environment variables](reference/env-vars.md) | Every `.env` key |
| &nbsp;&nbsp;&nbsp;&nbsp;[Database schema](reference/database-schema.md) | Every SQLite table |
| Architecture | Design rationale |
| &nbsp;&nbsp;&nbsp;&nbsp;[System design](architecture/system-design.md) | Layered architecture, data flows, dependencies |
| &nbsp;&nbsp;&nbsp;&nbsp;[ADRs](architecture/adr/) | Decision records |
| [Troubleshooting](troubleshooting/common-issues.md) | Top failures and fixes |

> **New here?** Read [what-is-this](overview/what-is-this.md), then follow the [quickstart](getting-started/quickstart.md).
