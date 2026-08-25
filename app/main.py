from __future__ import annotations

import asyncio
import csv
import io
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Annotated

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

from app import __version__
from app.config import Settings, load_settings
from app.dashboard import dashboard_html
from app.rich_dashboard import rich_dashboard_html
from app.db import Database, row_to_api
from app.importer import import_file
from app.models import MEASURE_TYPES
from app.security import admin_dependency, api_key_dependency
from app.withings import NotConnectedError, WithingsError, WithingsService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("withings-api")


def _parse_time(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    if raw.isdigit():
        return int(raw)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Zeit muss Unix-Sekunden oder ISO-8601 sein",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _csv_response(rows: list[dict[str, object]]) -> StreamingResponse:
    output = io.StringIO()
    fieldnames = [
        "timestamp",
        "measured_at",
        "period_end_timestamp",
        "period_end",
        "metric",
        "value",
        "unit",
        "measure_type",
        "source",
        "device_id",
        "category",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)
    headers = {"Content-Disposition": 'attachment; filename="withings-measurements.csv"'}
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


def create_app(custom_settings: Settings | None = None) -> FastAPI:
    settings = custom_settings or load_settings()
    database = Database(settings.database_path)
    service = WithingsService(settings, database)
    require_api_key = api_key_dependency(settings)
    require_admin = admin_dependency(settings)
    stop_event = asyncio.Event()

    async def safe_sync() -> None:
        try:
            result = await service.sync()
            logger.info("Withings-Synchronisation abgeschlossen: %s", result.as_dict())
        except NotConnectedError:
            logger.info("Noch kein Withings-Konto verbunden; Synchronisation übersprungen")
        except Exception:
            logger.exception("Automatische Withings-Synchronisation fehlgeschlagen")

    async def periodic_sync() -> None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10)
            return
        except TimeoutError:
            pass
        while not stop_event.is_set():
            await safe_sync()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=settings.sync_interval_minutes * 60,
                )
            except TimeoutError:
                continue

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        sync_task = asyncio.create_task(periodic_sync(), name="withings-periodic-sync")
        yield
        stop_event.set()
        sync_task.cancel()
        with suppress(asyncio.CancelledError):
            await sync_task

    app = FastAPI(
        title="Private Withings REST API",
        summary="Eigene REST-API für Withings-Daten und Withings-Exporte",
        version=__version__,
        lifespan=lifespan,
    )

    # Nur aktiv, wenn CORS_ALLOW_ORIGINS gesetzt ist (z.B. "*" oder eine
    # kommagetrennte Liste erlaubter Origins) — z.B. nötig, damit ein lokal im
    # Browser geöffnetes Dashboard (Origin "null" bei file://-Seiten, oder eine
    # andere Domain) die API per fetch() direkt aufrufen darf. Ohne gesetzte
    # Variable bleibt CORS wie bisher deaktiviert (kein Access-Control-Allow-
    # Origin-Header, Browser-Anfragen von anderen Origins werden blockiert).
    # Der eigentliche Zugriffsschutz bleibt in jedem Fall der X-API-Key: CORS
    # entscheidet nur, ob JavaScript auf einer fremden Seite die Antwort lesen
    # darf, nicht, ob die Anfrage überhaupt Daten zurückbekommt.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["X-API-Key", "Content-Type"],
            allow_credentials=False,
        )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(WithingsError)
    async def withings_error_handler(_, exc: WithingsError):
        status_code = 409 if isinstance(exc, NotConnectedError) else 502
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home() -> str:
        connected = database.is_connected()
        connection_text = "verbunden" if connected else "noch nicht verbunden"
        setup_text = (
            "vollständig"
            if not settings.missing_setup
            else "unvollständig: " + ", ".join(settings.missing_setup)
        )
        return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Withings REST API</title>
<style>body{{font:17px system-ui;max-width:720px;margin:3rem auto;padding:0 1rem;line-height:1.5}}
a{{color:#0759b8}}code{{background:#eee;padding:.15rem .35rem;border-radius:.3rem}}</style></head>
<body><h1>Withings REST API</h1>
<p>Withings-Konto: <strong>{connection_text}</strong><br>Konfiguration: {setup_text}</p>
<p><a href="/oauth/start">Withings-Konto verbinden</a> ·
<a href="/dashboard">Dashboard</a> · <a href="/rich-dashboard">Dashboard (erweitert)</a> · <a href="/admin/status">Status</a> ·
<a href="/docs">API-Dokumentation</a></p>
<p>Geschützte API-Aufrufe benötigen den Header <code>X-API-Key</code>.</p>
</body></html>"""

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> str:
        return dashboard_html()

    @app.get("/rich-dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def rich_dashboard() -> str:
        return rich_dashboard_html()

    @app.get("/health", tags=["Betrieb"])
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "setup_complete": not settings.missing_setup,
            "withings_connected": database.is_connected(),
        }

    @app.get("/admin/status", dependencies=[Depends(require_admin)], tags=["Admin"])
    async def admin_status() -> dict[str, object]:
        last_sync = database.get_state("last_metrics_sync")
        return {
            "version": __version__,
            "base_url": settings.base_url,
            "callback_url": settings.callback_url,
            "missing_setup": settings.missing_setup,
            "withings_connected": database.is_connected(),
            "last_metrics_sync": int(last_sync) if last_sync else None,
            **database.counts(),
        }

    @app.get("/oauth/start", dependencies=[Depends(require_admin)], tags=["OAuth"])
    async def oauth_start() -> RedirectResponse:
        oauth_missing = [
            name
            for name in ("TOKEN_ENCRYPTION_KEY", "WITHINGS_CLIENT_ID", "WITHINGS_CLIENT_SECRET")
            if name in settings.missing_setup
        ]
        if oauth_missing:
            raise HTTPException(
                status_code=503,
                detail="OAuth-Konfiguration fehlt: " + ", ".join(oauth_missing),
            )
        return RedirectResponse(service.new_authorization(), status_code=302)

    @app.get("/oauth/callback", response_class=HTMLResponse, tags=["OAuth"])
    async def oauth_callback(
        background_tasks: BackgroundTasks,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> str:
        if error:
            raise HTTPException(status_code=400, detail=f"Withings-Autorisierung abgelehnt: {error}")
        if not code or not state:
            raise HTTPException(status_code=400, detail="code oder state fehlt")
        await service.finish_authorization(code, state)
        background_tasks.add_task(safe_sync)
        return """<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>Withings verbunden</title>
<body style="font:18px system-ui;max-width:680px;margin:3rem auto;padding:0 1rem">
<h1>Withings ist verbunden</h1><p>Die erste Synchronisation läuft jetzt im Hintergrund.</p>
<p><a href="/admin/status">Status ansehen</a> · <a href="/docs">API öffnen</a></p></body></html>"""

    @app.post(
        "/api/v1/sync",
        dependencies=[Depends(require_api_key)],
        tags=["Synchronisation"],
    )
    async def sync_now() -> dict[str, int]:
        return (await service.sync()).as_dict()

    @app.get(
        "/api/v1/measure-types",
        dependencies=[Depends(require_api_key)],
        tags=["Messwerte"],
    )
    async def measure_types() -> dict[str, dict[str, object]]:
        return {
            str(type_id): {"metric": metric, "unit": unit}
            for type_id, (metric, unit) in MEASURE_TYPES.items()
        }

    @app.get(
        "/api/v1/measurements",
        dependencies=[Depends(require_api_key)],
        tags=["Messwerte"],
    )
    async def measurements(
        metric: str | None = None,
        measure_type: int | None = None,
        start_value: Annotated[str | None, Query(alias="from")] = None,
        end_value: Annotated[str | None, Query(alias="to")] = None,
        limit: Annotated[int, Query(ge=1, le=10_000)] = 500,
        offset: Annotated[int, Query(ge=0)] = 0,
        ascending: bool = False,
    ) -> dict[str, object]:
        rows = database.query_measurements(
            metric=metric.strip().lower() if metric else None,
            measure_type=measure_type,
            start=_parse_time(start_value),
            end=_parse_time(end_value),
            limit=limit,
            offset=offset,
            ascending=ascending,
        )
        return {"count": len(rows), "offset": offset, "items": [row_to_api(row) for row in rows]}

    @app.get(
        "/api/v1/latest",
        dependencies=[Depends(require_api_key)],
        tags=["Messwerte"],
    )
    async def latest() -> dict[str, object]:
        rows = database.latest_measurements()
        return {"count": len(rows), "items": [row_to_api(row) for row in rows]}

    @app.get(
        "/api/v1/weight",
        dependencies=[Depends(require_api_key)],
        tags=["Messwerte"],
    )
    async def weight(
        start_value: Annotated[str | None, Query(alias="from")] = None,
        end_value: Annotated[str | None, Query(alias="to")] = None,
        limit: Annotated[int, Query(ge=1, le=10_000)] = 500,
    ) -> dict[str, object]:
        rows = database.query_measurements(
            metric="weight",
            start=_parse_time(start_value),
            end=_parse_time(end_value),
            limit=limit,
        )
        return {"count": len(rows), "items": [row_to_api(row) for row in rows]}

    @app.get(
        "/api/v1/export.csv",
        dependencies=[Depends(require_api_key)],
        tags=["Export"],
    )
    async def export_csv(
        metric: str | None = None,
        start_value: Annotated[str | None, Query(alias="from")] = None,
        end_value: Annotated[str | None, Query(alias="to")] = None,
    ) -> StreamingResponse:
        rows = database.query_measurements(
            metric=metric.strip().lower() if metric else None,
            start=_parse_time(start_value),
            end=_parse_time(end_value),
            limit=100_000,
            ascending=True,
        )
        return _csv_response([row_to_api(row) for row in rows])

    @app.post(
        "/api/v1/import",
        dependencies=[Depends(require_api_key)],
        tags=["Import"],
    )
    async def import_export(
        file: Annotated[UploadFile, File(description="Withings-CSV oder vollständiges ZIP-Archiv")],
    ) -> dict[str, object]:
        max_bytes = settings.max_upload_mib * 1024 * 1024
        content = await file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Datei ist größer als {settings.max_upload_mib} MiB",
            )
        if not content:
            raise HTTPException(status_code=400, detail="Datei ist leer")
        try:
            report = import_file(
                database,
                data=content,
                filename=file.filename or "upload.csv",
                timezone_name=settings.import_timezone,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return report.as_dict()

    return app


app = create_app()
