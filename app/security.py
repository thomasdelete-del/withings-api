from __future__ import annotations

import secrets
from typing import Annotated

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials

from app.config import Settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
basic_auth = HTTPBasic(auto_error=False)


class TokenCipher:
    def __init__(self, key: str):
        if not key:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY fehlt")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY ist kein gültiger Fernet-Schlüssel") from exc

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError(
                "Gespeicherte Tokens können mit TOKEN_ENCRYPTION_KEY nicht entschlüsselt werden"
            ) from exc


def api_key_dependency(settings: Settings):
    async def require_api_key(
        candidate: Annotated[str | None, Depends(api_key_header)] = None,
    ) -> None:
        if not settings.api_key or not candidate or not secrets.compare_digest(candidate, settings.api_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Ungültiger oder fehlender API-Schlüssel",
            )

    return require_api_key


def admin_dependency(settings: Settings):
    async def require_admin(
        credentials: Annotated[
            HTTPBasicCredentials | None,
            Depends(basic_auth),
        ] = None,
    ) -> None:
        username_ok = bool(
            credentials
            and secrets.compare_digest(credentials.username, settings.admin_username)
        )
        password_ok = bool(
            credentials
            and settings.admin_password
            and secrets.compare_digest(credentials.password, settings.admin_password)
        )
        if not (username_ok and password_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Admin-Anmeldung erforderlich",
                headers={"WWW-Authenticate": "Basic"},
            )

    return require_admin
