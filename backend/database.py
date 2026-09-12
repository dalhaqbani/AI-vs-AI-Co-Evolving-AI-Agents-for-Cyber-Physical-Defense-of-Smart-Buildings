import json
import os
import sqlite3
from contextlib import contextmanager


DATABASE_PATH = os.getenv("DATABASE_PATH", "readings.db")


@contextmanager
def connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def initialize() -> None:
    with connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                component_id TEXT NOT NULL,
                component_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                is_test INTEGER NOT NULL,
                data_json TEXT NOT NULL,
                ai_status TEXT NOT NULL,
                ai_alert TEXT,
                ai_action TEXT,
                ai_score REAL NOT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_readings_component ON readings(component_id, id)"
        )


def insert_reading(payload: dict, decision: dict) -> None:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO readings (
                component_id, component_type, timestamp, is_test,
                data_json, ai_status, ai_alert, ai_action, ai_score, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["component_id"],
                payload["component_type"],
                payload["timestamp"],
                int(payload.get("is_test", False)),
                json.dumps(payload["data"]),
                decision["status"],
                decision["alert"],
                decision["action"],
                decision["score"],
                json.dumps(payload),
            ),
        )


def recent(limit: int = 50) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def latest_per_component() -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT r.* FROM readings r
            JOIN (
                SELECT component_id, MAX(id) AS max_id
                FROM readings GROUP BY component_id
            ) latest ON r.id = latest.max_id
            ORDER BY r.component_id
            """
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["is_test"] = bool(item["is_test"])
    item["data"] = json.loads(item.pop("data_json"))
    item.pop("raw_json", None)
    return item
