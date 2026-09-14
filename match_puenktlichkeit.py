"""
Schritt 5: Matching-Logik. Gleicht Ist-Events aus `raw_events` (siehe
logger.py) mit dem Soll-Fahrplan (GTFS-Auszug, siehe extract_gtfs_subset.py)
ab und berechnet die Verspätung je Event.

Matching-Strategie (siehe README "Bekannte offene Fragen"):
- `stationid` aus dem Live-Feed entspricht NICHT der GTFS stop_id, daher
  Matching über Linie + Haltestellenname (Fuzzy-Match, siehe gtfs_lookup.py).
- `trip` aus dem Live-Feed ist keine verlässliche GTFS trip_id, daher wird
  pro Event die zeitlich nächstgelegene geplante Ankunft am gematchten Stop
  gewählt (innerhalb eines Zeitfensters), nicht über trip_id direkt verknüpft.

Nutzung:
    python match_puenktlichkeit.py
    python match_puenktlichkeit.py --window-minutes 15
"""

import argparse
import sqlite3
from datetime import datetime

from config import DB_PATH
from db import get_connection
from gtfs_lookup import GtfsData, gtfs_time_to_seconds

DEFAULT_WINDOW_MINUTES = 20


def match_event(gtfs: GtfsData, route: str, stationname: str, event_time_str: str, window_seconds: int):
    """Gibt (stop_id, stop_name, trip_id, scheduled_str, delay_seconds) zurück
    oder None, wenn kein Match im Zeitfenster gefunden wurde."""
    route_id = gtfs.route_id_by_short_name.get(route)
    if not route_id:
        return None

    event_dt = datetime.strptime(event_time_str, "%Y-%m-%d %H:%M:%S")
    stop_id = gtfs.match_stop_by_name(route_id, stationname)
    if not stop_id:
        return None

    active_services = gtfs.active_service_ids(event_dt.date())
    candidates = gtfs.scheduled_arrivals(route_id, stop_id, active_services)
    if not candidates:
        return None

    event_seconds = event_dt.hour * 3600 + event_dt.minute * 60 + event_dt.second

    best = None
    best_diff = None
    for sched_seconds, sched_str, trip_id in candidates:
        # GTFS-Zeiten >=24h auf denselben Tageskreis abbilden (Modulo 24h),
        # damit z.B. 00:05 Ist mit 24:05 Soll (Fahrt nach Mitternacht) vergleichbar ist.
        diff = event_seconds - (sched_seconds % 86400)
        # kürzesten Abstand auf einem 24h-Kreis nehmen
        if diff > 43200:
            diff -= 86400
        elif diff < -43200:
            diff += 86400
        if best_diff is None or abs(diff) < abs(best_diff):
            best_diff = diff
            best = (stop_id, gtfs.stop_name_by_id[stop_id], trip_id, sched_str)

    if best is None or abs(best_diff) > window_seconds:
        return None
    return (*best, best_diff)


def run(window_minutes: int):
    conn = get_connection(DB_PATH)
    gtfs = GtfsData()
    window_seconds = window_minutes * 60

    cur = conn.execute(
        """
        SELECT id, route, stationname, event_time
        FROM raw_events
        WHERE id NOT IN (SELECT raw_event_id FROM matches)
        """
    )
    rows = cur.fetchall()
    matched, unmatched = 0, 0

    for raw_event_id, route, stationname, event_time in rows:
        if not (route and stationname and event_time):
            unmatched += 1
            continue
        result = match_event(gtfs, route, stationname, event_time, window_seconds)
        if result is None:
            unmatched += 1
            continue
        stop_id, stop_name_gtfs, trip_id, scheduled_str, delay_seconds = result
        conn.execute(
            """
            INSERT OR REPLACE INTO matches
                (raw_event_id, route, stop_id, stop_name_gtfs, trip_id, scheduled_time, actual_time, delay_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (raw_event_id, route, stop_id, stop_name_gtfs, trip_id, scheduled_str, event_time, delay_seconds),
        )
        matched += 1
    conn.commit()

    print(f"Gematcht: {matched}, ohne Match: {unmatched} (Fenster: ±{window_minutes} Min.)")
    print_summary(conn)


def print_summary(conn: sqlite3.Connection):
    cur = conn.execute(
        """
        SELECT route,
               COUNT(*) AS n,
               ROUND(AVG(delay_seconds) / 60.0, 1) AS avg_delay_min,
               ROUND(100.0 * SUM(CASE WHEN delay_seconds <= 180 THEN 1 ELSE 0 END) / COUNT(*), 1) AS puenktlichkeitsquote
        FROM matches
        GROUP BY route
        """
    )
    print("\nZusammenfassung je Linie (Pünktlichkeitsquote = Anteil Ankünfte <= 3 Min. Verspätung):")
    for route, n, avg_delay_min, quote in cur.fetchall():
        print(f"  Linie {route}: {n} Events, Ø Verspätung {avg_delay_min} Min., Pünktlichkeit {quote}%")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES)
    args = parser.parse_args()
    run(args.window_minutes)


if __name__ == "__main__":
    main()
