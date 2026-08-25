from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _integer(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} muss eine ganze Zahl sein") from exc


@dataclass(frozen=True)
class Settings:
    database_path: Path
    base_url: str
    api_key: str
    admin_username: str
    admin_password: str
    token_encryption_key: str
    withings_client_id: str
    withings_client_secret: str
    withings_redirect_uri: str
    withings_scopes: str
    sync_interval_minutes: int
    import_timezone: str
    max_upload_mib: int

    @property
    def callback_url(self) -> str:
        return self.withings_redirect_uri or f"{self.base_url}/oauth/callback"

    @property
    def missing_setup(self) -> list[str]:
        required = {
            "API_KEY": self.api_key,
            "ADMIN_PASSWORD": self.admin_password,
            "TOKEN_ENCRYPTION_KEY": self.token_encryption_key,
            "WITHINGS_CLIENT_ID": self.withings_client_id,
            "WITHINGS_CLIENT_SECRET": self.withings_client_secret,
        }
        return [name for name, value in required.items() if not value]


def load_settings() -> Settings:
    base_url = os.getenv("BASE_URL", "http://localhost:8000").strip().rstrip("/")
    database_path = Path(os.getenv("DATABASE_PATH", "./data/withings.db"))
    return Settings(
        database_path=database_path,
        base_url=base_url,
        api_key=os.getenv("API_KEY", "").strip(),
        admin_username=os.getenv("ADMIN_USERNAME", "admin").strip() or "admin",
        admin_password=os.getenv("ADMIN_PASSWORD", "").strip(),
        token_encryption_key=os.getenv("TOKEN_ENCRYPTION_KEY", "").strip(),
        withings_client_id=os.getenv("WITHINGS_CLIENT_ID", "").strip(),
        withings_client_secret=os.getenv("WITHINGS_CLIENT_SECRET", "").strip(),
        withings_redirect_uri=os.getenv("WITHINGS_REDIRECT_URI", "").strip(),
        withings_scopes=os.getenv("WITHINGS_SCOPES", "user.metrics").strip(),
        sync_interval_minutes=max(5, _integer("SYNC_INTERVAL_MINUTES", 30)),
        import_timezone=os.getenv("IMPORT_TIMEZONE", "Europe/Berlin").strip(),
        max_upload_mib=max(1, _integer("MAX_UPLOAD_MIB", 25)),
    )
