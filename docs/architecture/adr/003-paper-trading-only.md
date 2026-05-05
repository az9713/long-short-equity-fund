# ADR 003: Paper trading by default; live mode gated by an explicit env var

**Status:** Accepted

## Context

Alpaca's API is the same shape for paper and live trading; the difference is one boolean (`paper=True` vs `paper=False`) when constructing the `TradingClient`. A single misconfiguration could send real orders against a real brokerage account.

JARVIS is a personal research project written by a non-professional engineer. Bugs in the optimizer, the pre-trade veto, or the order manager could plausibly cause significant losses if pointed at a live account. The cost of a mistake is asymmetric — a paper-trading bug is annoying, a live-trading bug is financial harm.

## Decision

The execution layer is **paper-trading only by default**. Live mode is gated by **two switches that must both be set**:

1. `config.yaml: execution.mode: live`
2. `ALPACA_LIVE_CONFIRMED=YES_I_UNDERSTAND_THE_RISKS` environment variable

The check, in `execution/broker.py: _is_live_mode`:

```python
def _is_live_mode() -> bool:
    mode = cfg.get("mode", "paper")
    confirmed = os.getenv("ALPACA_LIVE_CONFIRMED", "")
    return mode == "live" and confirmed == "YES_I_UNDERSTAND_THE_RISKS"
```

If either is missing or misspelled, the broker stays in paper mode and logs a warning. The string `YES_I_UNDERSTAND_THE_RISKS` is intentionally verbose so it cannot be set accidentally (no `1`, no `true`, no `yes`).

## Alternatives considered

### Option A: Single switch (config flag)
- Pros: Simpler.
- Cons: Editing a YAML file is too easy. A user copying their config to a new machine could flip it without thinking.

### Option B: Two switches (chosen)
- Pros: Defense in depth. The env var is process-scoped, can't be checked in to git, and forces a moment of explicit decision per session.
- Cons: Slightly fiddly UX. Acceptable because live mode should be fiddly.

### Option C: Compile-time constant
- Pros: Maximum safety — code change required to enable live.
- Cons: Too restrictive; the user reasonably wants a path to live trading once confident.

### Option D: Remove live mode entirely
- Pros: Cannot misuse.
- Cons: Forecloses a legitimate future use case. The architecture supports live mode cleanly; removing it is overreaction.

## Rationale

Live trading should be *deliberate*. The two-switch pattern requires the user to:

1. Edit a config file (intentional act, version-controlled).
2. Type a verbose string into an env var (intentional act, not stored).

This makes accidental live trading essentially impossible. The user can still get there with one minute of effort when they choose to.

The verbose env-var string ("YES_I_UNDERSTAND_THE_RISKS") is also a self-documenting affordance. Anyone reading the code or the config sees the safety check and understands the intent without reading a separate doc.

## Trade-offs

- **Friction.** A user who genuinely wants live mode has to set up two things. Acceptable — and arguably good — for the use case.
- **No "always-on" production mode.** The system isn't designed for unsupervised live trading. If someone wants to run JARVIS as a real-money production system, they should fork it and reconsider the architecture from first principles.

## Consequences

- The default `.env.example` does not include `ALPACA_LIVE_CONFIRMED`. Users see no hint that live mode exists unless they read the docs or source.
- `config.yaml: execution.mode: paper` is the shipped default. Switching to `live` alone produces no change — the broker stays in paper mode and warns.
- Live-mode invocations are visually prominent in logs:
  ```
  LIVE TRADING MODE - REAL MONEY
  ```
  Versus paper:
  ```
  PAPER TRADING MODE
  ```
- The same code path serves both modes; only the `paper` flag on `TradingClient` differs. No separate "live" code branch to drift out of sync.
