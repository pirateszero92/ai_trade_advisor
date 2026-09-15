# Review remediation — 2026-09-07

The prior summary said 23 findings. Its enumerated entries actually contained 25 (2 Critical, 11 High, 12 Medium). This ledger follows the individual entries rather than the incorrect total.

| Finding | Resolution |
| --- | --- |
| Hardcoded shared API key | Removed fallback; rotated root/backend environment keys; rebuild client bundles. Users must provision the replacement key in Settings. Existing Git history and installed old binaries retain the revoked value. |
| Observation-only scenarios approve orders | Strict boolean actionable check in Strategy Engine and a second check in Auto-Pilot, including missing scenarios and all pressure scores. |
| WebSocket has a separate strategy pipeline | Signals WS uses execution service and metadata; canonical runtime entry mode is shared with AI and HTTP. Scanner uses configured trigger TF. Non-execution TF cannot obtain authoritative service results. |
| Undefined exception logger | Added logger import; sentiment failure no longer raises NameError. |
| Wrong price-change horizon | Hourly derivatives are evaluated only with hourly execution. Other execution TFs do not import hourly derivatives. |
| API failures look neutral/healthy | Any failed/missing Binance component returns unavailable; Bybit OI-only returns partial rather than healthy complete data. |
| Synthetic VPIN acts as institutional gate | Estimated candle-volume split is advisory; only validated measured buy/sell flow can set toxicity. |
| AI borrows mismatched scanner context | Removed scanner lookup. Server execution snapshot overrides all client decision fields; unavailable snapshot produces no-entry response. |
| Overlay response races | Capture identity and generation before await; reject old responses before mutation, including error handlers. |
| Ticker response races | Symbol/market/exchange/TF identity plus request generation guard. |
| Chat response races | Conversation generation/identity guard; symbol and TF switch invalidate old response and loading state. Limit outgoing history to latest 50 messages. |
| Runtime portfolio data tracked | Removed six runtime files from Git index only; local files retained; ignore rules and Docker exclusions added. Historical commits are not rewritten. |
| Release uses debug signing | Release requires external signing environment; debug remains available for emulator. No production private signing key generated. |
| Mutable derivatives cache | Store and return deep copies; cache key includes exchange; serialized details also copied. |
| Per-request HTTP clients | Shared bounded connection pool, closed on application shutdown. Network latency is still external and no scan-time SLA is claimed. |
| HMM conflates bearishness and volatility | Bearish label becomes bearish_trend; volatility uses expected normalized range versus observed quartiles. |
| Oldest-zone priority versus nearest-zone comment | Review correction: existing regression test proves oldest pending reaction is intentionally latched. Preserve that behavior and correct misleading comment; no arbitrary threshold change. |
| Scanner mode races | Request generation and mode checks for signals, positions and explicit scan responses. |
| WS bypasses watchlist/mode | Treat WS signal as refresh notification; authenticated HTTP projection applies filters. |
| Android default HTTP blocked | Network security allows only local development hosts; other cleartext remains denied. |
| Unbounded lock registry | Weak-value lock registries retain active/waiting locks and release idle entries across all three analysis services. |
| Stale TF labels | Reaction/auto-trade messages use actual TF; README reflects configured 1H and distinguishes execution from research. |
| Internal error strings | Generic execution/research and quote-validation errors; validation errors remain explanatory. |
| Unbounded dependency versions | Deployment constraints lock direct/transitive versions to running Linux environment; Docker uses lock. Package hashes are not yet pinned. |
| Testing gaps | Added perfect-score observation rejection, measured versus estimated VPIN, partial/failed derivatives, exchange/copy isolation, AI server authority, TF rejection and lock cleanup tests; updated existing confirmed-signal fixtures explicitly. |

No live broker order is part of remediation verification. Directional analysis remains distinct from order approval. Strategy/Risk thresholds are not relaxed to manufacture more entries.

Verification: backend 233 passed / 2 skipped (3 existing Pandas deprecation warnings); Flutter 15 passed; Dart analyzer clean; Python critical-error lint clean. Live read-only smoke check matches Scanner/Chart/WebSocket snapshot IDs and Strategy approval on configured 1H, even when WS requests 4H. Backend recreated and healthy; web rebuilt/deployed (HTTP 200); debug APK installed on Pixel_10_Pro and app restarted. Replacement API key must be entered in each client's Settings. Production release signing still requires the owner's external keystore.
