"""
Standzeit-Analyse: An welchen Haltestellen stehen Busse ungewöhnlich lange?

Methodik:
Jedes Roh-Event in `raw_events` entspricht einem Update der
Fahrgastzähl-Anlage (open/close-Ereignis an Türen bzw. periodisches Update
während des Halts an einer Station). Aufeinanderfolgende Events desselben
Fahrzeugs an derselben `stationid` werden als EIN "Aufenthalt" (Visit)
zusammengefasst; die Standzeit ist die Differenz zwischen erstem und letztem
Event dieses Aufenthalts. Ein Aufenthalt endet, sobald das Fahrzeug bei der
nächsten Meldung an einer anderen Station auftaucht.

Achtung / Limitation (PoC): Diese Heuristik unterschätzt tendenziell echte
Standzeiten, wenn zwischen zwei Events an derselben Station kein Update kam
(z.B. bei geringer Update-Frequenz), und kann bei extrem kurzer Update-Lücke
+ erneuter Vorbeifahrt an derselben Haltestelle (z.B. Ringlinie) fälschlich
zwei Fahrten zusammenfassen, falls dazwischen keine andere Station geloggt
wurde. Für den PoC ausreichend, für Produktivbetrieb zu verfeinern.

Nutzung:
    python standzeit.py                      # Standard: >= 5 Minuten
    python standzeit.py --min-minutes 3
"""

import argparse
from datetime import datetime

from config import DB_PATH
from db import get_connection


MAX_GAP_SECONDS = 180  # Lücke zwischen zwei Events an derselben Haltestelle,
# ab der ein neuer Aufenthalt beginnt statt den alten fortzusetzen. Ohne das
# würden zwei getrennte Besuche (z.B. Bus verlässt die Haltestelle, fährt
# eine Runde und kommt Stunden später zurück) fälschlich zu einem einzigen,
# extrem langen "Aufenthalt" verschmolzen (beobachtet: bis zu 589 Min. bei
# tatsächlich nur wenigen Minuten echter Standzeit plus stundenlanger Lücke).


def compute_visits(conn):
    """Gruppiert raw_events zu Aufenthalten (Visits) pro Fahrzeug. Eine Lücke
    von mehr als MAX_GAP_SECONDS zwischen zwei Events an derselben
    Haltestelle beendet den Aufenthalt (siehe MAX_GAP_SECONDS).

    Gruppierung erfolgt über (vehicle, stationNAME), nicht stationid:
    Analyse vom 2026-09-12 ergab, dass `stationid` in der Live-Quelle NICHT
    zuverlässig pro Haltestelle vergeben ist (273 von 280 stationid-Werten
    hatten mehrere unterschiedliche stationnames; einige Fahrzeuge melden
    über ihre gesamte Laufzeit nur EINE stationid) — vermutlich eine
    geräte-/fahrzeugbezogene ID der Zählanlage, keine Orts-ID. `stationname`
    ist der verlässliche Ortsbezug (wird auch vom Matching in
    match_puenktlichkeit.py erfolgreich so verwendet, siehe README)."""
    rows = conn.execute(
        """
        SELECT vehicle, route, stationid, stationname, event_time
        FROM raw_events
        WHERE event_time IS NOT NULL AND stationname IS NOT NULL AND stationname != ''
        ORDER BY vehicle, event_time
        """
    ).fetchall()

    visits = []
    current = None
    for vehicle, route, stationid, stationname, event_time in rows:
        try:
            dt = datetime.strptime(event_time, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        same_stop = current and current["vehicle"] == vehicle and current["stationname"] == stationname
        gap_ok = same_stop and (dt - current["end"]).total_seconds() <= MAX_GAP_SECONDS
        if gap_ok:
            current["end"] = dt
            current["count"] += 1
        else:
            if current:
                visits.append(current)
            current = {
                "vehicle": vehicle,
                "route": route,
                "stationid": stationid,
                "stationname": stationname,
                "start": dt,
                "end": dt,
                "count": 1,
            }
    if current:
        visits.append(current)

    for v in visits:
        v["dwell_seconds"] = int((v["end"] - v["start"]).total_seconds())
    return visits


def store_visits(conn, visits):
    conn.execute("DELETE FROM dwell_events")  # bei jedem Lauf neu berechnen
    conn.executemany(
        """
        INSERT INTO dwell_events
            (vehicle, route, stationid, stationname, start_time, end_time, dwell_seconds, event_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                v["vehicle"],
                v["route"],
                v["stationid"],
                v["stationname"],
                v["start"].strftime("%Y-%m-%d %H:%M:%S"),
                v["end"].strftime("%Y-%m-%d %H:%M:%S"),
                v["dwell_seconds"],
                v["count"],
            )
            for v in visits
        ],
    )
    conn.commit()


def run(min_minutes: float):
    conn = get_connection(DB_PATH)
    visits = compute_visits(conn)
    store_visits(conn, visits)

    threshold = min_minutes * 60
    long_stops = sorted(
        (v for v in visits if v["dwell_seconds"] >= threshold),
        key=lambda v: v["dwell_seconds"],
        reverse=True,
    )

    print(f"Aufenthalte gesamt: {len(visits)}")
    print(f"Davon >= {min_minutes} Min. Standzeit: {len(long_stops)}\n")

    print(f"{'Haltestelle':<35} {'Linie':<6} {'Fahrzeug':<9} {'Start':<20} {'Dauer (Min.)'}")
    for v in long_stops[:50]:
        print(
            f"{v['stationname']:<35} {v['route']:<6} {v['vehicle']:<9} "
            f"{v['start'].strftime('%Y-%m-%d %H:%M:%S'):<20} {v['dwell_seconds'] / 60:.1f}"
        )

    # Aggregation je Haltestelle: wie oft und wie lange im Schnitt Standzeit >= Schwelle
    print(f"\n--- Aggregiert je Haltestelle (nur Aufenthalte >= {min_minutes} Min.) ---")
    by_station: dict[str, list] = {}
    for v in long_stops:
        by_station.setdefault(v["stationname"], []).append(v["dwell_seconds"])
    agg = sorted(by_station.items(), key=lambda kv: (len(kv[1]), sum(kv[1])), reverse=True)
    for name, durations in agg[:30]:
        avg_min = sum(durations) / len(durations) / 60
        max_min = max(durations) / 60
        print(f"{name:<35} Anzahl: {len(durations):<4} Ø {avg_min:.1f} Min.  Max {max_min:.1f} Min.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-minutes", type=float, default=5.0)
    args = parser.parse_args()
    run(args.min_minutes)


if __name__ == "__main__":
    main()
