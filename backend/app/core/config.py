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
