"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    DATABASE_URL: str
    REDIS_URL: str
    SECRET_KEY: str
    CORS_ORIGINS: str
    DEBUG: bool = False

    # Dungeon Master AI
    ANTHROPIC_API_KEY: str | None = None
    DM_DEFAULT_MODEL: str = "claude-haiku-4-5-20251001"

    # Image generation (ported from Silly-Tavern-Flux-Bridge)
    IMAGE_PROVIDER_ORDER: str = "dummy"  # comma-separated: dummy,runware,wavespeed,fal,together
    IMAGES_STORAGE_DIR: str = "./var/images"
    IMAGE_PROMPT_SUMMARIZER_ENABLED: bool = False
    DEEPSEEK_MODEL: str = "deepseek-ai/DeepSeek-V3"
    RUNWARE_API_KEY: str | None = None
    RUNWARE_MODEL: str = "runware:101@1"
    WAVESPEED_API_KEY: str | None = None
    FAL_API_KEY: str | None = None
    TOGETHER_API_KEY: str | None = None

    # Phase 1 hardening: per-provider total budget incl. polling, request-level cache.
    IMAGE_PROVIDER_TIMEOUT_S: int = 60
    IMAGE_REQUEST_CACHE_BACKEND: str = "auto"  # auto = redis if REDIS_URL else memory
    IMAGE_REQUEST_CACHE_TTL_S: int = 604800  # 7 days

    # Storage backend abstraction (Phase 2): local FS for dev, S3-compatible for prod.
    STORAGE_BACKEND: str = "local"  # local | s3
    S3_ENDPOINT: str | None = None
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_BUCKET: str | None = None
    S3_PUBLIC_BASE_URL: str | None = None  # CDN/public URL prefix, falls back to endpoint

    # Tier 3 / composite tuning (Phase 3).
    COMPOSITE_LIGHTING_STRENGTH: float = 0.25
    COMPOSITE_LIGHTING_STEPS: int = 15
    COMPOSITE_DEFAULT_WIDTH: int = 1536
    COMPOSITE_DEFAULT_HEIGHT: int = 1024

    # Asset library paths (Phase 2).
    BACKDROP_SPECS_PATH: str = "./backend/config/backdrop_specs.yaml"
    POSE_MATRIX_PATH: str = "./backend/config/pose_matrix.yaml"
    BACKGROUND_REMOVAL_BACKEND: str = "rembg"  # rembg | birefnet

    # HQ MultiCharPipeline tuning (Phase 5). Ported defaults from
    # Silly-Tavern-Flux-Bridge/flux_lora_bridge.py Config (MULTI_CHAR_*).
    # Canvas dimensions come from the per-job request (job.width/height) so
    # they aren't config knobs here.
    HQ_BG_STEPS: int = 25
    HQ_FG_STEPS: int = 25
    HQ_FG_STRENGTH: float = 0.92
    HQ_MG_STRENGTH: float = 0.88
    HQ_BG_STRENGTH: float = 0.85
    HQ_HARMONIZE_ENABLED: bool = True
    HQ_HARMONIZE_STRENGTH: float = 0.30
    HQ_HARMONIZE_STEPS: int = 15
    HQ_FEATHER_PX: int = 40
    # Per-pass timeout = IMAGE_PROVIDER_TIMEOUT_S × this multiplier (HQ passes
    # legitimately need more headroom than single-shot Tier 2 calls).
    HQ_PER_PASS_TIMEOUT_MULTIPLIER: float = 1.5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS_ORIGINS into a list."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def image_provider_order_list(self) -> list[str]:
        """Parse comma-separated IMAGE_PROVIDER_ORDER into a list."""
        return [p.strip() for p in self.IMAGE_PROVIDER_ORDER.split(",") if p.strip()]


settings = Settings()
