# Plan: Finish Sprint A + Image Generation Pipeline

## Context

The Midnight Tavern repo currently has a solid backend skeleton (FastAPI app bootstrap, structlog JSON logging, request-id middleware, error envelope, async SQLAlchemy session, base mixins) and a **fully-built but unwired** Dungeon Master module (5 tables, dynamic Anthropic tool generation, prompt-cached agentic loop, full router) at `backend/app/modules/dungeon_master/`. The frontend has typed DM API + Zustand store + polished DM components — but they are **orphaned**: there is no `index.html`, no `main.tsx`, no router, no auth, no chat UI, and `web/package.json` declares zero dependencies.

Sprint A's remaining work is the connective tissue: database migrations, auth/users/characters/chats/messages modules, provider abstraction, the chat-send SSE pipeline that drives DM evaluation before the story AI, and the frontend toolchain + chat UI that finally renders the DM components in place. The user has also locked in scope for the image-generation pipeline, porting providers and LoRA-matching from `Deathwalker-47/Silly-Tavern-Flux-Bridge` (FastAPI/Python, same stack) to avoid reinventing four provider clients.

Auth is **session cookies** (server-side sessions in Postgres, opaque UUID cookie, sliding 30-day TTL) — simpler than JWT for a single-frontend app and avoids key-rotation pitfalls.

Deferred to a later sprint: training (LoRA), lorebooks, moderation. Their empty `__init__.py` stubs stay as-is.

## Approach

Sixteen mergeable PRs, ordered by hard dependencies. Each PR is small enough to review and lands a working slice.

---

### Phase A — Foundation hardening

**PR 1 — Alembic + tests + env scaffolding.**

Files to create:
- `backend/alembic.ini` (script_location = `alembic`, leave sqlalchemy.url empty — read from env)
- `backend/alembic/env.py` — async-aware: import `from app.core.config import settings`; import every module's models module so `Base.metadata` is populated; use `async_engine_from_config` + `connection.run_sync(context.run_migrations)`; `target_metadata = Base.metadata`; `compare_type=True`
- `backend/alembic/script.py.mako`, `backend/alembic/versions/.gitkeep`
- `backend/alembic/versions/0001_initial_dm_tables.py` — captures `game_rulesets`, `dm_sessions`, `character_sheets`, `dm_rolls`, `dm_actions` (FK columns to users/chats/messages stay nullable for now; FKs added in a later migration)
- `backend/.env.example` listing `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `CORS_ORIGINS`, `DEBUG`, `ANTHROPIC_API_KEY`, `DM_DEFAULT_MODEL`, `IMAGES_STORAGE_DIR`, `IMAGE_PROVIDER_ORDER`, `RUNWARE_API_KEY`, `WAVESPEED_API_KEY`, `FAL_API_KEY`, `TOGETHER_API_KEY`, `DEEPSEEK_MODEL`. Warn loudly that rotating `SECRET_KEY` invalidates encrypted provider keys.
- `backend/pytest.ini` (`asyncio_mode = auto`)
- `backend/tests/__init__.py`, `backend/tests/conftest.py` (httpx `AsyncClient` against an in-memory SQLite via `aiosqlite`, `get_db` override with transactional rollback), `backend/tests/test_health.py`
- Add to `backend/requirements.txt`: `cryptography`, `email-validator`, `pytest`, `pytest-asyncio`, `aiosqlite`. Remove `python-jose` (unused under session cookies).

Reuse: `app.core.config.settings`, `app.db.base.Base`, `app.db.base.TimestampMixin`, `app.db.base.SoftDeleteMixin`.

Sharp edge: Alembic env.py silently skips tables if a models module isn't imported — add `app/db/__init__.py` that explicitly imports every models module, and have env.py import from there.

---

### Phase B — Core product modules

Hard order: **auth → users → characters → chats → messages → providers → chat send pipeline → DM scoping**. Each gets its own PR (PR 2–PR 8). Every new router is mounted in `backend/app/api/v1/router.py` (currently mounts only `dm_router` at line 9).

**PR 2 — Auth (session cookies).**

Files: `backend/app/modules/auth/{models.py,schemas.py,service.py,router.py,security.py,dependencies.py}`. New tables `users(id, email UNIQUE, password_hash, created_at, updated_at, deleted_at)` and `sessions(id UUID PK, user_id FK, created_at, last_seen_at, expires_at, revoked_at)`. Migration `0002_auth.py`.

- `security.py`: passlib `CryptContext(schemes=["bcrypt"])` → `hash_password`, `verify_password`, `new_session_token` (uuid4).
- `dependencies.py`: `get_current_user(request, db)` reads `session_id` cookie, joins sessions+users, validates `expires_at > now and revoked_at is null`, refreshes `last_seen_at` only if stale > 60s. Raises `AppError("unauthorized")`.
- Endpoints: `POST /auth/register`, `POST /auth/login` (sets `session_id` cookie: httpOnly, SameSite=Lax, Secure when `not settings.DEBUG`), `POST /auth/logout`, `GET /auth/me`.

Reuse: `app.core.errors.AppError`, `app.db.session.get_db`, `Base`+`TimestampMixin`+`SoftDeleteMixin`.

**PR 3 — Users (profile + encrypted provider keys).**

Files: `backend/app/modules/users/{models.py,schemas.py,service.py,router.py}`, plus `backend/app/core/crypto.py` (`get_fernet()` derives a Fernet key from `settings.SECRET_KEY` via `base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY).digest())`). Table `user_provider_keys(user_id, provider, ciphertext, created_at, updated_at)`. Migration `0003_users.py`.

Endpoints: `GET /users/me`, `PUT /users/me`, `PUT /users/me/provider-keys/{provider}`, `DELETE /users/me/provider-keys/{provider}`, `GET /users/me/provider-keys` (returns provider names only, never ciphertext).

**PR 4 — Characters.** Table `characters(id, user_id FK, name, description, persona, avatar_url, system_prompt, created_at, updated_at, deleted_at)`. CRUD with soft delete, owner-scoped via `get_current_user`. Migration `0004_characters.py`.

**PR 5 — Chats.** Table `chats(id, user_id FK, character_id FK, title, created_at, updated_at, deleted_at)`. CRUD scoped to user. Migration `0005_chats.py`.

**PR 6 — Messages.** Table `messages(id, chat_id FK, message_type ENUM('user','character','dm'), content, metadata JSONB, created_at)`. Cursor pagination on `created_at`. Migration `0006_messages.py`.

**PR 7 — Providers + chat send pipeline.**

Files: `backend/app/modules/providers/{base.py,registry.py,anthropic.py}`.
- `base.py`: `class ProviderAdapter(Protocol)` with `async def generate(...)` and `def stream(...) -> AsyncIterator[str]`.
- `anthropic.py`: wraps `anthropic.AsyncAnthropic`; resolves API key per-user via `users.service.get_provider_key(db, user_id, "anthropic")` (Fernet decrypt); falls back to `settings.ANTHROPIC_API_KEY`.
- `registry.py`: `PROVIDERS = {"anthropic": AnthropicAdapter}`.

Chat-send SSE endpoint `POST /chats/{chat_id}/messages` returns `StreamingResponse` with `X-Accel-Buffering: no` (mirror `dungeon_master/router.py:425`). Single async generator:
1. Persist user message.
2. If a `DMSession` exists for this chat, drive `app.modules.dungeon_master.service.evaluate_action(...)` and forward each yielded `{"type", "data"}` dict as an SSE event (`dm_thinking`/`dm_roll`/`dm_stat_update`/...). Capture `narrative_hint` from the final `dm_done`.
3. If DM verdict is `reject` or `ask`, persist a `message_type=dm` message and emit final `done`. Stop.
4. Otherwise call `AnthropicAdapter.stream(...)`, emit `content_delta` events, accumulate text, persist assistant message with `message_type=character` and the `narrative_hint` injected as a system block before the model call.
5. Emit final `done`.

Reuse: `dungeon_master.service.evaluate_action`, `dungeon_master.service.get_session_by_chat`.

Sharp edge: `evaluate_action` calls `db.commit()` mid-stream (`service.py:417`). That's intentional — each tool execution is durable. Keep it. Use the same `AsyncSession` for both DM and provider calls; if a downstream provider error occurs, DM state stays committed (correct behavior).

**PR 8 — DM scoping + auth wiring.**

Migration `0007_dm_scoping.py`: backfill not required (dev DBs reset); add `user_id NOT NULL` to `game_rulesets` and `dm_sessions`, add FK `dm_sessions.chat_id → chats.id`, FK `dm_actions.message_id → messages.id`. Add `Depends(get_current_user)` to every endpoint in `backend/app/modules/dungeon_master/router.py` and filter all queries by ownership (via `GameRuleset.user_id` or `DMSession.ruleset.user_id`). Drop the unused `HTTPException` import at `router.py:15`.

---

### Phase C — Image generation pipeline

**PR 9 — Images skeleton + dummy provider.**

Files under `backend/app/modules/images/`:
- `models.py`: `ImageJob`, `ImageOutput` per the existing `PLAN_image_generation_architecture.md`. Soft delete on jobs only.
- `schemas.py`, `router.py`, `service.py`.
- `prompt_builder.py`: assembles `prompt_system` + `prompt_user` from user intent → recent chat window → character profile snippets → ruleset hints → safety suffix. Persists both for reproducibility.
- `storage.py`: `class Storage(Protocol)` + `LocalStorage` writing to `settings.IMAGES_STORAGE_DIR/{job_id}/{idx}.png`. Mount `StaticFiles` at `/static/images` in `backend/app/main.py`.
- `providers/base.py`: `class ImageProvider(Protocol)` + typed `ImageProviderError(transient: bool, ...)`.
- `providers/dummy.py`: returns a fixture PNG (lets PR 9 ship without real API keys; integration tests use this).
- Migration `0008_images.py`.

Endpoints: `POST /images/generate`, `GET /jobs/{id}`, `GET /jobs/{id}/events` (SSE), `POST /jobs/{id}/cancel`.

**PR 10 — Real providers + LoRA matching.**

Port from `Deathwalker-47/Silly-Tavern-Flux-Bridge`:
- `providers/{runware,wavespeed,fal,together}.py` — translate any `requests` calls to `httpx.AsyncClient` (already in requirements), `time.sleep` → `asyncio.sleep`, poll loops (Wavespeed/FAL) use async polling with hard timeout. Wrap each provider's exceptions in `ImageProviderError(transient=...)`.
- `providers/chain.py`: `ProviderChain` iterates `settings.IMAGE_PROVIDER_ORDER`, advances on `transient=True`, records `provider_used` on the job.
- `lora.py`: copies `master_lora_dict.json` from the bridge into `backend/data/master_lora_dict.json`, loaded into a module-level dict in the FastAPI `lifespan` startup. `match_loras(text: str) -> list[str]`.
- Optional DeepSeek V3 summarization via Together, gated by `settings.IMAGE_PROMPT_SUMMARIZER_ENABLED`. Called inside `prompt_builder` before persisting `prompt_user`.

**PR 11 — ARQ worker + Redis pub/sub SSE.**

File: `backend/app/workers/main.py` with ARQ `WorkerSettings` and function `generate_image(ctx, job_id)`. Worker creates its **own** engine — do NOT reuse `app.db.session.engine` (ARQ uses its own event loop). On job state changes, publish JSON events to Redis channel `images:job:{id}`. The SSE handler `GET /jobs/{id}/events` subscribes to that channel and yields each message. Run worker via `arq app.workers.main.WorkerSettings`.

Moderation: stub a `Moderator` Protocol with a no-op default in `images/service.py`. No real moderation wired in this sprint.

---

### Phase D — Frontend

**PR 12 — Toolchain bootstrap.**

Fill `web/package.json` with: `react@18`, `react-dom@18`, `vite@5`, `@vitejs/plugin-react`, `typescript@5`, `tailwindcss@3`, `postcss`, `autoprefixer`, `react-router-dom@6`, `zustand`, `clsx`, `@types/react`, `@types/react-dom`. Create:
- `web/vite.config.ts` (proxy `/api → http://localhost:8000` — needed so cookies stay same-site in dev)
- `web/tsconfig.json` (strict, `"jsx": "react-jsx"`, paths `"@/*": ["src/*"]`)
- `web/tailwind.config.js`, `web/postcss.config.js`
- `web/index.html` mounting `#root`
- `web/src/main.tsx`, `web/src/App.tsx`, `web/src/index.css` (Tailwind directives)
- `web/src/api/client.ts`: central `apiFetch` with `credentials: "include"` — and **fix the existing `apiFetch` in `web/src/api/dm.ts` (lines 67-80) to use it**; otherwise cookie-based auth silently fails on every DM call.

**PR 13 — Auth UI.** `src/api/auth.ts`, `src/store/authStore.ts` (Zustand, `user | null`, `bootstrap()` calls `/auth/me` on mount), `src/pages/LoginPage.tsx`, `src/pages/RegisterPage.tsx`, `src/components/ProtectedRoute.tsx`.

**PR 14 — Characters UI.** `src/api/characters.ts`, `src/pages/CharactersPage.tsx`, `src/components/CharacterForm.tsx`.

**PR 15 — Chat UI + SSE.** `src/api/chats.ts`, `src/store/chatStore.ts`, `src/pages/ChatPage.tsx`, `src/components/chat/{MessageList,Composer,SSEStream}.tsx`. The SSE consumer dispatches `dm_*` events to the existing `useDMStore().handleDMEvent` (in `web/src/store/dmStore.ts`) and appends `content_delta` events to the streaming assistant bubble. `ChatPage` mounts the existing `web/src/components/DMPanel/index.tsx` as its right rail. Stored messages with `message_type === "dm"` render via the existing `web/src/components/chat/DMMessage.tsx`.

**PR 16 — Image UI.** `src/api/images.ts`, `src/components/chat/ImageCard.tsx` (subscribes via `EventSource` to `/api/v1/images/jobs/{id}/events`, renders `queued|running|completed|failed`). Composer detects `/image <prompt>` and dispatches `POST /images/generate`.

---

### Phase E — Integration tests

`backend/tests/test_smoke_e2e.py`: register → login (capture cookie) → create character → create chat → POST message → assert SSE stream contains `dm_thinking` (when DM session attached) and `content_delta`, and the assistant message is persisted. Mock `AnthropicAdapter.stream` to yield canned tokens. Image happy path: `POST /images/generate` → poll `GET /jobs/{id}` until completed → assert one `image_outputs` row + file on disk. Use `DummyImageProvider` so no real API keys are needed.

---

## Critical Files to Modify or Create

### Existing files to modify
- `backend/app/api/v1/router.py:9` — mount auth/users/characters/chats/messages/images routers.
- `backend/app/main.py` — mount `StaticFiles` at `/static/images` (PR 9) and ensure `lifespan` loads `master_lora_dict.json` (PR 10).
- `backend/app/db/__init__.py` — explicit imports of every models module so Alembic autogenerate sees them.
- `backend/requirements.txt` — add `cryptography`, `email-validator`, `pytest`, `pytest-asyncio`, `aiosqlite`; remove `python-jose`.
- `backend/app/modules/dungeon_master/router.py:15` — drop unused `HTTPException` import; add `Depends(get_current_user)` (PR 8).
- `web/src/api/dm.ts:67-80` — replace `apiFetch` with `credentials: "include"` (PR 12).
- `web/package.json` — populate dependencies (PR 12).

### New files (highlights — full set listed per PR above)
- `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/0001_*.py`
- `backend/.env.example`, `backend/pytest.ini`, `backend/tests/conftest.py`
- `backend/app/core/crypto.py`
- `backend/app/modules/{auth,users,characters,chats,messages,providers,images}/...`
- `backend/app/workers/main.py`
- `backend/data/master_lora_dict.json` (copied from Silly-Tavern-Flux-Bridge)
- `web/{index.html,vite.config.ts,tsconfig.json,tailwind.config.js,postcss.config.js}`
- `web/src/{main.tsx,App.tsx,index.css}`
- `web/src/api/{client,auth,characters,chats,images}.ts`
- `web/src/store/{authStore,chatStore}.ts`
- `web/src/pages/{LoginPage,RegisterPage,CharactersPage,ChatPage}.tsx`
- `web/src/components/{ProtectedRoute.tsx,CharacterForm.tsx,chat/{MessageList,Composer,SSEStream,ImageCard}.tsx}`

## Existing Utilities to Reuse

- `app.core.config.settings` (`backend/app/core/config.py:31`) — all env-driven config.
- `app.core.errors.AppError` (`backend/app/core/errors.py`) — keep the project-wide error convention; do not raise `HTTPException`.
- `app.db.session.get_db` (`backend/app/db/session.py`) — FastAPI dependency for AsyncSession.
- `app.db.base.Base`, `TimestampMixin`, `SoftDeleteMixin` (`backend/app/db/base.py:10-29`) — every new model.
- `app.modules.dungeon_master.service.evaluate_action` and `get_session_by_chat` — called from the chat send pipeline (PR 7).
- DM router pattern at `backend/app/modules/dungeon_master/router.py:402-419` (SSE `StreamingResponse` with `X-Accel-Buffering: no`) — copy for chat send + image events.
- Frontend: `web/src/store/dmStore.ts` (`handleDMEvent`), `web/src/components/DMPanel/index.tsx`, `web/src/components/chat/DMMessage.tsx` — mount and dispatch into from PR 15.
- Silly-Tavern-Flux-Bridge: `flux_lora_bridge.py` (provider clients), `master_lora_dict.json` (LoRA matching), provider fallback chain pattern — port to async (PR 10).

## Sharp Edges to Watch

1. **Alembic env.py imports** — silently skips tables if a models module isn't imported. Centralize via `app/db/__init__.py`.
2. **Cookie SameSite + Vite proxy** — must proxy `/api` through Vite in dev so cookies stay same-site. Configured in PR 12's `vite.config.ts`.
3. **SSE buffering** — every streaming endpoint needs `X-Accel-Buffering: no` (already done for DM evaluate; mirror for chat send and image events).
4. **Fernet key derivation from SECRET_KEY** — rotating `SECRET_KEY` invalidates all stored provider keys. Document loudly in `.env.example`.
5. **ARQ worker engine** — worker process must build its own asyncpg engine, not import `app.db.session.engine`. ARQ uses its own event loop.
6. **DM commit-mid-stream** — `evaluate_action` calls `db.commit()` between tool calls; if the downstream provider then fails, DM state stays committed. This is intentional (each tool execution is durable) but worth documenting.
7. **Provider port from bridge** — bridge code is likely sync (`requests`, `time.sleep`); translate to `httpx.AsyncClient` and `asyncio.sleep`. Poll loops (Wavespeed/FAL) need hard timeouts.

## Verification

Per PR:
- PR 1: `cd backend && alembic upgrade head` on a fresh dev DB succeeds; `pytest backend/tests/test_health.py` passes.
- PR 2: `pytest -k auth` for register/login/me/logout. Curl: `curl -i -c cookies.txt -X POST /api/v1/auth/login` returns 200 + Set-Cookie.
- PR 3: GET/PUT `/users/me` round-trip; PUT provider-key then re-fetch returns provider name only (no ciphertext leak).
- PR 4-6: CRUD round-trips with pytest fixtures.
- PR 7: `pytest backend/tests/test_smoke_e2e.py::test_chat_send_with_dm` — SSE stream contains both `dm_*` events and `content_delta`, assistant message persisted.
- PR 8: Run `alembic upgrade head`; verify FKs exist (`\d dm_sessions` in psql). Unauthenticated DM endpoint calls return 401.
- PR 9: POST `/images/generate` with `DummyImageProvider` selected → job transitions queued → completed; fixture PNG on disk; SSE stream emits `image_completed`.
- PR 10: Mock provider HTTP responses with `respx`; assert chain advances on transient errors.
- PR 11: Run `arq app.workers.main.WorkerSettings` in one terminal; POST a real generation; SSE stream pushes progress within < 1s of worker update.
- PR 12-16: `cd web && npm install && npm run dev` boots Vite; `/login`, `/register`, `/characters`, `/chats/:id` all render; cookies persist across reload (`document.cookie` shows nothing — confirms httpOnly); sending a message in a chat with a DM session streams DM bubbles in the message list + updates the DMPanel sidebar simultaneously; `/image a wizard in moonlight` produces an inline ImageCard that transitions to the rendered image.

End-to-end smoke (final acceptance): `pytest backend/tests/test_smoke_e2e.py` green, plus a manual run-through of the full UI flow from register to image generation.
