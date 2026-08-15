from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    bot_token: str = ""
    chapa_secret_key: str = "CHASECK_TEST-sandbox_secret_key"
    database_url: str = "sqlite+aiosqlite:///./tipa.db"
    webhook_base_url: str = "http://localhost:8000"
    platform_fee_birr: float = 0.0
    bot_username: str = "TipaPayBot"
    tip_reminder_hours: float = 24.0
    tip_expiry_hours: float = 72.0
    tip_reminder_loop_minutes: float = 30.0

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
