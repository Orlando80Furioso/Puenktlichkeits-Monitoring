"""SQLite-Schema und Hilfsfunktionen für den Live-Event-Logger."""

import sqlite3
from pathlib import Path

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle TEXT NOT NULL,
    route TEXT,
    trip TEXT,
    stationid TEXT,
    stationname TEXT,
    event_time TEXT,      -- "time"-Feld aus der Quelle (Ist-Ankunftszeit)
    sendtime TEXT,         -- "sendtime"-Feld aus der Quelle
    inserted_at TEXT DEFAULT (datetime('now')),
    raw_json TEXT          -- kompletter roher "raw"-Knoten, für Debugging
);

CREATE INDEX IF NOT EXISTS idx_raw_events_route ON raw_events(route);
CREATE INDEX IF NOT EXISTS idx_raw_events_station ON raw_events(stationid);
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_events_dedup
    ON raw_events(vehicle, sendtime, stationid);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_event_id INTEGER NOT NULL REFERENCES raw_events(id),
    route TEXT,
    stop_id TEXT,          -- gematchte GTFS stop_id
    stop_name_gtfs TEXT,   -- voller GTFS-Haltestellenname
    trip_id TEXT,          -- gematchte GTFS trip_id
    scheduled_time TEXT,   -- geplante Ankunftszeit "HH:MM:SS" (GTFS, kann >=24h sein)
    actual_time TEXT,      -- Ist-Zeit aus raw_events.event_time
    delay_seconds INTEGER, -- actual - scheduled, positiv = zu spät
    computed_at TEXT DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_raw_event ON matches(raw_event_id);

CREATE TABLE IF NOT EXISTS dwell_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle TEXT NOT NULL,
    route TEXT,
    stationid TEXT,
    stationname TEXT,
    start_time TEXT,       -- erstes geloggtes Event dieses Aufenthalts
    end_time TEXT,         -- letztes geloggtes Event dieses Aufenthalts
    dwell_seconds INTEGER, -- end_time - start_time
    event_count INTEGER,   -- Anzahl Rohdaten-Events in diesem Aufenthalt
    computed_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dwell_station ON dwell_events(stationname);
CREATE INDEX IF NOT EXISTS idx_dwell_route ON dwell_events(route);
"""


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def insert_event(conn: sqlite3.Connection, record: dict, raw_json: str) -> bool:
    """Fügt ein Event ein. Gibt False zurück, wenn es (vehicle, sendtime, stationid)
    schon existiert (Dedup bei Reconnects / doppelten Patches)."""
    try:
        conn.execute(
            """
            INSERT INTO raw_events
                (vehicle, route, trip, stationid, stationname, event_time, sendtime, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("vehicle"),
                record.get("route") or record.get("route_translated"),
                record.get("trip"),
                record.get("stationid"),
                record.get("stationname"),
                record.get("time"),
                record.get("sendtime"),
                raw_json,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
