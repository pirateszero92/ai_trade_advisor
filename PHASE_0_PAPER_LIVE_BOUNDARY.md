# Phase 0: Paper / Live Safety Boundary

## Invariants

- The application and backend always start in Paper mode.
- Live authorization is an opaque in-memory token and is never persisted.
- A backend or mobile restart invalidates Live mode.
- Paper order routes call `PaperExecutionEngine`, which owns no broker client.
- Local paper automation cannot fill, modify, or close a Live ledger record.
- New Live exposure remains disabled until broker-side protective-order OMS is implemented.
- Risk-reducing cancellation of an existing InnovestX order is allowed only with a valid Live Session.

## Canonical API namespaces

Paper:

- `POST /api/v1/paper/orders`
- `GET /api/v1/paper/orders`
- `POST /api/v1/paper/orders/{trade_id}/close`
- `DELETE /api/v1/paper/orders/{trade_id}`
- `GET /api/v1/paper/account`
- `POST /api/v1/paper/account/reset`

Live gateway:

- `POST /api/v1/live/session`
- `GET /api/v1/live/session`
- `DELETE /api/v1/live/session`
- `POST /api/v1/live/kill-switch`
- `GET /api/v1/live/account`
- `POST /api/v1/live/orders/innovestx/cancel`

Opening a Live Session requires the exact confirmation value
`ENABLE_LIVE_TRADING`, a secure `APP_SECRET_KEY`, configured InnovestX
credentials, and a successful broker preflight. Subsequent calls require both
`X-API-Key` and `X-Live-Session-Token`.

## Storage

- Paper ledger: `config/paper_trades_store.json`
- Live ledger: `config/live_trades_store.json`
- Legacy mixed ledger: `config/trades_store.json` (read-only migration source)

The first trade mutation imports legacy records into the appropriate isolated
ledger without deleting the legacy file.

## Deployment note

Backend source is copied into the Docker image and is not bind-mounted by the
Compose configuration. Backend code changes therefore require an image rebuild,
not only a container restart. Flutter source changes also require rebuilding the
web or mobile artifact before distribution.

