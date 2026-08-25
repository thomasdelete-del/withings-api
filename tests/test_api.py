from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _settings(tmp_path: Path, cors_allow_origins: str = "") -> Settings:
    return Settings(
        database_path=tmp_path / "test.db",
        base_url="https://example.test",
        api_key="test-api-key",
        admin_username="admin",
        admin_password="test-admin-password",
        token_encryption_key="",
        withings_client_id="",
        withings_client_secret="",
        withings_redirect_uri="",
        withings_scopes="user.metrics",
        sync_interval_minutes=30,
        import_timezone="Europe/Berlin",
        max_upload_mib=2,
        cors_allow_origins=cors_allow_origins,
    )


def test_health_auth_and_import(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/latest").status_code == 401
        assert (
            client.get(
                "/api/v1/latest",
                headers={"X-API-Key": "test-api-key"},
            ).status_code
            == 200
        )
        assert client.get("/admin/status").status_code == 401
        assert client.get(
            "/admin/status",
            auth=("admin", "test-admin-password"),
        ).status_code == 200

        csv_data = b"Date,Weight (kg)\n2026-08-25 08:00:00,72.0\n"
        imported = client.post(
            "/api/v1/import",
            headers={"X-API-Key": "test-api-key"},
            files={"file": ("weight.csv", csv_data, "text/csv")},
        )
        assert imported.status_code == 200
        assert imported.json()["inserted"] == 1

        weight = client.get(
            "/api/v1/weight",
            headers={"X-API-Key": "test-api-key"},
        )
        assert weight.status_code == 200
        assert weight.json()["count"] == 1


def test_cors_disabled_by_default(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get(
            "/api/v1/latest",
            headers={"X-API-Key": "test-api-key", "Origin": "null"},
        )
        assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_allowed_when_configured(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, cors_allow_origins="*"))) as client:
        preflight = client.options(
            "/api/v1/latest",
            headers={
                "Origin": "null",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-API-Key",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "*"

        response = client.get(
            "/api/v1/latest",
            headers={"X-API-Key": "test-api-key", "Origin": "null"},
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"
