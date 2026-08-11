# CLAUDE.md

Guidance for AI assistants working in this repository.

## What this is

**meu-crm** is a self-contained commercial CRM for lead management and sales
pipeline automation, built for the Brazilian market (UI, enum labels, and
messages are in Portuguese). It ships as a **FastAPI + PostgreSQL** backend plus
a **single-file HTML frontend** (`frontend.html`), deployed as one container to
Railway.

Core capabilities: lead CRUD with activity history, a Kanban sales pipeline,
outbound webhooks (HMAC-signed, with retries and delivery logs), a generic
inbound webhook, Meta (Facebook/Instagram) Lead Ads ingestion, JWT auth with
role-based access, and an in-process nurture (drip) scheduler.

## Layout

```
main.py                       # App entry: FastAPI init, lifespan, CORS, router wiring, health checks
database.py                   # SQLAlchemy engine, SessionLocal, Base, get_db() dependency
models.py                     # All ORM models + enums (single file)
schemas.py                    # All Pydantic request/response schemas (single file)
frontend.html                 # Entire SPA — HTML + CSS + vanilla JS, served at "/"
routes/
  auth.py                     # /auth  — login, register (bootstrap), user management
  leads.py                    # /leads — CRUD, stage moves, activities
  pipeline.py                 # /pipeline — pipeline stage CRUD
  webhooks.py                 # /webhooks — outbound webhook CRUD, test, deliveries, inbound receiver
  meta.py                     # /meta — Meta Lead Ads verify + receive + stats
services/
  auth.py                     # JWT create/decode, bcrypt hashing, get_current_user / require_admin deps
  webhook_dispatcher.py       # dispatch_event(), HMAC signing, retry/backoff, delivery logging
  nurture_scheduler.py        # APScheduler job (every 60s) that advances nurture enrollments
requirements.txt              # Pinned Python deps
Dockerfile                    # python:3.12-slim image
railway.json                  # Railway deploy config (Dockerfile builder)
```

There is **no `README`, no test suite, no linter/formatter config, and no
migration setup wired in** (see Conventions). `alembic` is in `requirements.txt`
but no `alembic.ini` / `migrations/` exist — schema is created at runtime.

## Architecture & key conventions

- **Table creation at startup, not migrations.** `lifespan` in `main.py` calls
  `Base.metadata.create_all(bind=engine)` on every boot. It also **seeds 6
  default pipeline stages** if the `pipeline_stages` table is empty. This means
  **adding a column to a model does NOT alter an existing table** —
  `create_all` only creates missing tables. For schema changes on an existing
  database you must migrate manually (Alembic is installed but not configured)
  or drop/recreate. Keep this in mind before assuming a new model field will
  appear in production.
- **Models and schemas are each one file.** Add new ORM classes to `models.py`
  and new Pydantic models to `schemas.py`; don't split them out unless doing a
  deliberate refactor.
- **Enums are string-valued with Portuguese labels** (e.g.
  `LeadStatus.novo = "Novo Lead"`). The DB stores the *value* string. When
  serializing to webhook/JSON dicts, use `enum_member.value`.
- **Routers** live in `routes/`, each exposing `router = APIRouter(prefix=..., tags=[...])`,
  and are registered in `main.py` via `app.include_router(...)`. A new feature
  area = a new file in `routes/` + one `include_router` line.
- **DB sessions** come from the `get_db` dependency (`Depends(get_db)`). The
  engine uses `NullPool` (no connection pooling — suited to serverless/Railway).
  Default `DATABASE_URL` points at a local Postgres; override via env var.
- **Business logic that isn't request handling** goes in `services/`
  (dispatching, scheduling, auth primitives). Routes stay thin.
- **Activity log:** most mutations on a lead append an `Activity` row
  (`type` is a free-form string like `created`, `updated`, `stage_change`,
  `meta_import`, `webhook_inbound`, `nurture_email`). Preserve this pattern when
  adding lead-mutating endpoints.
- **Soft deletes:** leads, pipeline stages, and users are deactivated by setting
  `is_active = False` (see `DELETE` handlers), not physically removed. Webhooks
  are the exception — they are hard-deleted.

### Naming caveat — `metadata` vs `extra_data`

SQLAlchemy reserves the attribute name `metadata` on declarative models, so ORM
columns use **`extra_data`** (`Activity.extra_data`, `NurtureStep.extra_data`,
etc.). The **Pydantic schemas** still expose the field as **`metadata`**
(`ActivityCreate.metadata`, `NurtureStepCreate.metadata`). When mapping between
them, translate `metadata` ⇄ `extra_data`. Don't rename model columns back to
`metadata` — it will raise at import time.

## Auth model

- JWT (HS256, 8-hour expiry) via `services/auth.py`; secret from `SECRET_KEY`
  env var (has an insecure default — set it in production).
- Passwords are bcrypt-hashed (`passlib` + `bcrypt`); never stored in plaintext.
- `Depends(get_current_user)` protects a route; `Depends(require_admin)` further
  requires `role == "admin"`. Roles are `admin` and `vendedor` (salesperson).
- **Bootstrap:** `POST /auth/register` is only open while zero users exist, and
  the first registrant automatically becomes `admin`. After that, registration
  is disabled and admins create users via `POST /auth/users`.
- **Visibility rule:** in `GET /leads`, admins see all leads; a `vendedor` sees
  only leads where `owner_id == their id`. New leads are owned by their creator.
  Note: several other lead endpoints (`GET /leads/{id}`, `move`, `delete`,
  activities) currently do **not** enforce ownership — match or tighten this
  intentionally, don't assume it's already enforced.

## Integrations

- **Outbound webhooks** (`services/webhook_dispatcher.py`): `dispatch_event`
  finds active webhooks subscribed to an event, wraps the payload in an
  `{event, timestamp, data, lead_id}` envelope, and delivers concurrently.
  Failures retry with exponential backoff (`2**attempt` seconds) up to
  `retry_count`. Every attempt is logged to `webhook_deliveries`. Payloads are
  signed with HMAC-SHA256 in the `X-CRM-Signature` header when a secret is set
  (auto-generated if not provided). Emit new events by calling
  `dispatch_event(...)` — usually via `background_tasks.add_task(asyncio.run, ...)`
  from a route (see `routes/leads.py`). Add new event types to the
  `WebhookEvent` enum in `models.py` **and** the label/description maps in
  `routes/webhooks.py::list_events`.
- **Inbound generic webhook:** `POST /webhooks/incoming/{slug}` accepts arbitrary
  JSON and creates a lead (`source=webhook`), storing the raw body in
  `custom_fields`.
- **Meta Lead Ads** (`routes/meta.py`): `GET /meta/webhook` answers Meta's
  verification challenge (`META_VERIFY_TOKEN`); `POST /meta/webhook` validates
  the `X-Hub-Signature-256` HMAC when `META_APP_SECRET` is set, parses
  `leadgen` changes into leads, and logs raw payloads to `meta_webhook_logs`.
- **Nurture scheduler** (`services/nurture_scheduler.py`): an `AsyncIOScheduler`
  job runs **every 60s in the same process** (no Celery/Redis), advancing
  `active` enrollments whose `next_step_at` has passed. Email/WhatsApp steps are
  currently **stubs that only log** — real sending (Brevo/SendGrid, Meta
  WhatsApp Cloud API) must be wired into `_send_email` / `_send_whatsapp`.
  **Note:** nurture models/schemas and the scheduler exist, but there are **no
  CRUD routes** to create sequences/steps/enrollments yet — they'd have to be
  seeded directly in the DB. Adding these routes is a natural extension point.

## Frontend

`frontend.html` is the whole SPA: HTML, inline CSS (dark theme, CSS variables),
and vanilla JS — no build step, no framework. It's served directly from `GET /`
by FastAPI (`FileResponse`). Key JS behavior (near the bottom `<script>`):

- `API_BASE` is `window.location.origin` when hosted, else
  `localStorage['crm_api_url']` or `http://localhost:8000` locally.
- JWT is stored in `localStorage['crm_token']` and sent as
  `Authorization: Bearer <token>` on API calls.

When changing API request/response shapes, update the corresponding `fetch`
calls here too — there's no shared client or type generation.

## Running & deploying

```bash
# Local (needs a reachable PostgreSQL; set DATABASE_URL if not the default)
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# Then open http://localhost:8000  (frontend) and /docs (Swagger UI)
```

- **Docker/Railway:** `Dockerfile` builds on `python:3.12-slim`; `railway.json`
  runs `uvicorn main:app --host 0.0.0.0 --port 8000`. Restart policy: on-failure,
  max 3 retries.
- **API docs** are always available at `/docs` (Swagger) and `/redoc`.
- **Health:** `GET /health` reports app + database status; `GET /` returns the
  frontend (or a JSON status blob if `frontend.html` is missing).

### Environment variables

| Var | Purpose | Default (insecure — override in prod) |
| --- | --- | --- |
| `DATABASE_URL` | Postgres connection string | `postgresql://crm_user:crm_pass@localhost:5432/crm_db` |
| `SECRET_KEY` | JWT signing secret | `crm-secret-key-mude-em-producao-2025` |
| `META_VERIFY_TOKEN` | Meta webhook verification token | `crm_meta_verify_token_2025` |
| `META_APP_SECRET` | Meta app secret for HMAC validation | `""` (validation skipped when empty) |

## Gotchas

- **CORS is wide open** (`allow_origins=["*"]`) — the code comments flag this;
  lock it down for production.
- `meta_stats` has a known quirk: there is **no `LeadStatus.instagram` /
  `LeadSource.instagram`** enum member, so the Instagram-count branch falls back
  to counting Facebook leads. Don't rely on its `total_ig`.
- `create_all` won't migrate existing tables (see Architecture) — plan schema
  changes accordingly.
- No tests exist. If you add meaningful logic, prefer adding tests (e.g.
  `pytest` + FastAPI `TestClient`) rather than assuming a harness is present.
- Keep user-facing strings (API messages, enum labels, frontend copy) in
  **Portuguese** to match the existing codebase.
