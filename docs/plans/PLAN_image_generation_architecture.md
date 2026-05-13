# Midnight Tavern — Image Generation Architecture Plan

> **Note (2026-05-13):** This document supersedes the single-tier image generation plan now archived at `docs/plans/archive/PLAN_image_generation_architecture_v1_single_tier.md`. The v1 plan covered only what is now called Tier 2 (single-character generation via a provider chain) — that work is already implemented in `backend/app/modules/images/`. This document extends the system with Tier 1 (default no-image), Tier 3 (cached scene composition), an `[SCENE_BEAT:*]` marker convention to drive tier selection, and a repositioned async high-quality path.

---

## Adaptation for this repo

The plan below was originally drafted with `flux_lora_bridge.py` as a **separate** HTTP service. In `midnight-tavern`, the bridge's logic was absorbed directly into `backend/app/modules/images/`. Use this map when reading any "change in flux_lora_bridge.py" reference:

| Plan reference | Midnight Tavern equivalent |
|---|---|
| `flux_lora_bridge.py` | `backend/app/modules/images/` (providers, lora, prompt_builder, service, storage) |
| `POST /sdapi/v1/txt2img` | `POST /api/v1/images/generate` |
| `POST /sdapi/v1/txt2img_hq` | `POST /api/v1/images/generate_hq` |
| `POST /sdapi/v1/img2img` | `POST /api/v1/images/img2img` |
| `GET /sdapi/v1/jobs/{id}` | Existing `GET /api/v1/images/jobs/{id}` + `GET /api/v1/images/jobs/{id}/events` (SSE) |
| `master_lora_dict.json` | `backend/data/master_lora_dict.json` |
| `runware_lora_mapping.json` | `backend/data/runware_lora_mapping.json` |
| `silly-tavern-pluggin/index.js` marker handling | Chat module SSE pipeline — `backend/app/modules/chats/` |
| `MultiCharPipeline`, `MaskGenerator`, `LAYOUT_TEMPLATES` | **Not ported.** Phase 5 lands the async HQ endpoint shape so this can be ported in a future sprint. Layout templates for Tier 3 are inlined in `backend/app/modules/images/composer.py`. |
| ARQ workers | `backend/app/workers/` (ARQ functions) + `backend/app/workers/cli/` (standalone CLI entrypoints) |
| `backdrops`/`character_poses`/`scene_composites` tables | Live in this repo's Postgres via Alembic migration `005_add_image_asset_tables.py` (models in `backend/app/modules/images/models.py`) |
| Storage backend abstraction (§9.5) | `backend/app/modules/images/storage.py` — `LocalStorage` (dev) + `S3CompatibleStorage` (prod) selected by `STORAGE_BACKEND` env var |

Anything below this section is the plan as written.

---

**Status:** Proposed
**Owner:** Anujith
**Last updated:** 2026-04-25
**Audience:** Claude Code (primary), Anujith (reviewer)
**Supersedes:** Sequential multi-character inpainting approach in `flux_lora_bridge.py` `MultiCharPipeline`

---

## 0. How to read this doc

This plan is written to be picked up by Claude Code working directly in the repo. When starting a coding session, point Claude Code at this file with a task scoped to one section. The plan is intentionally verbose — each section is meant to give Claude Code enough context that it doesn't have to guess about adjacent systems.

**Sections you'll reference most:**
- §3 — Three-tier architecture (the core conceptual model)
- §4 — Tier 3 cached scene library design (the biggest new build)
- §5 — Tier 2 hardening (changes to the existing `flux_lora_bridge.py`)
- §8 — Phased task breakdown (concrete units of work)
- Appendix A — File map of changes
- Appendix B — Reusable task prompt patterns

**Deployment target is undecided** as of this writing. Options under consideration: Replit, Hetzner (where the MCP gateway already runs at `37.27.191.114`), or another VPS. The architecture is deployment-agnostic — code should not assume Replit-specific paths, build steps, or environment quirks. See §9 for deployment-specific notes when those decisions are made.

---

## 1. Executive summary

Midnight Tavern needs a sustainable image generation strategy that:

1. **Maintains identity fidelity** for characters (especially user-trained LoRAs like Nimya33)
2. **Stays under user-perceived latency budget** (~15s end-to-end per image, p95)
3. **Keeps unit economics viable** at session scale (50+ turns per session, N concurrent users)
4. **Handles 1-N character scenes** without identity bleed

The current `MultiCharPipeline` in `flux_lora_bridge.py` (sequential inpainting with `MaskGenerator` + back-to-front compositing) **achieves quality but fails on latency**. Realistic cost: 32-44s per multi-character image. This is a non-starter for production roleplay chat where users expect images alongside or shortly after LLM responses.

This plan replaces sequential inpainting **as the primary multi-character path** with a three-tier architecture:

- **Tier 1 (default):** No image. LLM response only.
- **Tier 2 (most images):** Single-character generation using existing flux bridge pipeline.
- **Tier 3 (key narrative beats):** Cached scene library + cached character poses, composited at runtime.

Sequential inpainting is **not deleted** — it's repositioned as a user-initiated "high quality" feature for asynchronous use cases (reroll button, premium tier, offline asset pre-generation).

This plan also covers the **video generation pipeline** (Flux still → I2V routing across WAN/Seedance/Kling) which is a related future scope, currently out-of-scope for initial build.

---

## 2. Why this plan exists (problem statement)

### 2.1 What we tried

`MultiCharPipeline` in `flux_lora_bridge.py` implements sequential inpainting:

- Pass 0: Generate background (no character LoRAs) on a fixed canvas (`Config.MULTI_CHAR_CANVAS_W` × `H`)
- Pass 1..N: For each character, generate a slot mask via `MaskGenerator.generate_slot_mask`, inpaint the character into that region with their specific LoRA + shared LoRAs
- Pass N+1: Optional harmonization pass over seam zones via `MaskGenerator.generate_seam_mask`

This works architecturally and produces high-quality multi-character images. It's the **right tool for offline pre-generation**, not for inline response generation.

### 2.2 Why it can't be the primary path

**Latency math (estimated against Runware):**

| Stage | Time |
|---|---|
| DeepSeek decompose call | 1.5-3s |
| Background generation (Pass 0) | 8-12s |
| Inpaint character A | 8-12s |
| Inpaint character B | 8-12s |
| Inpaint character C (if 3 chars) | 8-12s |
| Harmonization pass | 6-10s |
| **Total for 2 chars** | **~26-40s** |
| **Total for 3 chars** | **~32-44s** |

Anything past ~15s feels broken in conversational UI. Users abandon, complain, or generate without waiting. CAI / Janitor / Wyvern all ship single-character only — they're not stupid, they hit this wall and chose latency over visual richness.

**Cost math:** at Runware's pricing, 5 sequential generations × 50 turns × N concurrent users compounds quickly. Per-message cost roughly 2-3x a single-pass generation. Unsustainable at scale.

### 2.3 What we're keeping vs replacing

**Keeping (in `flux_lora_bridge.py`):**
- Provider chain (Runware → Wavespeed → FAL → Together) — works well
- DeepSeek V3 summarization — works well
- Keyword-based LoRA matching via `master_lora_dict.json` — works well
- Role caps (`Config.ROLE_CAPS`) — necessary
- LoRA upload/resolution mapping (`runware_lora_mapping.json`) — works well
- Single-character generation path — this becomes Tier 2

**Replacing as primary path:**
- `MultiCharPipeline.generate()` automatic invocation when ≥2 character LoRAs match
- The "if char_count >= 2 → run multi-char pipeline" branch in `txt2img` endpoint

**Repositioning, not deleting:**
- `MultiCharPipeline`, `MaskGenerator`, `LAYOUT_TEMPLATES` — moved behind explicit user opt-in (new `/sdapi/v1/txt2img_hq` async endpoint, and asset pre-build jobs)

---

## 3. Three-tier architecture

### 3.1 Tier definitions

```
┌─────────────────────────────────────────────────────────────────┐
│ Tier 1 — Text only                                              │
│   When: 70%+ of LLM messages                                    │
│   Latency: 0ms (no image generated)                             │
│   Trigger: Default state                                        │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Tier 2 — Single character (existing flow)                       │
│   When: ~25% of messages — scene change, new character intro,   │
│         significant action, location shift                      │
│   Latency: 8-12s                                                │
│   Trigger: LLM emits [SCENE_BEAT:single] OR plugin heuristic    │
│   Pipeline: Existing single-character flow in flux_lora_bridge  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Tier 3 — Cached composite (new)                                 │
│   When: ~5% of messages — multi-character key moments           │
│   Latency: 5-12s (cache hit) | 25-40s (cache miss, async build) │
│   Trigger: LLM emits [SCENE_BEAT:multi] AND ≥2 chars present    │
│   Pipeline: Cached scene + cached character poses + composite + │
│             lighting unification pass                           │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Trigger logic

Image generation is **gated** — not every LLM turn produces an image. Triggers are:

**A. LLM-emitted markers (preferred):**
The Midnight Tavern system prompt instructs the LLM to emit special markers when image generation is appropriate:

```
[SCENE_BEAT:single]   → Tier 2, generate image of speaking character
[SCENE_BEAT:multi]    → Tier 3, generate composite scene
[SCENE_BEAT:none]     → Explicit no-image (silent moment, dialogue only)
```

If no marker is emitted, default is Tier 1 (no image).

**B. User-initiated:**
- "Illustrate this scene" button on any message → generates Tier 3 if 2+ chars in scene, else Tier 2
- "Reroll image" button → regenerates same tier with different seed

**C. Plugin heuristics (fallback):**
If LLM doesn't emit markers (older models, custom backends), fall back to lightweight heuristics:
- New character name introduced in this message → Tier 2
- Location/scene change keywords ("they walk into", "later that night", "the next morning") → Tier 2
- Otherwise Tier 1

**D. Explicit user toggles:**
- "Always generate image" mode → forces Tier 2 on every turn (premium tier, opt-in)
- "Image-free mode" → force Tier 1 always

---

## 4. Tier 3: Cached scene library design (new system)

### 4.1 Conceptual model

Treat this like a visual novel asset pipeline updated for AI generation:

- **Backdrops:** Generated once, stored, reused. ~50-200 backdrops covering common locations (tavern interior, bedroom, forest path, throne room, etc.). No characters. High quality.
- **Character poses:** Per character LoRA, generate a cached library of poses against a transparent/neutral background. ~100-300 poses per character (standing, sitting, kneeling, leaning, reaching, fighting stance, etc.). Cached forever (until LoRA retrained).
- **Composite at runtime:** Pick backdrop + N character poses + composite + run a fast lighting unification img2img pass at low denoise strength.

This is conceptually similar to traditional visual novel sprite systems, except sprites are AI-generated per character LoRA and backdrops are AI-generated per scene type.

### 4.2 Database schema additions

Postgres tables to add (in the Midnight Tavern repo, not flux_lora_bridge):

```sql
-- Backdrops generated and cached
CREATE TABLE backdrops (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT UNIQUE NOT NULL,                    -- 'tavern_interior_warm', 'forest_path_dusk'
    description TEXT NOT NULL,                    -- prompt used to generate
    tags TEXT[] NOT NULL,                         -- ['indoor', 'tavern', 'warm-light']
    width INT NOT NULL DEFAULT 1536,
    height INT NOT NULL DEFAULT 1024,
    storage_url TEXT NOT NULL,                    -- object storage path
    lighting_profile JSONB NOT NULL,              -- {direction:'left', warmth:'warm', intensity:'soft'}
    aspect_ratio TEXT NOT NULL,                   -- '3:2', '16:9', etc.
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    generation_meta JSONB                         -- seed, model, loras used
);

CREATE INDEX idx_backdrops_tags ON backdrops USING GIN(tags);
CREATE INDEX idx_backdrops_lighting ON backdrops USING GIN(lighting_profile);

-- Character pose library (linked to character LoRAs)
CREATE TABLE character_poses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    lora_version TEXT NOT NULL,                   -- invalidate when LoRA retrained
    pose_slug TEXT NOT NULL,                      -- 'standing_neutral', 'sitting_relaxed'
    expression_slug TEXT NOT NULL,                -- 'neutral', 'smiling', 'angry'
    framing TEXT NOT NULL,                        -- 'full_body', 'upper_body', 'waist_up'
    facing TEXT NOT NULL,                         -- 'forward', 'three_quarter_left', 'profile_right'
    transparent_storage_url TEXT NOT NULL,        -- background-removed PNG
    raw_storage_url TEXT,                         -- original generation, debug only
    width INT NOT NULL,
    height INT NOT NULL,
    bounding_box JSONB NOT NULL,                  -- {x, y, w, h} of subject within image
    lighting_profile JSONB NOT NULL,              -- matches backdrop schema
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    generation_meta JSONB,
    UNIQUE(character_id, lora_version, pose_slug, expression_slug, facing)
);

CREATE INDEX idx_poses_character ON character_poses(character_id);
CREATE INDEX idx_poses_lookup ON character_poses(character_id, lora_version, pose_slug, expression_slug);

-- Final composite cache (for repeat scene requests)
CREATE TABLE scene_composites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_hash TEXT UNIQUE NOT NULL,              -- hash of (backdrop_id, [pose_ids], layout)
    backdrop_id UUID NOT NULL REFERENCES backdrops(id),
    pose_ids UUID[] NOT NULL,
    layout_template TEXT NOT NULL,                -- 'side_by_side', 'triangle', etc.
    storage_url TEXT NOT NULL,
    composite_meta JSONB,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_composites_hash ON scene_composites(scene_hash);
CREATE INDEX idx_composites_lru ON scene_composites(last_used_at);
```

**Note on `lora_version`:** Critical for cache invalidation. When a user retrains their character LoRA, all `character_poses` rows with the old version become stale. The pose generation worker should bump this version and regenerate, while keeping old rows until cleanup.

### 4.3 Asset pre-generation pipeline (offline)

This runs as a background worker, not in the request hot path. Two options for execution model:

**Option A — In-process FastAPI background tasks** (simpler):
- Use `BackgroundTasks` from FastAPI
- Worker runs in the same process as the API server
- Good for low-volume pre-generation (testing, single user)
- Risk: long-running jobs can starve the API process

**Option B — Separate worker process** (production):
- Standalone Python script: `python -m backend.workers.backdrop_generator`
- Reads from a job queue (Postgres-backed `pending_jobs` table, or RQ/Celery if scale demands)
- Can run on its own machine separate from API server
- Better resource isolation

Start with Option A for Phase 2, migrate to Option B if/when concurrent users force it. Worker code structure is the same in either case — the difference is invocation.

**Backdrop generation:**

Worker pulls from a curated list (`backdrop_specs.yaml`):

```yaml
backdrops:
  - slug: tavern_interior_warm
    prompt: "warm cozy tavern interior, wooden beams, fireplace glow, empty space in foreground for characters, painterly cinematic lighting"
    tags: [indoor, tavern, warm-light, fantasy]
    lighting_profile:
      direction: left
      warmth: warm
      intensity: soft
    aspect_ratio: '3:2'

  - slug: forest_path_dusk
    prompt: "moss-covered forest path at dusk, golden hour light filtering through trees, atmospheric, empty foreground space"
    tags: [outdoor, forest, golden-hour, fantasy]
    lighting_profile:
      direction: right
      warmth: warm
      intensity: medium
    aspect_ratio: '3:2'
  # ... 30-50 more
```

Worker calls `flux_lora_bridge.py` `/sdapi/v1/txt2img` with no character LoRAs, stores result in object storage, indexes in Postgres. Run once per backdrop, regenerate only if specs change.

**Character pose generation:**

When a character LoRA is added/trained, trigger a job that generates a pose matrix per character:

```yaml
pose_matrix:
  poses:
    - slug: standing_neutral
      prompt_fragment: "{trigger}, standing relaxed, arms at sides, looking forward"
      framings: [full_body, upper_body]
      facings: [forward, three_quarter_left, three_quarter_right]
    - slug: sitting_relaxed
      prompt_fragment: "{trigger}, sitting on a chair, hands resting in lap"
      framings: [waist_up]
      facings: [forward, three_quarter_left]
    - slug: leaning_against_wall
      prompt_fragment: "{trigger}, leaning casually against a wall, one shoulder forward"
      framings: [full_body, upper_body]
      facings: [three_quarter_left, three_quarter_right]
    # ... 15-20 more poses
  expressions:
    - slug: neutral
      prompt_fragment: "calm neutral expression"
    - slug: smiling
      prompt_fragment: "warm gentle smile"
    - slug: angry
      prompt_fragment: "furrowed brow, tight lips, intense gaze"
    - slug: surprised
      prompt_fragment: "wide eyes, slightly open mouth"
    # ... 5-7 more expressions
```

For each (pose × expression × facing) combination, generate against a neutral grey backdrop using the character's LoRA + the pose prompt, run background removal (rembg or BiRefNet), store transparent PNG. Index bounding box of the subject.

**Math:** ~20 poses × 5 expressions × 3 facings = 300 images per character. At ~10s each via Runware, ~50 minutes wall-clock per character. Cost ~$1-2 per character via Runware credits. One-time cost per LoRA version.

**Important caveat:** Pre-generated poses lock in the character's appearance under specific lighting (the neutral grey backdrop's flat lighting). When composited onto a backdrop with directional warm light, the character will look "stuck on" unless the lighting unification pass works well. See §4.4 step 5 — this is the main quality risk for Tier 3.

### 4.4 Runtime composition (Tier 3 hot path)

```
Request arrives with: scene description + character list
  ↓
1. Pick backdrop:
   - LLM (lightweight model, cached prompt) classifies scene → tag set
   - Postgres query: backdrops matching tag set, ranked by tag overlap
   - Pick top result; if multiple equally good, randomize for variety
  ↓
2. Pick layout:
   - Use existing LAYOUT_TEMPLATES from flux_lora_bridge.py
   - 2 chars → side_by_side, 3 chars → triangle, etc.
  ↓
3. Pick pose for each character:
   - LLM (lightweight) reads scene description, suggests pose+expression per char
   - Postgres query: character_poses matching (character_id, lora_version, pose_slug, expression_slug)
   - Fall back: closest pose by slug similarity if exact match missing
  ↓
4. Composite (PIL):
   - Load backdrop image
   - For each character (back-to-front per layout z-order):
     - Load transparent pose PNG
     - Resize to layout slot dimensions (preserving aspect ratio)
     - Place at slot center coordinates
     - Alpha blend over backdrop
   - Result: composite_v0.png
  ↓
5. Lighting unification (img2img pass):
   - Send composite_v0.png to flux_lora_bridge as init image
   - Strength: 0.20-0.25 (very low — preserve composition, only unify lighting)
   - Steps: 15
   - Prompt: "{scene description}, unified cohesive lighting, {backdrop lighting_profile}, photorealistic"
   - Result: composite_final.png
  ↓
6. Cache composite (scene_composites table):
   - Hash = SHA256(backdrop_id + sorted pose_ids + layout)
   - On cache hit next time: return immediately
   - LRU eviction at storage cap
  ↓
7. Return final image to user
```

**Latency profile:**
- LLM scene classification: 800ms-2s (use a fast model — Claude Haiku, DeepSeek-V3, or even a small local model)
- Postgres queries: <100ms
- Image composition (PIL): 200-500ms
- Lighting unification img2img pass: 6-10s (this is the dominant cost)
- **Total: 7-13s typical cache miss**

Cache hits drop to ~500ms (just the storage fetch).

**Risk areas worth flagging up front:**

1. **Lighting mismatch.** Characters were generated against flat grey lighting. Compositing them onto a warm-side-lit tavern interior creates a visual disconnect. The lighting unification img2img pass is supposed to fix this, but at strength 0.20-0.25 it may not be strong enough. If it's too weak, we'll see "obviously composited" output. If we crank it up to 0.40+, we lose identity. Tuning needed — start at 0.25 and adjust based on output.

2. **Edge artifacts from background removal.** rembg/BiRefNet aren't perfect. Hair edges especially are tricky. The unification pass partially fixes this by blending edges, but severe artifacts will still show.

3. **Perspective inconsistency.** Pre-generated character poses have a fixed camera angle. If the chosen backdrop has a different perspective (low angle vs eye-level), the composite looks wrong. Mitigate by tagging both backdrops and poses with perspective, and only compositing matched pairs. Add `camera_angle` to both schemas if Phase 3 testing shows this is a real problem.

4. **Resolution mismatch.** Backdrops are 1536x1024, poses might be 1024x1024 from generation. Compositing requires careful resize logic that preserves character body proportions. The `bounding_box` field in `character_poses` helps target the right scale.

These aren't reasons to abandon the approach — they're tuning challenges. But Phase 3 should plan for at least one iteration of "the composites look obviously fake, fix the unification pass."

### 4.5 What goes in `flux_lora_bridge.py` vs Midnight Tavern backend

**Stays in `flux_lora_bridge.py`:**
- Single-character generation (`/sdapi/v1/txt2img` continues to work as-is for Tier 2)
- Provider chain
- LoRA matching
- The img2img endpoint for lighting unification (add new endpoint `/sdapi/v1/img2img` if not present)
- The existing `MultiCharPipeline` (move behind a new endpoint, see §6)

**Goes in Midnight Tavern backend (new):**
- `backdrops`, `character_poses`, `scene_composites` tables and CRUD
- Asset pre-generation worker code
- Tier 3 composite endpoint (new): `POST /api/v1/images/composite`
- LLM-based scene/pose classifiers
- PIL composition logic
- Cache lookup logic

This separation keeps the bridge focused on its current job (multi-provider Flux LoRA routing) and puts the application-layer logic in the app where it belongs. It also lets the bridge stay reusable for SillyTavern + Midnight Tavern + any other consumer.

---

## 5. Tier 2 hardening (existing flow improvements)

While Tier 3 is the new build, Tier 2 (existing single-character path in `flux_lora_bridge.py`) needs hardening for production:

### 5.1 Issues to fix

1. **Permanent LoRAs can dominate.** With Realism + Imagination + Detail Enhancer + Detailed Hands + Indian Style Face + NSFW Master Mystic + NSFW Busts all set as `permanent: true` in `master_lora_dict.json`, the role-cap logic still includes them and consumes the entire `general` cap (which is 2) and `nsfw` cap (which is 4). Audit and reduce permanent LoRAs to 1 (Realism only).

2. **`provider_based_lora_url_pruning` is fragile.** It silently drops `:` + `@` formatted LoRAs (like `deathwalker:xxx@1`) for non-Runware providers, but never re-adds them via the Runware mapping. Result: when Runware fails and we fall back to Wavespeed, characters using Runware-hosted IDs get silently dropped. Need explicit handling: either use the original URL from `runware_lora_mapping.json` reverse lookup, or skip with a clear log message so we know identity will be missing in the fallback.

3. **No structured timeout per provider.** `httpx.AsyncClient(timeout=120.0)` is set, but the polling loops in Wavespeed/FAL can run beyond that for queued jobs. Set per-provider total budget, not just per-request. If a provider hasn't returned an image in 60s including polling, fall over to next.

4. **Image validation is post-hoc.** `_validate_image_bytes` runs after retrieval, but doesn't trigger fallback if the bytes are invalid (HTML error page from a CDN 403, etc.). The Together AI 403 bug surfaced in `tests/test_image_delivery.py` is a real production risk. Wire up: if validation fails, treat as provider failure and fall through to next provider.

5. **No request-level cache.** Same prompt + same seed + same LoRAs should hash and return cached image. Adding a cache here at the bridge level saves credits and dramatically improves response times for repeated scenes. Use Redis if available, fall back to in-memory dict for dev.

### 5.2 Concrete changes to `flux_lora_bridge.py`

```python
# 1. Add image validation that triggers fallback
async def _generate_with_validation(client, prompt, neg, loras, params, provider_name):
    try:
        image_bytes = await client.generate(prompt, neg, loras, params)
        _validate_image_bytes(image_bytes, provider_name)  # raises on invalid
        return image_bytes
    except Exception as e:
        logger.error(f"❌ [{provider_name}] Generation or validation failed: {e}")
        raise  # re-raise so the provider loop falls through to next

# Replace the inline `client.generate` call in txt2img with this wrapper.

# 2. Add request-level cache
import hashlib

def _request_cache_key(prompt, neg, loras, params):
    sig = json.dumps({
        'p': prompt,
        'n': neg,
        'l': sorted([(l.get('url'), l.get('weight')) for l in loras]),
        'params': {k: params.get(k) for k in ['steps', 'cfg_scale', 'width', 'height', 'seed']}
    }, sort_keys=True)
    return f"flux_bridge:img:{hashlib.sha256(sig.encode()).hexdigest()[:32]}"

# Skip cache when seed == -1 (random); otherwise check cache before generation,
# write to cache on success with TTL ~7 days.
# Cache backend: Redis if REDIS_URL set, else in-memory dict (dev only).

# 3. Audit role caps: drop permanent LoRA count to 1
# In master_lora_dict.json, set permanent: false for:
#   imagination, detail_enhancer, detailed_hands, indian_style_face,
#   nsfw_master_mystic, nsfw_busts
# Keep only `realism` permanent. Update the relevant `notes` fields to remove
# "ALWAYS ACTIVE" phrasing where it no longer applies.

# 4. Fix provider pruning fallback
# In provider_based_lora_url_pruning, when dropping a deathwalker:xxx@1 LoRA
# for non-Runware providers, attempt to find the original URL via reverse lookup
# in runware_lora_mapping.json. If found, substitute the URL. If not, log a
# clear warning that this character will be missing from the fallback provider.

# 5. Per-provider timeout enforcement
# Wrap each provider attempt in asyncio.wait_for(coro, timeout=60). On timeout,
# log and continue to next provider in the chain.
```

### 5.3 Improvements to the SillyTavern plugin (`silly-tavern-pluggin/index.js`)

The plugin also feeds Midnight Tavern's prototype, so improvements here carry over:

1. **Detect `[SCENE_BEAT:*]` markers** in the LLM response and route accordingly:
   - `[SCENE_BEAT:single]` → call current bridge endpoint
   - `[SCENE_BEAT:multi]` → call new Tier 3 composite endpoint (when implemented)
   - `[SCENE_BEAT:none]` → skip image
   - No marker → existing heuristic (always generate)

2. **Strip `[SCENE_BEAT:*]` markers from displayed text** so they don't appear in chat.

3. **Add a config flag** `respect_scene_beat_markers: bool = false` (default false for backward compat) to gate this behavior.

---

## 6. Repositioning sequential inpainting (`MultiCharPipeline`)

The pipeline isn't deleted — it's moved out of the hot path.

### 6.1 New endpoint: `POST /sdapi/v1/txt2img_hq`

Same request schema as `/sdapi/v1/txt2img`, but:

- Always uses `MultiCharPipeline` if ≥2 character LoRAs match
- Returns immediately with a `job_id` (202 Accepted), polling via `GET /sdapi/v1/jobs/{job_id}`
- Documents 30-60s latency expectation

This becomes the backend for:
- "Illustrate this scene in HQ" button (premium tier, explicit user opt-in)
- The asset pre-generation worker (when generating cached scene composites that need character interaction beyond what cached poses can express, e.g. hugging, fighting embrace)

### 6.2 Code changes

In `flux_lora_bridge.py`:

```python
# 1. Remove auto-invocation of MultiCharPipeline from /sdapi/v1/txt2img
#    Currently around line ~1700:
#      if Config.MULTI_CHAR_ENABLED:
#          character_loras = [m for m in matched_loras if ...]
#          if len(character_loras) >= 2:
#              ...
#    DELETE this block. Keep MultiCharPipeline class intact.

# 2. Add new endpoint
JOBS: Dict[str, Dict] = {}  # in-memory for dev, Redis-backed in prod

@app.post("/sdapi/v1/txt2img_hq")
async def txt2img_hq(request: Txt2ImgRequest, background_tasks: BackgroundTasks):
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "queued", "result": None, "error": None, "created_at": time.time()}
    background_tasks.add_task(_run_hq_job, job_id, request)
    return {"job_id": job_id, "status": "queued"}

@app.get("/sdapi/v1/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "Job not found")
    return JOBS[job_id]

async def _run_hq_job(job_id: str, request: Txt2ImgRequest):
    JOBS[job_id]["status"] = "running"
    try:
        # ... existing multi-char pipeline invocation ...
        # (extract from current txt2img endpoint)
        JOBS[job_id]["status"] = "complete"
        JOBS[job_id]["result"] = base64_image
        JOBS[job_id]["completed_at"] = time.time()
    except Exception as e:
        logger.error(f"HQ job {job_id} failed: {e}")
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = str(e)
```

For dev, in-memory dict is fine. For deployed runs, switch to Redis or Postgres-backed jobs table so jobs survive restarts.

### 6.3 Where `MultiCharPipeline` gets used in Tier 3

Almost never. The cached pose library handles 95%+ of multi-character needs. `MultiCharPipeline` gets invoked only when:
- User explicitly requests HQ mode
- Asset worker generates a "rare interaction" pose (kissing, hugging, fighting embrace) that can't be composited from independent poses
- Future: pre-generation jobs that need true character interaction baked in

---

## 7. Video generation pipeline (separate scope, plan ahead)

**Out of scope for initial Tier 1-3 build.** Documented here so we don't lose the thinking; build later.

### 7.1 Architecture

```
User triggers video generation (button on a Tier 2 or Tier 3 image)
  ↓
Flux composition (already exists from image gen):
  - Single character: Tier 2 output is the I2V reference still
  - Multi character: Tier 3 composite is the I2V reference still
  ↓
LLM classifies motion intent:
  - 'subtle' (talking, looking, breathing) → Seedance route
  - 'moderate' (gestures, walking, turning) → WAN route
  - 'dynamic' (action, fast motion) → Kling route (when access available)
  ↓
Provider-specific I2V call:
  - Seedance: tight identity, weak prompt adherence, 2-5s clips
  - WAN 2.6: balanced, with user's WAN LoRA at 0.7-0.8
  - Kling 2.x: strongest motion, moderate identity
  ↓
Stitch (frontend):
  - Multiple short clips edited together
  - Each cut = identity reset point
```

### 7.2 Why route by motion intent

Direct tradeoff baked into each model:

| Model | Identity | Prompt adherence | Motion quality | Best for |
|---|---|---|---|---|
| Seedance | High | Low | Subtle | Talking heads, reactions |
| WAN 2.6 | Medium | High | Medium | Walking, gestures |
| Kling 2.x | Medium-High | Medium | High | Action, dynamic shots |

Use each for what it's good at. Don't try to make one model handle all cases.

### 7.3 Configuration

Add `video_loras.json`:

```json
{
  "wan_loras": {
    "nimya_wan_2_2": {
      "high_noise_url": "...",
      "low_noise_url": "...",
      "default_weights": {"high": 0.8, "low": 0.85},
      "char_keyword": "nimya"
    }
  }
}
```

### 7.4 Phase ordering

Don't build video pipeline until image Tier 1-3 is shipped and stable. Video is a "delight" feature, image is core UX. Drift mitigation strategies (short clips, motion magnitude limits, multi-cut editing) need their own dedicated planning doc when video work begins.

---

## 8. Implementation phases

Phases are work units, not calendar weeks. Pace depends on available time.

### Phase 1 — Hardening (`flux_lora_bridge.py` only)
**Goal:** Production-stable Tier 2 (single character). Touches the existing bridge repo only.

Tasks:
1. Audit `master_lora_dict.json` permanent LoRA count, drop to `realism` only. Update notes.
2. Add `_generate_with_validation` wrapper in `flux_lora_bridge.py` — wraps each provider's `generate()` call, runs `_validate_image_bytes`, re-raises on failure for fallback.
3. Add request-level cache (Redis if `REDIS_URL` env set, in-memory dict otherwise). Skip cache when `seed == -1`. TTL 7 days.
4. Fix `provider_based_lora_url_pruning` to use reverse lookup in `runware_lora_mapping.json` for fallback providers.
5. Add per-provider timeout enforcement via `asyncio.wait_for(..., timeout=60)`.
6. Update `silly-tavern-pluggin/index.js` to detect `[SCENE_BEAT:*]` markers (behind config flag `respect_scene_beat_markers`). Strip from display text.
7. Add tests in `tests/`:
   - `test_validation_triggers_fallback.py` — invalid bytes from provider A causes attempt at provider B
   - `test_request_cache.py` — same prompt+seed returns cached image
   - `test_role_caps_edge_cases.py` — verify cap behavior with new permanent LoRA count
   - `test_provider_pruning_fallback.py` — deathwalker LoRA gets URL substitution for non-Runware

**Acceptance:** Tier 2 generation completes in <12s p95, fails over cleanly across all 4 providers, returns valid images 99%+ of the time. All existing tests still pass.

### Phase 2 — Tier 3 foundation (Midnight Tavern backend)
**Goal:** Postgres schema, asset pre-generation worker, initial backdrop library.

Tasks:
1. Create migration files for `backdrops`, `character_poses`, `scene_composites` tables.
2. Create `backend/config/backdrop_specs.yaml` with 30-50 initial backdrop specs.
3. Build worker: `backend/app/workers/backdrop_generator.py`. Reads spec file, generates each backdrop via flux_lora_bridge, stores in object storage, indexes in Postgres. Idempotent (skip if slug already exists unless `--regenerate` flag).
4. Create `backend/config/pose_matrix.yaml` with 20 poses × 5 expressions × 3 facings = 300 entries.
5. Build worker: `backend/app/workers/character_pose_generator.py`. Takes a `character_id`, reads matrix, generates each pose, runs background removal, stores transparent PNG, indexes.
6. Build `backend/app/services/background_removal.py` — wraps rembg (default) or BiRefNet (opt-in via env var). Returns bytes + bounding box.
7. Build CRUD endpoints:
   - `GET /api/v1/backdrops` (list, filter by tags)
   - `GET /api/v1/characters/{id}/poses` (list, filter by slug/expression)
8. Implement storage backend abstraction (see §9.5) supporting local FS (dev) and S3-compatible (prod).

**Acceptance:** 30+ backdrops generated and queryable. 1 character (Nimya) has 200+ poses indexed. Background removal cleanly extracts character from neutral backdrop.

### Phase 3 — Tier 3 hot path
**Goal:** Runtime composite generation under latency budget.

Tasks:
1. Add `/sdapi/v1/img2img` endpoint to `flux_lora_bridge.py` if not present — accepts init image, returns img2img result.
2. Build `backend/app/services/scene_classifier.py` — uses fast LLM (DeepSeek-V3 via Together, or Claude Haiku) to classify scene description into backdrop tags + per-character pose recommendations.
3. Build `backend/app/services/composer.py` — PIL-based composition logic. Takes backdrop URL + list of (pose_url, slot_coords) → composite PNG.
4. Build `backend/app/api/v1/images.py` — `POST /api/v1/images/composite` endpoint:
   - Inputs: scene_description, character_ids list
   - Routes through: classifier → backdrop pick → pose pick → composer → unification img2img call → cache write → return
5. `scene_composites` cache lookup before regeneration (hash-based).
6. Frontend: "Illustrate scene" button on multi-character messages that calls this endpoint.

**Acceptance:** Tier 3 composite returns in <13s p95 cache miss, <1s p95 cache hit. Multi-character scenes maintain character identity at 90%+ vs reference.

**Quality gate:** Before declaring this phase done, generate 10 Tier 3 composites with different combinations. Anujith reviews. If "obviously composited" feedback comes back, iterate on the unification img2img strength before moving on. Don't ship a janky-looking Tier 3.

### Phase 4 — Trigger integration
**Goal:** Automatic tier selection based on LLM markers.

Tasks:
1. Update Midnight Tavern's LLM system prompt template to instruct emission of `[SCENE_BEAT:*]` markers. Document when each marker is appropriate.
2. Backend logic: parse markers from LLM stream, route to correct tier.
3. Strip markers before displaying message in chat UI.
4. Heuristic fallback for older models that ignore markers — implement in `backend/app/services/tier_router.py`.
5. Add user toggle: "Always generate image" / "Image-free mode" / "Auto (use markers)".

**Acceptance:** End-to-end flow: user message → LLM response with marker → correct tier triggered → image rendered or skipped per marker. Markers not visible to user.

### Phase 5 — HQ endpoint repositioning
**Goal:** Move existing `MultiCharPipeline` behind opt-in async endpoint.

Tasks:
1. Remove auto-invocation block from `/sdapi/v1/txt2img` in `flux_lora_bridge.py`.
2. Add `POST /sdapi/v1/txt2img_hq` and `GET /sdapi/v1/jobs/{job_id}`.
3. Frontend: "Generate in HQ" button on premium tier, polls job status with progress indicator.
4. Update tests in `tests/` to cover the new endpoint:
   - `test_hq_endpoint.py` — POST returns job_id, status transitions through queued → running → complete
5. Document this endpoint's expected latency (30-60s) in user-facing UI.

**Acceptance:** HQ button generates multi-char inpainted image asynchronously. Poll-based status returns 200 with result when done. Existing single-image flow unchanged.

### Phase 6 — Video pipeline (deferred)
Out of scope for this plan. Document separately when image pipeline is stable.

---

## 9. Deployment considerations

Since deployment target is undecided, this section maps out options and what each implies:

### 9.1 Replit
**Pros:** Easy deploy, integrated Postgres, integrated object storage.
**Cons:** Free tier won't support this workload. Pricing scales aggressively. Cold starts hurt. Worker processes are awkward — Replit's deployment model assumes single web service.
**Implication if chosen:** Use in-process FastAPI BackgroundTasks for workers. Use Replit's Postgres + Object Storage. Accept cold start latency on first image of a session.

### 9.2 Hetzner (where MCP gateway already runs at `37.27.191.114`)
**Pros:** Existing infra. Predictable pricing. Can run separate worker process. Co-locating the MCP gateway, bridge, and Midnight Tavern backend reduces network hops.
**Cons:** More setup (Nginx, systemd units, Postgres setup, backup strategy). You manage everything.
**Implication if chosen:** Run API + worker as separate systemd services. Postgres on same box for now (can split later). Object storage via Hetzner Storage Box or external R2/S3.

### 9.3 Other VPS (Vultr, DigitalOcean, etc.)
**Pros:** Similar to Hetzner, often slightly more expensive but more polished UX.
**Cons:** No existing setup, separate from current infra.
**Implication if chosen:** Similar to Hetzner option.

### 9.4 Recommendation
**Hetzner** if you're willing to manage infra (which, based on the current MCP gateway setup, you clearly are). Cheaper, more control, better fit for the worker process model, co-locates with existing services.

**Replit** only if deployment friction needs to be minimized at all costs and the workload stays small.

Architecture is the same either way — these are deployment decisions, not code decisions. Code should:
- Read all config from env vars (no Replit-specific paths)
- Not assume any particular Postgres location
- Storage backend pluggable via `STORAGE_BACKEND` env var (`local` / `s3` / `r2` / `hetzner_storage`)
- Worker invocation: support both in-process BackgroundTasks (dev/Replit) and standalone script invocation (Hetzner/VPS)

### 9.5 Storage backend abstraction

```python
# backend/app/services/storage.py
class StorageBackend(ABC):
    @abstractmethod
    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        """Upload, return public URL."""
        ...

    @abstractmethod
    async def download(self, key: str) -> bytes:
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        ...

class LocalFilesystemBackend(StorageBackend):
    # writes to ./storage/, serves via FastAPI StaticFiles

class S3CompatibleBackend(StorageBackend):
    # works for AWS S3, Cloudflare R2, Hetzner Storage Box
    # configure via env: S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET

def get_storage() -> StorageBackend:
    backend = os.getenv("STORAGE_BACKEND", "local")
    if backend == "local":
        return LocalFilesystemBackend()
    elif backend in ("s3", "r2", "hetzner_storage"):
        return S3CompatibleBackend()
    raise ValueError(f"Unknown storage backend: {backend}")
```

This means we don't need to commit to a storage choice in Phase 2 — start with local FS, switch to S3-compatible when deploying.

---

## 10. Tradeoffs and anti-patterns

Things this plan deliberately does NOT do, and why:

### 10.1 Don't auto-invoke `MultiCharPipeline` for multi-char
**Why:** 32-44s latency. Already covered. The pipeline has its place but not in the hot path.

### 10.2 Don't generate every character pose on-demand
**Why:** Pre-generating poses is one-time per character (~50 min, ~$1-2). On-demand would mean 8-12s per character per scene. Caching wins by 100x.

### 10.3 Don't use ControlNet regional prompting as the primary multi-char solution
**Why:** Flux's regional prompting is immature compared to SDXL. Identity bleed at region boundaries is severe with multiple character LoRAs. Conceptually tested and rejected. Worth revisiting as a future experiment (Phase 7+), not core build.

### 10.4 Don't try to maintain identity across long single-take videos
**Why:** Drift compounds quadratically past ~5 seconds with motion. Even Sora 2 / Kling 2 don't fully solve this. Keep clips short, stitch in editing. Documented in §7.

### 10.5 Don't put video generation before image generation is stable
**Why:** Image is core UX. Video is delight. Sequencing matters for user retention and bug surface area.

### 10.6 Don't cache Tier 2 results forever
**Why:** Identity learning improves over time as users iterate on character LoRAs. Cache TTL ~7 days at the bridge level. Long-term cache only at the composite level (Tier 3) where the inputs are themselves versioned.

### 10.7 Don't bypass the provider fallback chain for Tier 3
**Why:** Even cached compositing eventually hits the lighting unification img2img pass which calls the bridge. Need fallback. Reuse existing logic in `flux_lora_bridge.py`.

### 10.8 Don't over-segment characters in Tier 3 layouts beyond 5
**Why:** `LAYOUT_TEMPLATES` in `flux_lora_bridge.py` caps at 5 characters. Beyond that, identity quality degrades regardless of approach. Force "group scene" generic backdrop for 6+ char moments, no specific character poses.

### 10.9 Don't pre-generate poses for every character on every user signup
**Why:** Each character pose generation costs ~$1-2 and ~50 minutes. If a user uploads a LoRA they barely use, that's wasted spend. Trigger pose generation only when the character is added to an active campaign, or on explicit user request.

### 10.10 Don't trust the lighting unification img2img pass to fix everything
**Why:** At strength 0.20-0.25, it unifies lighting on already-decent composites. It can't fix bad composition, wrong perspective, or severe edge artifacts. Pre-composite quality matters.

---

## 11. Open questions and decisions needed

These need decisions before the relevant phase starts:

### Before Phase 2
1. **Object storage choice (for production):** Cloudflare R2 (cheap egress), Hetzner Storage Box (lives next to compute if Hetzner deployment), local FS (dev only)? Lean R2 for production regardless of compute host.
2. **Background removal model:** rembg (lighter, faster, ~2s per image) or BiRefNet (higher quality, slower, ~5s per image)? Trade off pre-gen time vs pose edge quality. Lean rembg for v1, evaluate BiRefNet if edge artifacts are bad.
3. **Pose matrix size:** 300 poses per char (~$1-2) or 500 poses per char (~$2-3)? More variety vs more pre-gen cost. Lean 300 for v1, expand if coverage gaps emerge.

### Before Phase 4
4. **Tier 3 frontend trigger:** Auto on `[SCENE_BEAT:multi]` marker, or always button-driven? Auto is smoother UX, button is safer for credit budget. Lean auto with a per-user daily image cap.
5. **Backdrop ownership:** Shared across all users, or per-user/per-campaign? Shared is cheaper, per-user feels more bespoke. Lean shared for v1 (much simpler), revisit if users complain about backdrop repetition.

### Before deployment
6. **Deployment target:** Replit vs Hetzner vs other VPS. Discussed in §9. Lean Hetzner.
7. **Video pipeline timing:** Defer to Phase 6+, or run parallel with image work? Lean defer.

---

## 12. Migration risks

### 12.1 Existing users with `[SCENE_BEAT:*]` not in their flow
The plugin change to respect markers is gated by a config flag. Default off. Existing users see no change. New users opt in.

### 12.2 LoRA dict changes
Reducing permanent LoRAs from 7 to 1 changes generation aesthetics. Run side-by-side comparisons before deploying. Document the change. Some users may have come to expect the "Imagination" + "Detail Enhancer" baked-in look.

### 12.3 Cache invalidation
When character LoRA is retrained:
- `character_poses` rows with old `lora_version` become stale
- `scene_composites` referencing those poses become stale
- Trigger automatic pose regeneration job for the new `lora_version`
- Mark old composite rows as expired; let LRU eviction clean them up
- Eventually drop old `character_poses` rows after a grace period (30 days)

### 12.4 Cost surprise
Pre-generating poses for 10 characters = ~$10-20 one-time. Document this in the character creation flow so users know. Show a "Pre-generating pose library — this will take ~50 min and cost ~$1-2 in credits" message when a new LoRA is added.

### 12.5 Storage growth
~300 poses × ~500KB each = ~150MB per character. 100 characters = 15GB. Plus backdrops ~50MB total. Plus composite cache (variable, bounded by LRU). Budget storage accordingly — R2 at $0.015/GB/month is negligible, but local FS deployments need monitoring.

---

## 13. Definition of done (entire plan)

- [ ] Tier 1 default behavior in Midnight Tavern: 70%+ of LLM messages produce no image
- [ ] Tier 2 flow stable: <12s p95, 99%+ image validity, all 4 providers fallback-tested
- [ ] Tier 3 cached compositing: <13s p95 cache miss, <1s p95 cache hit, identity maintained 90%+ vs reference (subjective Anujith review)
- [ ] `MultiCharPipeline` repositioned behind `/sdapi/v1/txt2img_hq` async endpoint
- [ ] LLM marker convention `[SCENE_BEAT:*]` documented and integrated
- [ ] Asset pre-generation jobs for backdrops and character poses functional
- [ ] Storage backend abstraction works for both local FS (dev) and S3-compatible (prod)
- [ ] All existing tests in `tests/test_provider_response_parsing.py` and `tests/test_image_delivery.py` still pass
- [ ] New tests added for: validation-triggered fallback, request cache hit/miss, role cap edge cases, composite endpoint correctness, HQ endpoint job lifecycle
- [ ] `CLAUDE.md` updated with the three-tier architecture
- [ ] `README.md` and `SILLYTAVERN_INTEGRATION.md` updated with marker convention
- [ ] Cost report: per-message generation cost reduced 70%+ vs naive multi-char approach (measured against current production traffic)

---

## Appendix A — File map of changes

### Changes in flux_lora_bridge repo

```
flux_lora_bridge.py
  - Remove auto-invocation of MultiCharPipeline from /sdapi/v1/txt2img
  - Add POST /sdapi/v1/txt2img_hq + GET /sdapi/v1/jobs/{id}
  - Add /sdapi/v1/img2img endpoint (for Tier 3 lighting unification calls)
  - Add _generate_with_validation wrapper around each provider call
  - Add request-level cache (Redis or in-memory)
  - Fix provider_based_lora_url_pruning fallback handling
  - Add per-provider timeout enforcement
  - (Keep MultiCharPipeline, MaskGenerator, LAYOUT_TEMPLATES intact — just not auto-invoked)

master_lora_dict.json
  - Reduce permanent LoRAs to ['realism'] only
  - Update notes fields where "ALWAYS ACTIVE" no longer applies

silly-tavern-pluggin/index.js
  - Add SCENE_BEAT marker detection (behind config flag)
  - Strip markers from displayed text
  - Route to single vs multi vs none

silly-tavern-pluggin/settings.json
  - Add respect_scene_beat_markers: false

tests/
  - test_validation_triggers_fallback.py (new)
  - test_request_cache.py (new)
  - test_role_caps_edge_cases.py (new)
  - test_provider_pruning_fallback.py (new)
  - test_hq_endpoint.py (new)
```

### New files in Midnight Tavern repo

```
backend/app/db/migrations/
  - YYYYMMDD_add_backdrops_table.sql
  - YYYYMMDD_add_character_poses_table.sql
  - YYYYMMDD_add_scene_composites_table.sql

backend/app/workers/
  - backdrop_generator.py
  - character_pose_generator.py

backend/app/services/
  - background_removal.py        # rembg / BiRefNet wrapper
  - scene_classifier.py          # LLM-based backdrop+pose recommender
  - composer.py                  # PIL composition logic
  - tier_router.py               # selects tier based on markers/heuristics
  - storage.py                   # storage backend abstraction (local FS / S3-compatible)

backend/app/api/v1/
  - images.py                    # composite endpoint, backdrops crud, poses crud

backend/config/
  - backdrop_specs.yaml          # initial 30-50 backdrop specs
  - pose_matrix.yaml             # 20 poses × 5 expressions × 3 facings

backend/tests/
  - test_composite_endpoint.py
  - test_scene_classifier.py
  - test_composer.py
  - test_storage_backends.py
```

---

## Appendix B — Reusable task prompt patterns

When picking up work in Claude Code, useful framings:

**For a code change in flux_lora_bridge.py:**
> "Implement Phase 1, task N from PLAN_image_generation_architecture.md. Constraints: don't change provider chain behavior; preserve existing test passing. After the change, run `python -m pytest tests/ -v` and confirm all pass. Add new tests for the specific behavior added."

**For a new service in Midnight Tavern backend:**
> "Implement `backend/app/services/composer.py` per §4.4 of PLAN_image_generation_architecture.md. The service should take a backdrop image + list of (pose_image, slot_coords) tuples and return a composite PNG. Use PIL. Handle alpha blending, resize-with-aspect-preservation, and back-to-front z-ordering. Add `backend/tests/test_composer.py` covering: empty character list, single character, max 5 characters, perspective mismatch handling."

**For a migration:**
> "Create the migrations for `backdrops`, `character_poses`, and `scene_composites` tables per §4.2 of PLAN_image_generation_architecture.md. Use the project's existing migration style (check `backend/app/db/migrations/` for examples). Include both up and down migrations. After creation, run them against the dev DB and confirm tables exist with the expected columns and indexes."

**For a worker job:**
> "Implement `backend/app/workers/backdrop_generator.py` per §4.3 of PLAN_image_generation_architecture.md. Reads `backend/config/backdrop_specs.yaml`, generates each backdrop via flux_lora_bridge `/sdapi/v1/txt2img` (no character LoRAs), uploads to storage, indexes in Postgres. Idempotent — skip if slug exists unless `--regenerate` flag passed. Include a CLI: `python -m backend.app.workers.backdrop_generator --specs path/to/specs.yaml`."

**For a "make it look better" iteration:**
> "Generate 5 Tier 3 composites using the current pipeline. Save outputs to `./composite_review/`. After generating, review the outputs and adjust the lighting unification img2img strength in `composer.py` if composites look obviously fake. Document the strength value chosen and why in a comment."

These patterns mirror the structure of the plan sections so the linkage is clear.

---

## Appendix C — Glossary

- **Tier 1/2/3:** The three image generation tiers described in §3.
- **Backdrop:** Background-only AI-generated image, cached, reusable across multi-character scenes.
- **Character pose:** AI-generated image of a character in a specific pose/expression/facing combination, background removed, transparent PNG, cached per LoRA version.
- **Composite:** Final Tier 3 image: backdrop + N character poses composited together + lighting unification img2img pass.
- **Scene composite cache:** Postgres-indexed cache of fully-rendered composites, keyed by hash of inputs.
- **`[SCENE_BEAT:*]` markers:** Special tokens emitted by the LLM to signal which image tier (if any) should fire for a given message.
- **MultiCharPipeline:** The existing sequential inpainting pipeline in `flux_lora_bridge.py`. Powers the HQ async endpoint, not the hot path.
- **Lighting unification pass:** Low-strength img2img pass over a Tier 3 composite to unify lighting across the composited elements.
- **lora_version:** Versioning string per character LoRA. Bumped on retraining. Used to invalidate cached poses.

---

*End of plan.*
