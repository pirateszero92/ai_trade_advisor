# Operations, Deployment & Rollback

## Deployment topology

Root Docker Compose runs:
- Nginx/Flutter Web;
- FastAPI backend;
- PostgreSQL 16;
- Redis 7.

Default root bindings are local-only:
- web: 127.0.0.1:3000 and 127.0.0.1:8080;
- backend: 127.0.0.1:8000;
- PostgreSQL: 127.0.0.1:5432;
- Redis: 127.0.0.1:6379.

Use a TLS reverse proxy before external exposure.

## Required preparation

1. Copy environment templates:
   ```bash
   cp .env.example .env
   cp backend/.env.example backend/.env
   ```
2. Replace placeholder secrets.
3. Keep real secrets out of Git.
4. Confirm PostgreSQL and Redis destinations.
5. Confirm allowed LLM hosts.
6. Keep trading mode at Paper during deployment verification.

## Build

Flutter Web:

```bash
cd mobile
flutter build web
cd ..
```

Backend / full stack:

```bash
docker compose build
docker compose up -d
```

## Pre-deployment checks

Run:
```bash
cd backend
python -m pytest -q
```

Then:
```bash
cd ../mobile
flutter test
```

Verify:
- /health is healthy;
- /metrics responds;
- /api/v1/settings/config/contract returns expected hashes/revision;
- runtime config has no invalid fields;
- Paper mode is active;
- no unintended Live Session exists.

## Configuration changes

Before changing behavior:
- capture strategy hash;
- capture runtime hash/revision;
- record reason/change owner;
- retain prior config for rollback.

Runtime config updates are validated before persistence and increment an in-process revision.

## Live safety

Live mode is not restored from persisted runtime configuration.

Live requires an explicit short-lived session and can be revoked through the live-session/kill-switch controls.

AI cannot create or enable Live authorization.

## Rollback procedure

### Application rollback

1. Stop order creation / disable AutoPilot.
2. Confirm no unintended pending orders.
3. Preserve Decision Trace and OMS state.
4. Restore the previous known-good application revision/image.
5. Restore compatible config files if they changed.
6. Restart backend.
7. Verify database migrations are compatible before reverting application code.
8. Run health and smoke checks in Paper mode.
9. Verify config hashes against the rollback record.
10. Re-enable automation only after validation.

### Config-only rollback

1. Disable AutoPilot.
2. Restore previous validated strategy/runtime config.
3. Reload/restart relevant services.
4. Confirm config hash/revision.
5. Run a replay/smoke check.
6. Resume Paper mode evaluation before Live use.

### Database rollback

Prefer forward-compatible migrations. Before any destructive migration:
- take a PostgreSQL backup;
- record schema revision;
- test restore procedure.

Do not roll back application code across incompatible DB schema changes without an explicit migration plan.

## Backup targets

Preserve:
- PostgreSQL data;
- backend/config;
- backend/data;
- backend/prompts;
- environment/secrets via secure external storage;
- release artifacts;
- validation/release evidence.

## Incident response

If behavior is unexpected:
1. disable AutoPilot;
2. revoke Live Session if active;
3. capture Decision Trace IDs;
4. capture config contract hashes;
5. capture metrics/logs;
6. inspect candidate lifecycle;
7. reproduce using immutable evidence/replay;
8. patch only after root cause is identified.

## Deployment invariant

```text
Deployment must never change trading authority implicitly. Behavioral changes require identifiable config/code versions and a reversible rollout path.
```
