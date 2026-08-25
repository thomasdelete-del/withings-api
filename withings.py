from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings
from app.db import Database
from app.models import CanonicalMeasurement, api_measurement_from_group
from app.security import TokenCipher

AUTHORIZATION_URL = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"
AUTH_ERROR_STATUSES = {100, 101, 102, 200, 214}


class WithingsError(RuntimeError):
    def __init__(self, message: str, api_status: int | None = None):
        super().__init__(message)
        self.api_status = api_status


class NotConnectedError(WithingsError):
    pass


@dataclass(frozen=True)
class SyncResult:
    pages: int
    received: int
    inserted: int
    duplicates: int
    started_at: int

    def as_dict(self) -> dict[str, int]:
        return {
            "pages": self.pages,
            "received": self.received,
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "started_at": self.started_at,
        }


class WithingsClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def authorization_url(self, state: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.withings_client_id,
                "scope": self.settings.withings_scopes,
                "redirect_uri": self.settings.callback_url,
                "state": state,
            }
        )
        return f"{AUTHORIZATION_URL}?{query}"

    async def _post(
        self,
        url: str,
        data: dict[str, Any],
        access_token: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, data=data, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise WithingsError(f"Withings ist nicht erreichbar: {exc}") from exc
        except (ValueError, TypeError) as exc:
            raise WithingsError("Withings lieferte keine gültige JSON-Antwort") from exc

        api_status = int(payload.get("status", -1))
        if api_status != 0:
            error = payload.get("error") or payload.get("body", {}).get("error") or "API-Fehler"
            raise WithingsError(f"Withings-Fehler {api_status}: {error}", api_status)
        body = payload.get("body")
        if not isinstance(body, dict):
            raise WithingsError("Withings-Antwort enthält keinen gültigen body")
        return body

    async def exchange_code(self, code: str) -> dict[str, Any]:
        return await self._post(
            TOKEN_URL,
            {
                "action": "requesttoken",
                "grant_type": "authorization_code",
                "client_id": self.settings.withings_client_id,
                "client_secret": self.settings.withings_client_secret,
                "code": code,
                "redirect_uri": self.settings.callback_url,
            },
        )

    async def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        return await self._post(
            TOKEN_URL,
            {
                "action": "requesttoken",
                "grant_type": "refresh_token",
                "client_id": self.settings.withings_client_id,
                "client_secret": self.settings.withings_client_secret,
                "refresh_token": refresh_token,
            },
        )

    async def get_measure_page(
        self,
        access_token: str,
        *,
        last_update: int | None,
        offset: int | None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"action": "getmeas", "category": 1}
        if last_update is None:
            data["startdate"] = 0
        else:
            data["lastupdate"] = max(1, last_update)
        if offset is not None:
            data["offset"] = offset
        return await self._post(MEASURE_URL, data, access_token)


def parse_measure_groups(body: dict[str, Any]) -> list[CanonicalMeasurement]:
    parsed: list[CanonicalMeasurement] = []
    for group in body.get("measuregrps", []):
        if not isinstance(group, dict) or "date" not in group:
            continue
        for measure in group.get("measures", []):
            if not isinstance(measure, dict) or "type" not in measure or "value" not in measure:
                continue
            item = api_measurement_from_group(group, measure)
            parsed.append(
                CanonicalMeasurement(
                    **{
                        **item.__dict__,
                        "raw_json": json.dumps(
                            {"group": group, "measure": measure},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                )
            )
    return parsed


class WithingsService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        client: WithingsClient | None = None,
    ):
        self.settings = settings
        self.database = database
        self.client = client or WithingsClient(settings)
        self._sync_lock = asyncio.Lock()
        self._token_lock = asyncio.Lock()

    def _cipher(self) -> TokenCipher:
        return TokenCipher(self.settings.token_encryption_key)

    def new_authorization(self) -> str:
        state = secrets.token_urlsafe(32)
        self.database.save_oauth_state(state)
        return self.client.authorization_url(state)

    async def finish_authorization(self, code: str, state: str) -> None:
        if not self.database.consume_oauth_state(state):
            raise WithingsError("OAuth-State ist ungültig oder abgelaufen")
        token_data = await self.client.exchange_code(code)
        self._store_token_response(token_data)

    def _store_token_response(self, token_data: dict[str, Any]) -> None:
        required = ("userid", "access_token", "refresh_token", "expires_in")
        missing = [name for name in required if name not in token_data]
        if missing:
            raise WithingsError(f"Token-Antwort unvollständig: {', '.join(missing)}")
        cipher = self._cipher()
        self.database.save_tokens(
            userid=str(token_data["userid"]),
            access_token=cipher.encrypt(str(token_data["access_token"])),
            refresh_token=cipher.encrypt(str(token_data["refresh_token"])),
            expires_at=int(time.time()) + int(token_data["expires_in"]),
            scope=str(token_data.get("scope", "")),
        )

    async def _access_token(self, force_refresh: bool = False) -> str:
        async with self._token_lock:
            row = self.database.get_tokens()
            if row is None:
                raise NotConnectedError("Noch kein Withings-Konto verbunden")
            cipher = self._cipher()
            if not force_refresh and int(row["expires_at"]) > int(time.time()) + 300:
                return cipher.decrypt(str(row["access_token"]))

            refresh_token = cipher.decrypt(str(row["refresh_token"]))
            token_data = await self.client.refresh_token(refresh_token)
            if "userid" not in token_data:
                token_data["userid"] = row["userid"]
            self._store_token_response(token_data)
            new_row = self.database.get_tokens()
            if new_row is None:
                raise WithingsError("Token konnte nicht gespeichert werden")
            return cipher.decrypt(str(new_row["access_token"]))

    async def sync(self) -> SyncResult:
        async with self._sync_lock:
            started_at = int(time.time())
            state = self.database.get_state("last_metrics_sync")
            last_update = max(1, int(state) - 86400) if state else None
            access_token = await self._access_token()

            pages = received = inserted = duplicates = 0
            offset: int | None = None
            seen_offsets: set[int] = set()
            retried_auth = False

            while True:
                try:
                    body = await self.client.get_measure_page(
                        access_token,
                        last_update=last_update,
                        offset=offset,
                    )
                except WithingsError as exc:
                    if exc.api_status in AUTH_ERROR_STATUSES and not retried_auth:
                        access_token = await self._access_token(force_refresh=True)
                        retried_auth = True
                        continue
                    raise

                pages += 1
                page_measurements = parse_measure_groups(body)
                received += len(page_measurements)
                page_inserted, page_duplicates = self.database.upsert_measurements(
                    page_measurements
                )
                inserted += page_inserted
                duplicates += page_duplicates

                if not body.get("more"):
                    break
                next_offset = int(body.get("offset", -1))
                if next_offset < 0 or next_offset in seen_offsets:
                    raise WithingsError("Ungültige Seitennavigation in der Withings-Antwort")
                seen_offsets.add(next_offset)
                offset = next_offset
                if pages >= 1000:
                    raise WithingsError("Synchronisation nach 1000 Seiten abgebrochen")

            self.database.set_state("last_metrics_sync", started_at)
            return SyncResult(
                pages=pages,
                received=received,
                inserted=inserted,
                duplicates=duplicates,
                started_at=started_at,
            )
