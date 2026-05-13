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

### Three-Tier Image Generation
Image generation is **gated**, not produced on every turn. See `docs/plans/PLAN_image_generation_architecture.md` for the full design.

- **Tier 1 — text only** (default, ~70% of turns): no image.
- **Tier 2 — single character** (~25%): existing flow in `backend/app/modules/images/` (provider chain Runware→Wavespeed→FAL→Together, LoRA matching, prompt builder). Endpoint: `POST /api/v1/images/generate`.
- **Tier 3 — cached composite** (~5%): cached backdrops + transparent character poses composited at runtime with a low-strength img2img lighting unification pass. Endpoint: `POST /api/v1/images/composite`.
- **HQ async path**: `POST /api/v1/images/generate_hq` — placeholder for future `MultiCharPipeline` sequential-inpainting port.

Tier selection is driven by `[SCENE_BEAT:single|multi|none]` markers emitted by the story LLM and stripped before user-visible storage; user pref `image_pref` (`auto`/`always`/`never`) overrides. Heuristic fallback in `backend/app/modules/images/tier_router.py`.

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
1. [x] Backend skeleton: FastAPI app with lifespan, config, logging, error middleware, health endpoints
2. [x] Database setup: async SQLAlchemy + Alembic + connection pooling with retries
3. [x] Auth module: register, login, logout, me (httpOnly cookies, JWT or session-based)
4. [x] Users module: profile, provider key storage (encrypted)
5. [x] Characters module: full CRUD with soft delete
6. [x] Chats module: CRUD with character association
7. [x] Messages module: store/list with pagination + message_type enum (user/character/dm)
8. [x] Provider abstraction: adapter pattern with unified request/response
9. [x] Anthropic provider: streaming + non-streaming generation
10. [x] SSE streaming: proper streaming with no buffering issues
11. [x] Frontend: React app scaffold with routing, auth pages, basic layout
12. [x] Frontend: character list + creation
13. [x] Frontend: chat view with message list + composer
14. [x] Frontend: SSE streaming integration for live generation
15. [ ] Integration testing: full smoke test flow
16. [x] Dungeon Master AI module: scaffold (models, schemas, dice, tools, validation, service, router)
17. [x] Dungeon Master AI module: Alembic migration (game_rulesets, dm_sessions, character_sheets, dm_rolls, dm_actions)
18. [x] Dungeon Master AI module: wire DM evaluation into chat message flow (pre-story-AI hook)
19. [x] Dungeon Master AI module: auth wiring (user_id on rulesets/sessions via get_current_user)
20. [x] Frontend: integrate DMPanel into chat layout
21. [x] Frontend: render DMMessage events from SSE stream

### Module 8 — Dungeon Master Full Gatekeeper Upgrade (complete)
22. [x] Alembic migration 002: enforcement_config/reminder_text/instruction/player_guide/dm_temperature/dm_max_tokens/context_window on game_rulesets; is_alive on character_sheets; dc/success/advantage/disadvantage/stat_used on dm_rolls; new game_events table; new dm_config_attachments table
23. [x] Backend models: GameRuleset + CharacterSheet + DMRoll new columns; GameEvent + DMConfigAttachment new models
24. [x] Backend schemas: EnforcementConfig, GameEventResponse, AttachmentResponse, RollStatsResponse, updated DMEvalResult (narrative_context, game_event_id)
25. [x] pre_validator.py: deterministic Python pre-validation (dead character, conditions, ability claims, item claims, resource availability) — hard rejects skip LLM
26. [x] providers/pipeline.py: PreGenerationInterceptor ABC + GenerationPipeline interface
27. [x] service.py: build_narrative_context(), three-block prompt caching (reminder_text), context_window, is_alive permadeath, GameEvent write, timing + token counts
28. [x] tools.py: stat description field support
29. [x] dm router: duplicate ruleset, public rulesets, attachments CRUD, game events, roll stats, answer endpoint
30. [x] chats router: PreValidator wired, narrative_context injected into story AI system prompt
31. [x] Frontend: DMConfigEditorPage (5-tab editor, stat schema builder, enforcement toggles, Classic d20 template)
32. [x] Frontend: RollBar component (roll outcomes + stat diffs below assistant messages)
33. [x] Frontend: dmStore — isAlive, pendingEventId, answerQuestion, fixed snake_case mapping in dm_done handler
34. [x] Frontend: dm.ts — new types (EnforcementConfig, GameEventResponse, AttachmentResponse, RollStatsResponse) + all new API methods
35. [x] Frontend: ChatPage — RollBar below messages, DM ask-player answer input, DM Settings link in header
36. [x] Frontend: CharacterSheet — is_alive FALLEN badge, bar_color from stat schema via inline style
37. [x] Frontend: App.tsx — routes /dm/configs/new and /dm/configs/:configId/edit

### Dungeon Master AI Architecture
Separate AI agent (runs before story AI) handling game mechanics. Design inspired by Isekai Zero's "Dungeon Mind" — improved with server-side programmatic validation, arbitrary dice systems, and PostgreSQL-backed transactional stat tracking.

Key design decisions:
- DM runs FIRST — can reject/pause before story AI fires
- Stripped context: last 10 msgs + character sheet + rules reminder (appended last for recency bias)
- Schema-driven dynamic tools: stat_schema → Anthropic tool definitions generated at runtime
- Base + modifier delta pattern: deltas accumulate, 0 = reset to initial_value
- Programmatic validation before DB write: clamping, death state detection, unknown stat rejection
- DM runs on cheap model (Haiku default) — set DM_DEFAULT_MODEL or ruleset.recommended_model
- SSE event types: dm_thinking, dm_roll, dm_stat_update, dm_inventory_update, dm_skill_update, dm_reject, dm_ask, dm_done

New env vars:
- ANTHROPIC_API_KEY — required for DM agent
- DM_DEFAULT_MODEL — default DM model (default: claude-haiku-4-5-20251001)

New module: backend/app/modules/dungeon_master/
- models.py — GameRuleset, DMSession, CharacterSheet, DMRoll, DMAction
- schemas.py — Pydantic request/response types
- dice.py — server-side dice engine (d4–d100, FATE, keep-highest/lowest)
- tools.py — dynamic Anthropic tool generation from stat schema
- validation.py — stat delta validation, clamping, death state detection
- service.py — agentic evaluation loop with SSE emission + prompt caching
- router.py — /api/v1/dm/* endpoints

New frontend:
- web/src/store/dmStore.ts — Zustand store for DM state + SSE event handler
- web/src/api/dm.ts — typed DM API client + evaluateDMAction SSE helper
- web/src/components/DMPanel/ — CharacterSheet, DiceRoller, RollHistory, index
- web/src/components/chat/DMMessage.tsx — DM event chat bubbles
- web/src/components/chat/RollBar.tsx — roll outcomes + stat diffs below assistant messages
- web/src/pages/DMConfigEditorPage.tsx — full ruleset editor with stat schema builder

## Sprint B — Three-Tier Image Pipeline
Implements `docs/plans/PLAN_image_generation_architecture.md` (the new three-tier architecture). Phases 1–5; Phase 6 (video) deferred.

### Task Queue
1. [x] Phase 0: archive old v1 image plan, add new three-tier plan, update CLAUDE.md.
2. [x] Phase 1: Tier 2 hardening — `providers/chain.py` enforces per-provider `asyncio.wait_for(IMAGE_PROVIDER_TIMEOUT_S)` + magic-byte validation; `cache.py` deterministic Redis/in-memory cache (`IMAGE_REQUEST_CACHE_*`); `master_lora_dict.json` permanents pinned to `realism`. Outstanding sub-item: `data/runware_lora_mapping.json` mappings dict is empty (only matters when Runware-hosted shorthand IDs fall over to non-Runware providers — no-op today).
3. [x] Phase 2: Tier 3 foundation — migration `005_image_asset_tables.py`, ORM models in `images/models.py`, `images/background_removal.py` (rembg; BiRefNet stub), `images/storage.py` (LocalStorage + S3CompatibleStorage; boto3 commented in requirements), `config/backdrop_specs.yaml` + `config/pose_matrix.yaml`, `workers/backdrop_generator.py` + `workers/character_pose_generator.py`, list endpoints `/images/backdrops` + `/images/characters/{id}/poses`.
4. [x] Phase 3: Tier 3 hot path — `images/composer.py` (5 layouts), `images/scene_classifier.py`, `images/composite_service.py`, migration `006_image_job_kind.py`, `POST /api/v1/images/composite`, "Illustrate scene" button in `ChatPage.tsx`. Lighting img2img is best-effort (falls back to raw composite on provider failure).
5. [x] Phase 4: Tier routing — `images/tier_router.py` (`MarkerStreamStripper` + `select_tier`), migration `007_user_image_pref.py` (`users.image_pref` enum auto/always/never), story-LLM `SYSTEM_PROMPT_APPENDIX` injected, marker-driven dispatch in `chats/router.py` emitting `image_dispatched` SSE, frontend Auto/Always/Never toggle in `ChatPage.tsx:29-62`.
6. [ ] Phase 5: HQ endpoint — `POST /api/v1/images/generate_hq` + `ImageJob.job_kind="hq"` + `imagesApi.generateHq()` scaffolded; `run_hq_job()` is a stub that falls back to single-char generator. Remaining: port real `MultiCharPipeline` (sequential inpainting) from upstream https://github.com/Deathwalker-47/Silly-Tavern-Flux-Bridge, add `provider.generate_inpaint()` (Runware first), add `MaskGenerator`, surface "✨ HQ scene" button in `ChatPage.tsx`.

### Sprint A Task 15 — Integration testing (in progress)
- [ ] Backend e2e expansion in `tests/test_smoke_e2e.py`: marker-driven image dispatch, composite happy path, `image_pref=never` blocks dispatch.
- [ ] Vitest + React Testing Library setup; component tests for `ImageCard` SSE transitions, `ChatPage` image-pref toggle cycle, `streamImageJob` SSE parser.
- [ ] Playwright e2e in `web/tests/e2e/chat_image.spec.ts`: register → login → character → chat → marker-triggered image. Non-blocking CI workflow.

### Sprint B Polish (in scope)
- [ ] Backdrop/pose admin browse UI: `BackdropsPage.tsx`, `CharacterPosesPage.tsx` (read-only grids over existing `imagesApi.listBackdrops` / `imagesApi.listPoses`).

### Completed
- [x] Project scaffold and GitHub repo created
- [x] Dungeon Master AI module scaffold

### Blockers
- (none)

### Troubleshooting Reference
Read docs/bootstrap-troubleshooting.md for known environment issues and fixes. Update it when fixing new issues.

### Session Log
- 2026-02-22: Bootstrap started. Repo created, scaffold built, starting Sprint A.
- 2026-04-22: Dungeon Master AI module scaffolded. Full backend module + frontend components ready. Awaits DB migration after task 2 (database setup) is complete.
- 2026-05-04: Module 8 (Full Gatekeeper Game Engine) complete. Pre-validation layer, enforcement config, is_alive permadeath, game_events table, dm_config_attachments, context injector, pipeline interface, 8 new API endpoints, DMConfigEditorPage, RollBar component, answer input, DM settings link.
- 2026-05-13: Sprint B opened. Three-tier image generation architecture plan landed (`docs/plans/PLAN_image_generation_architecture.md`); v1 single-tier plan archived. Sprint B implements Phases 1–5: Tier 2 hardening, Tier 3 cached composites, `[SCENE_BEAT:*]` marker routing, HQ endpoint scaffolding.
- 2026-05-13: Sprint B Phases 1–4 audit confirmed shipped; task queue reconciled with code reality. Remaining work scoped to Phase 5 HQ MultiCharPipeline port + HQ button, Sprint A Task 15 full e2e closure (backend + Vitest + Playwright), and backdrop/pose admin browse UI. BiRefNet, Runware reverse mapping, and broader `image_pref=never` hardening explicitly deferred.
