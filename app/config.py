from decimal import Decimal

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    bot_token: str = ""
    verify_et_api_key: str = ""
    verify_et_base_url: str = "https://verify.et"
    check_et_api_key: str = ""
    check_et_base_url: str = "https://api.check.et/api/v1"
    justverify_api_key: str = ""
    justverify_base_url: str = "https://justverify.et"
    database_url: str = "sqlite+aiosqlite:///./tipa.db"
    platform_fee_birr: Decimal = Decimal("0.0")
    bot_username: str = "TipaPayBot"
    tip_reminder_hours: float = 24.0
    tip_expiry_hours: float = 72.0
    tip_reminder_loop_minutes: float = 30.0

    # Weekly digest DM cadence (per-creator anchored, dedup via last_weekly_digest_at)
    weekly_digest_days: int = 7
    digest_loop_minutes: float = 60.0

    # Tipa Pro subscription (creators pay Tipa directly; same no-custody flow)
    pro_price_birr: Decimal = Decimal("199.0")
    pro_duration_days: int = 30
    # Where creators send the Pro payment (must be a provider-verifiable method)
    tipa_receiving_method: str = "telebirr"
    tipa_receiving_account: str = ""
    # Comma-separated Telegram user ids that can manually approve Pro payments
    admin_telegram_ids: str = ""

    # Account ownership proof: creator sends this amount from the registered
    # account to Tipa's account with a coded reference (micro-deposit).
    account_verification_amount_birr: Decimal = Decimal("1.0")

    # Receipt screenshots are persisted here as dispute evidence.
    receipt_storage_dir: str = "data/receipts"

    # Telegram webhook mode. When set, updates arrive via POST /telegram/webhook
    # instead of long polling (recommended on Render). Secret must match the
    # X-Telegram-Bot-Api-Secret-Token header Telegram sends on every update.
    telegram_webhook_url: str = ""
    telegram_webhook_secret: str = ""

    # Production databases should be migrated with Alembic only; create_all is
    # a dev convenience (always on for local SQLite).
    auto_create_tables: bool = False

    # Deployment environment: "development" | "production". In production the
    # API fails closed — initData validation cannot be skipped and missing
    # secrets are treated as misconfiguration, not dev convenience.
    app_env: str = "development"

    # Abuse controls: per-tipper velocity limits.
    tipper_hourly_init_limit: int = 10
    tipper_daily_birr_cap: Decimal = Decimal("100000.0")

    # Verification provider circuit breaker: after this many consecutive
    # failures a provider is skipped for the cooldown window (seconds).
    breaker_failure_threshold: int = 3
    breaker_cooldown_seconds: float = 60.0

    # Optional error tracking (sentry-sdk is imported only when a DSN is set).
    sentry_dsn: str = ""

    # Optional vision-LLM receipt reading (#8): any OpenAI-compatible endpoint.
    # When the key is empty, receipts fall back to local pytesseract OCR.
    vision_llm_api_key: str = ""
    vision_llm_base_url: str = "https://api.openai.com/v1"
    vision_llm_model: str = "gpt-4o-mini"

    @property
    def admin_ids(self) -> set[int]:
        return {
            int(part.strip())
            for part in self.admin_telegram_ids.split(",")
            if part.strip().isdigit()
        }

    @property
    def get_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
