# PLAN: Image Generation Architecture

## Goal
Build an inline, context-aware image generation system for Midnight Tavern that can:
- generate images during roleplay/chat flows,
- respect conversation context and character metadata,
- support multiple providers/models via a unified backend abstraction,
- store outputs and metadata for replay/audit,
- stream progress/status updates to the frontend.

## Scope

### In scope
- Backend module for image generation requests, provider selection, and job tracking.
- Prompt construction pipeline that combines user intent + chat context + character/ruleset hints.
- Async generation execution (queue workers) with persisted job states.
- Frontend API and UI hooks for requesting images and rendering generation status/results.
- Safety/moderation hooks aligned with project policy constraints.

### Out of scope (initial phase)
- Full training pipeline orchestration for user LoRAs.
- Advanced gallery/search UX.
- Multi-image editing workflows (inpainting/outpainting) beyond basic single-step generation.

## Architecture Overview

### 1) API Layer (FastAPI)
Add `/api/v1/images` endpoints:
- `POST /generate` — create generation job.
- `GET /jobs/{job_id}` — fetch current job status.
- `GET /jobs/{job_id}/events` — SSE status stream for progress updates.
- `POST /jobs/{job_id}/cancel` — cancel queued/running job when supported.

Request payload should include:
- `chat_id`, `character_id` (optional),
- user intent prompt,
- style/aspect/size params,
- safety level / moderation mode.

### 2) Service Layer
Create an `images` service pipeline:
1. Validate request + normalize parameters.
2. Build final provider prompt from context blocks.
3. Persist job record (`queued`).
4. Enqueue worker task.
5. Worker executes provider call, updates status (`running` → `completed|failed`).
6. Persist output references + metadata.
7. Emit SSE events for frontend.

### 3) Provider Abstraction
Introduce an adapter interface similar to text providers:
- `generate_image(request) -> provider_result`
- `cancel(job_ref)` (optional)

Adapters can target:
- hosted diffusion providers,
- internal inference gateway,
- future per-user model variants.

Store provider-agnostic metadata in DB and raw provider payload in optional debug JSON.

### 4) Data Model
Proposed tables:
- `image_jobs`
  - `id`, `user_id`, `chat_id`, `character_id`,
  - `status` (queued/running/completed/failed/canceled),
  - `prompt_user`, `prompt_system`, `negative_prompt`,
  - `provider`, `model`, `width`, `height`, `seed`, `steps`,
  - `error_code`, `error_message`,
  - `created_at`, `updated_at`, `completed_at`, `deleted_at`.
- `image_outputs`
  - `id`, `job_id`, `storage_url`, `thumbnail_url`,
  - `mime_type`, `bytes`, `sha256`,
  - `provider_asset_id`, `metadata_json`,
  - `created_at`.

Use soft deletes on user-facing resources, matching project conventions.

### 5) Storage
- Phase 1: local/dev storage abstraction + pluggable backend.
- Phase 2: S3-compatible storage (R2/S3) with signed URLs.
- Ensure deterministic object naming by `job_id` and output index.

### 6) Queue & Execution
Use Redis + ARQ worker flow:
- short API transaction path (queue only),
- background worker for long-running generation,
- retry policy for transient provider failures,
- timeout and cancellation handling.

### 7) Streaming & Frontend Integration
Event types for image generation SSE:
- `image_queued`,
- `image_started`,
- `image_progress`,
- `image_completed`,
- `image_failed`,
- `image_canceled`.

Frontend should:
- show pending/running status inline in chat,
- render final image card(s) with metadata,
- preserve timeline ordering with normal chat messages.

## Context Assembly Strategy
Build prompts from layered context blocks:
1. User explicit image request.
2. Recent chat window (last N messages, trimmed).
3. Character profile snippets (appearance/style tags).
4. Optional ruleset/world constraints.
5. Safety instruction suffix.

Keep a persisted `prompt_system` and `prompt_user` for reproducibility/debugging.

## Safety & Moderation
- Pre-generation moderation on user input/context.
- Provider-side safety flags where available.
- Post-generation moderation option for returned outputs.
- Blocklist/audit entries for rejected requests.

## Rollout Plan

### Milestone 1 — Skeleton
- Add images module scaffolding (router/service/schemas/models).
- Add DB migrations for `image_jobs` and `image_outputs`.
- Add dummy provider adapter + mocked job completion.

### Milestone 2 — Real Provider
- Implement first real provider adapter.
- Add queue worker + retries + timeout handling.
- Persist generated output metadata.

### Milestone 3 — Frontend Inline UX
- Add typed API client + SSE listener.
- Add chat UI cards for in-progress/completed images.
- Add retry/cancel affordances.

### Milestone 4 — Hardening
- Add integration tests for job lifecycle.
- Add cost/rate-limit controls.
- Add signed URL storage path and cleanup jobs.

## Acceptance Criteria
- A user can request an image from chat and receive status updates + final output without blocking API request threads.
- Jobs survive worker restarts with recoverable state.
- Generated assets are persisted and retrievable with metadata.
- Error responses and logging conform to project standards.
