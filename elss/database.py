from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import g
from werkzeug.security import generate_password_hash


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "instance" / "elss.sqlite3"
VERCEL_DB_PATH = Path("/tmp/elss.sqlite3")
DB_PATH = Path(
    os.environ.get(
        "ELSS_DB",
        VERCEL_DB_PATH if os.environ.get("VERCEL") else DEFAULT_DB_PATH,
    )
)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    connection = get_db()
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_number TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            vessel_status TEXT NOT NULL,
            rss_number TEXT NOT NULL,
            catch_on_board INTEGER NOT NULL DEFAULT 0,
            landing_declaration_submitted INTEGER NOT NULL DEFAULT 0,
            last_fishing_operation_at_utc TEXT,
            simulated_now_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_number TEXT NOT NULL UNIQUE,
            report_type TEXT NOT NULL,
            trip_id INTEGER NOT NULL,
            operation TEXT NOT NULL,
            message_id TEXT NOT NULL UNIQUE,
            sequence_number INTEGER NOT NULL,
            status TEXT NOT NULL,
            filename TEXT NOT NULL,
            xml_content TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            corrected_report_id INTEGER,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            FOREIGN KEY (trip_id) REFERENCES trips(id),
            FOREIGN KEY (corrected_report_id) REFERENCES reports(id)
        );

        CREATE TABLE IF NOT EXISTS transmissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            operation TEXT NOT NULL,
            transmission_status TEXT NOT NULL,
            sent_at_utc TEXT,
            ack_status TEXT NOT NULL,
            ack_message TEXT,
            correlation_status TEXT NOT NULL,
            encrypted INTEGER NOT NULL DEFAULT 0,
            encrypted_payload TEXT,
            ack_id TEXT,
            ack_at_utc TEXT,
            ack_operation TEXT,
            FOREIGN KEY (report_id) REFERENCES reports(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        connection.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("fisherman", generate_password_hash("elss-demo"), "FISHERMAN"),
        )
    if connection.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 0:
        connection.execute(
            """
            INSERT INTO trips (
                trip_number, status, vessel_status, rss_number, catch_on_board,
                landing_declaration_submitted, last_fishing_operation_at_utc, simulated_now_utc
            ) VALUES ('TRIP-001', 'CURRENT', 'AT_SEA', 'RSS123456', 1, 0, NULL, ?)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
    connection.commit()
