from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    app_name: str = "LIBFAD"
    app_env: str = "development"
    app_debug: bool = True
    secret_key: str = Field(default="dev-secret-please-override")
    base_url: str = "http://localhost:8000"

    # Database
    database_url: str = "postgresql+asyncpg://libfad:libfad@localhost:5432/libfad"
    database_url_sync: str = "postgresql+psycopg2://libfad:libfad@localhost:5432/libfad"
    db_echo: bool = False  # logs SQL bruts (très verbeux) — désactivé par défaut

    # Auth
    jwt_secret: str = "dev-jwt-secret"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24

    # Cookies / sécurité — passer cookie_secure=true en production (HTTPS).
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    # Rate-limit connexion (anti-brute-force)
    login_max_attempts: int = 8
    login_window_seconds: int = 300

    # Stripe (abonnement SaaS) — laisser vide tant que non configuré.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_pro: str = ""
    stripe_price_business: str = ""

    @property
    def stripe_configured(self) -> bool:
        return bool(self.stripe_secret_key)

    # Anthropic
    anthropic_api_key: str = ""
    claude_model_default: str = "claude-sonnet-4-6"
    claude_model_heavy: str = "claude-opus-4-7"

    # Bot Telegram (un bot par société, jeton saisi dans l'interface)
    telegram_api_base: str = "https://api.telegram.org"
    telegram_polling_enabled: bool = True
    telegram_agent_model: str = "claude-opus-5-5"

    # SMTP
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_name: str = "LIBFAD"
    smtp_from_email: str = "noreply@libfad.local"
    smtp_tls: bool = True

    # Locale defaults
    default_locale: str = "fr_FR"
    default_currency: str = "EUR"
    default_timezone: str = "Europe/Paris"

    # Storage
    storage_dir: str = "./storage"

    # Scheduler (tâches quotidiennes : relances + abonnements)
    scheduler_enabled: bool = True
    scheduler_hour: int = 8  # heure locale d'exécution du job quotidien
    scheduler_minute: int = 0

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    # Valeurs placeholder du .env.example qui ne constituent pas une vraie config
    _SMTP_PLACEHOLDERS = {"your@email.com", "app-password", ""}

    # Valeurs de secrets non sûres (placeholders du .env.example)
    _INSECURE_SECRETS = {
        "change-me-to-a-random-64-char-string",
        "dev-secret-please-override",
        "change-me-too",
        "dev-jwt-secret",
        "",
    }

    @property
    def insecure_secrets(self) -> list[str]:
        """Liste les secrets laissés à leur valeur d'exemple (à durcir avant la prod)."""
        problems = []
        if self.secret_key in self._INSECURE_SECRETS:
            problems.append("SECRET_KEY")
        if self.jwt_secret in self._INSECURE_SECRETS:
            problems.append("JWT_SECRET")
        return problems

    @property
    def smtp_configured(self) -> bool:
        """True seulement si SMTP est réellement configuré (pas les valeurs d'exemple)."""
        return (
            bool(self.smtp_host)
            and self.smtp_user not in self._SMTP_PLACEHOLDERS
            and self.smtp_password not in self._SMTP_PLACEHOLDERS
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
