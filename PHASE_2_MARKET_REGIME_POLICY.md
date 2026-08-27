# Phase 2: Market Regime & Adaptive Decision Policy

Phase 2 makes the deterministic entry and risk rules adapt to the current
market environment. It does not add a fourth indicator and does not add points
to the Phase 1 confluence score.

## Market states

The classifier produces one of five states:

- `trending` — efficient directional movement with aligned SMC/HTF/momentum
  evidence;
- `ranging` — low directional efficiency, requiring a liquidity sweep before
  a selective mean-reversion entry;
- `volatile` — ATR expansion relative to the market's own recent baseline,
  requiring stronger confluence, higher R:R and direction-aligned Volume Delta;
- `compression` — the existing Squeeze layer reports active compression, so
  new entries remain blocked until release;
- `unknown` — missing or invalid history, which fails closed.

ATR ratio, volatility percentile and path efficiency are market-state
statistics only. They are not registered trading indicators, do not generate
entry direction and do not contribute to confluence. Entry direction remains
the responsibility of the three approved layers:

1. SMC Structure
2. Volume Delta & CVD
3. Squeeze Momentum

## Default adaptive policy

| Regime | New entry | Min confluence | Min R:R | Risk multiplier | Extra gate |
| --- | --- | ---: | ---: | ---: | --- |
| Trending | Selective | 65 | 2.0 | 1.00 | Direction alignment |
| Ranging | Selective | 75 | 2.0 | 0.65 | Liquidity sweep |
| Volatile | Selective | 82 | 2.5 | 0.40 | Direction + Volume Delta |
| Compression | Blocked | 85 | 2.5 | 0.00 | Wait for Squeeze release |
| Unknown | Blocked | 100 | 3.0 | 0.00 | Wait for valid data |

The adaptive policy can only tighten the base strategy. Effective confluence
and R:R are the maximum of the base strategy and regime values. Risk
multipliers are validated between zero and one, so a regime can never increase
the user's configured risk budget.

No classifier can guarantee profit in every market. The objective is to avoid
using one entry behavior in incompatible conditions, preserve capital during
uncertainty and make every approve/reject decision explainable.

## Integration

- `SMCEngine` classifies regime after the three indicator layers are complete.
- `StrategyEngine` applies the regime's hard entry gates and thresholds.
- `RiskEngine` applies the regime multiplier after drawdown adjustment.
- The proactive scanner publishes regime, policy and indicator decision data.
- AI receives the deterministic policy as an authoritative constraint and is
  instructed not to override blocked entries or increase risk.

Authenticated configuration endpoints:

- `GET /api/v1/settings/regime-policy`
- `PUT /api/v1/settings/regime-policy`

Configuration is stored under `regime_policy` in
`backend/config/strategy.yaml`. Indicator and regime settings share one atomic
YAML update lock, preventing concurrent section updates from overwriting each
other.

## Client UI

The Chart page displays a `MARKET REGIME` strip with regime, direction,
confidence, effective confluence/R:R, risk multiplier and entry state. Tapping
it opens the policy, state statistics and evidence. Proactive signal cards also
show the regime and risk multiplier, and keep execution disabled when the
regime entry gate is closed.

Phase 0 Paper/Live isolation remains unchanged. The regime policy is decision
support and does not grant Live authorization or bypass broker safety checks.
