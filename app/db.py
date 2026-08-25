from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.models import CanonicalMeasurement

SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_tokens (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    userid TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    scope TEXT,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    measured_at INTEGER NOT NULL,
    period_end INTEGER,
    metric TEXT NOT NULL,
    measure_type INTEGER,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    source TEXT NOT NULL,
    external_group_id TEXT,
    device_id TEXT,
    category INTEGER,
    raw_json TEXT,
    stored_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_measurements_time
ON measurements(measured_at DESC);

CREATE INDEX IF NOT EXISTS idx_measurements_metric_time
ON measurements(metric, measured_at DESC);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    imported_at INTEGER NOT NULL,
    rows_seen INTEGER NOT NULL,
    inserted INTEGER NOT NULL,
    duplicates INTEGER NOT NULL,
    errors INTEGER NOT NULL,
    details_json TEXT
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(SCHEMA)

    def is_connected(self) -> bool:
        with self.connect() as connection:
            return connection.execute("SELECT 1 FROM oauth_tokens WHERE id=1").fetchone() is not None

    def save_oauth_state(self, state: str) -> None:
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("DELETE FROM oauth_states WHERE created_at < ?", (now - 900,))
            connection.execute(
                "INSERT INTO oauth_states(state, created_at) VALUES (?, ?)",
                (state, now),
            )

    def consume_oauth_state(self, state: str, max_age_seconds: int = 600) -> bool:
        now = int(time.time())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT created_at FROM oauth_states WHERE state=?", (state,)
            ).fetchone()
            connection.execute("DELETE FROM oauth_states WHERE state=?", (state,))
        return bool(row and int(row["created_at"]) >= now - max_age_seconds)

    def save_tokens(
        self,
        *,
        userid: str,
        access_token: str,
        refresh_token: str,
        expires_at: int,
        scope: str,
    ) -> None:
        now = int(time.time())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO oauth_tokens(
                    id, userid, access_token, refresh_token, expires_at, scope, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    userid=excluded.userid,
                    access_token=excluded.access_token,
                    refresh_token=excluded.refresh_token,
                    expires_at=excluded.expires_at,
                    scope=excluded.scope,
                    updated_at=excluded.updated_at
                """,
                (userid, access_token, refresh_token, expires_at, scope, now),
            )

    def get_tokens(self) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM oauth_tokens WHERE id=1").fetchone()

    def upsert_measurements(self, measurements: Iterable[CanonicalMeasurement]) -> tuple[int, int]:
        inserted = 0
        duplicates = 0
        now = int(time.time())
        with self.connect() as connection:
            for item in measurements:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO measurements(
                        dedupe_key, measured_at, period_end, metric, measure_type,
                        value, unit, source, external_group_id, device_id, category,
                        raw_json, stored_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.dedupe_key,
                        item.measured_at,
                        item.period_end,
                        item.metric,
                        item.measure_type,
                        item.value,
                        item.unit,
                        item.source,
                        item.external_group_id,
                        item.device_id,
                        item.category,
                        item.raw_json,
                        now,
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    duplicates += 1
        return inserted, duplicates

    def set_state(self, key: str, value: str | int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sync_state(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, str(value)),
            )

    def get_state(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def record_import(
        self,
        *,
        filename: str,
        rows_seen: int,
        inserted: int,
        duplicates: int,
        errors: int,
        details: dict[str, Any],
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO imports(
                    filename, imported_at, rows_seen, inserted, duplicates, errors, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    filename,
                    int(time.time()),
                    rows_seen,
                    inserted,
                    duplicates,
                    errors,
                    json.dumps(details, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def query_measurements(
        self,
        *,
        metric: str | None = None,
        measure_type: int | None = None,
        start: int | None = None,
        end: int | None = None,
        limit: int = 500,
        offset: int = 0,
        ascending: bool = False,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        values: list[Any] = []
        if metric:
            clauses.append("metric = ?")
            values.append(metric)
        if measure_type is not None:
            clauses.append("measure_type = ?")
            values.append(measure_type)
        if start is not None:
            clauses.append("measured_at >= ?")
            values.append(start)
        if end is not None:
            clauses.append("measured_at <= ?")
            values.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        direction = "ASC" if ascending else "DESC"
        values.extend([limit, offset])
        with self.connect() as connection:
            return connection.execute(
                f"""
                SELECT * FROM measurements
                {where}
                ORDER BY measured_at {direction}, id {direction}
                LIMIT ? OFFSET ?
                """,
                values,
            ).fetchall()

    def latest_measurements(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY metric ORDER BY measured_at DESC, id DESC
                    ) AS position
                    FROM measurements
                ) WHERE position = 1
                ORDER BY metric
                """
            ).fetchall()

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            measurement_count = connection.execute(
                "SELECT COUNT(*) AS count FROM measurements"
            ).fetchone()["count"]
            import_count = connection.execute(
                "SELECT COUNT(*) AS count FROM imports"
            ).fetchone()["count"]
        return {"measurements": int(measurement_count), "imports": int(import_count)}


def row_to_api(row: sqlite3.Row) -> dict[str, Any]:
    measurement = CanonicalMeasurement(
        measured_at=int(row["measured_at"]),
        period_end=int(row["period_end"]) if row["period_end"] is not None else None,
        metric=str(row["metric"]),
        measure_type=int(row["measure_type"]) if row["measure_type"] is not None else None,
        value=float(row["value"]),
        unit=str(row["unit"]),
        source=str(row["source"]),
        external_group_id=row["external_group_id"],
        device_id=row["device_id"],
        category=int(row["category"]) if row["category"] is not None else None,
        raw_json=row["raw_json"],
    )
    return measurement.as_api_dict(row_id=int(row["id"]))
