# Midnight Tavern

## Project
Character/roleplay chat web app. SillyTavern-inspired with multi-LLM support, inline context-based image gen, and per-character Flux LoRA training. NSFW allowed (except CSAM/illegal).

## Stack
- Backend: FastAPI (Python 3.12), Pydantic v2, SQLAlchemy 2.0 (async), Alembic
- Frontend: React 18+ with TypeScript, Vite, TailwindCSS
- DB: PostgreSQL (async via asyncpg)
- Queue: Redis + ARQ (async background jobs)
- Streaming: SSE (Server-Sent Events) via FastAPI StreamingResponse
- Storage: S3-compatible (Cloudflare R2 or AWS S3) — later phase

## Architecture
Modular monolith. Single API + single web frontend. Internal module boundaries that map cleanly to services later.

## Code Conventions
- API routes: /api/v1/...
- Error shape: { "error": { "code": "...", "message": "...", "details": ... }, "request_id": "..." }
- Structured JSON logging with request_id and user_id
- Soft deletes (deleted_at column) on user-facing resources
- Conventional commits: feat:, fix:, chore:, refactor:, docs:
- Always push to origin main after committing
- Request ID middleware on all endpoints
- Async everywhere — no sync DB calls, no sync HTTP calls
- Type hints on all functions
- Pydantic models for all request/response schemas

## Backend Structure
- backend/app/main.py — FastAPI app bootstrap with lifespan handler
- backend/app/core/ — config, logging, middleware, security, error formatting
- backend/app/db/ — engine, session, base models, Alembic migrations
- backend/app/api/v1/ — versioned routers
- backend/app/modules/ — domain modules (auth, users, characters, chats, messages, providers, etc.)

## Frontend Structure
- web/src/api/ — typed API client + SSE helpers
- web/src/pages/ — route-level page components
- web/src/components/ — shared UI components

## Current Sprint: Sprint A — Foundation Build
Building from scratch: Stage 0 + Module 1 + Module 2 with hardening baked in.

### Task Queue
1. [ ] Backend skeleton: FastAPI app with lifespan, config, logging, error middleware, health endpoints
2. [ ] Database setup: async SQLAlchemy + Alembic + connection pooling with retries
3. [ ] Auth module: register, login, logout, me (httpOnly cookies, JWT or session-based)
4. [ ] Users module: profile, provider key storage (encrypted)
5. [ ] Characters module: full CRUD with soft delete
6. [ ] Chats module: CRUD with character association
7. [ ] Messages module: store/list with pagination
8. [ ] Provider abstraction: adapter pattern with unified request/response
9. [ ] Anthropic provider: streaming + non-streaming generation
10. [ ] SSE streaming: proper streaming with no buffering issues
11. [ ] Frontend: React app scaffold with routing, auth pages, basic layout
12. [ ] Frontend: character list + creation
13. [ ] Frontend: chat view with message list + composer
14. [ ] Frontend: SSE streaming integration for live generation
15. [ ] Integration testing: full smoke test flow

### Completed
- [x] Project scaffold and GitHub repo created

### Blockers
- (none)

### Session Log
- 2026-02-22: Bootstrap started. Repo created, scaffold built, starting Sprint A.
