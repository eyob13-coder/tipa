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
    platform_fee_birr: float = 0.0
    bot_username: str = "TipaPayBot"
    tip_reminder_hours: float = 24.0
    tip_expiry_hours: float = 72.0
    tip_reminder_loop_minutes: float = 30.0

    # Tipa Pro subscription (creators pay Tipa directly; same no-custody flow)
    pro_price_birr: float = 199.0
    pro_duration_days: int = 30
    # Where creators send the Pro payment (must be a provider-verifiable method)
    tipa_receiving_method: str = "telebirr"
    tipa_receiving_account: str = ""
    # Comma-separated Telegram user ids that can manually approve Pro payments
    admin_telegram_ids: str = ""

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
