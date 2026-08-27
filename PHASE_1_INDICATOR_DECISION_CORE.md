# Phase 1: Indicator Decision Core

Phase 1 makes the project's three approved indicators configurable,
explainable and fail-safe without adding a fourth indicator.

## Registered indicators

1. `smc_structure` — market structure, location, liquidity, Order Block and FVG
2. `volume_delta` — Volume Delta, CVD pressure, absorption and volume expansion
3. `squeeze_momentum` — compression release and direction-aligned momentum

The registry is intentionally closed to unknown indicator IDs. A future
indicator can be added by registering its metadata and scorer; the analysis
pipeline and Chart UI consume the registry dynamically and do not need another
hard-coded branch.

## Decision model

Each enabled layer returns a normalized score from 0 to 100 plus evidence.
The final confluence score is:

```text
sum(layer normalized score × configured weight) / sum(enabled weights)
```

An unavailable enabled layer contributes zero points and reduces data coverage.
Disabled layers are removed from both score normalization and coverage. A layer
marked `required` blocks the Strategy entry gate when its data is unavailable.
The global `minimum_data_coverage` also blocks entry when coverage is too low.

The default profile keeps the established weights:

- SMC Structure: 40
- Volume Delta & CVD: 30
- Squeeze Momentum: 30
- Minimum data coverage: 70%

## Configuration

The active configuration is stored under `indicator_core` in
`backend/config/strategy.yaml`. Parameters are validated with safe ranges and
unknown fields or indicator IDs are rejected. Configuration reads use an
mtime-aware in-memory cache, so high-frequency signal analysis does not parse
YAML repeatedly.

Authenticated configuration endpoints:

- `GET /api/v1/settings/indicator-core`
- `PUT /api/v1/settings/indicator-core`

Updates are validated and written atomically while preserving all other
strategy sections. The next analysis sees the new configuration automatically.

## Explainability UI

The Chart displays a `3-INDICATOR CORE` strip with:

- final score and `DATA OK` / `DATA BLOCK` indicator-readiness state;
- current data coverage;
- dynamic SMC, CVD and SQZ layer scores;
- a detail sheet containing each layer's status, weight, contribution and
  evidence, including explicit reasons when the entry gate is blocked.

The same Flutter implementation is used by Android and web.

## Performance and safety changes

- Squeeze rolling linear regression is vectorized instead of calling
  `polyfit` once per candle.
- Squeeze and Volume Delta failures are isolated; failure in one layer no
  longer suppresses the other.
- Strategy approval can require a ready indicator decision, preventing entries
  based on silently missing data.
- Paper/Live routing remains governed by the Phase 0 boundary and is not changed
  by indicator configuration.
