"""
Extrahiert einen kompakten GTFS-Auszug für ausgewählte NEW-Testlinien aus dem
NRW-weiten GTFS-Feed (https://gtfs.openvrr.de/google_transit.zip), ohne das
komplette ~1.2GB-Feed (v.a. stop_times.txt) auf die Platte zu entpacken.

Liest direkt aus der ZIP-Datei (Streaming), filtert auf agency_id "new-80"
(NEW) und die angegebenen route_short_name-Werte, und schreibt einen kleinen
Satz CSVs nach data/gtfs_subset/, den extract_matching.py bzw. die spätere
Matching-Logik (Schritt 5) verwenden kann.

Nutzung:
    python extract_gtfs_subset.py --routes 002,004
"""

import argparse
import csv
import io
import zipfile
from pathlib import Path

GTFS_ZIP = "data/gtfs/google_transit.zip"
OUT_DIR = Path("data/gtfs_subset")
AGENCY_ID = "new-80"  # NEW, siehe agency.txt


def open_csv(zf: zipfile.ZipFile, name: str):
    raw = zf.open(name)
    text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
    return csv.DictReader(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--routes", required=True, help="Komma-getrennte route_short_name, z.B. 002,004")
    parser.add_argument("--zip", default=GTFS_ZIP)
    args = parser.parse_args()

    wanted_routes = set(r.strip() for r in args.routes.split(","))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(args.zip) as zf:
        # 1) routes.txt -> passende route_ids finden
        route_ids = {}
        reader = open_csv(zf, "routes.txt")
        out_routes = []
        for row in reader:
            if row["agency_id"] == AGENCY_ID and row["route_short_name"] in wanted_routes:
                route_ids[row["route_id"]] = row
                out_routes.append(row)
        print(f"Gefundene Routen: {[(r['route_id'], r['route_short_name']) for r in out_routes]}")
        if not route_ids:
            print("WARNUNG: Keine passenden Routen gefunden. Abbruch.")
            return
        write_csv(OUT_DIR / "routes.csv", out_routes)

        # 2) trips.txt -> trip_ids + service_ids für diese Routen
        reader = open_csv(zf, "trips.txt")
        trip_ids = set()
        service_ids = set()
        out_trips = []
        for row in reader:
            if row["route_id"] in route_ids:
                trip_ids.add(row["trip_id"])
                service_ids.add(row["service_id"])
                out_trips.append(row)
        print(f"Trips gefunden: {len(out_trips)}, Service-IDs: {len(service_ids)}")
        write_csv(OUT_DIR / "trips.csv", out_trips)

        # 3) stop_times.txt -> streaming filtern nach trip_ids (großer Datei!)
        reader = open_csv(zf, "stop_times.txt")
        out_stop_times = []
        stop_ids = set()
        for row in reader:
            if row["trip_id"] in trip_ids:
                out_stop_times.append(row)
                stop_ids.add(row["stop_id"])
        print(f"stop_times-Zeilen gefunden: {len(out_stop_times)}, referenzierte Stops: {len(stop_ids)}")
        write_csv(OUT_DIR / "stop_times.csv", out_stop_times)

        # 4) stops.txt -> nur referenzierte Haltestellen
        reader = open_csv(zf, "stops.txt")
        out_stops = [row for row in reader if row["stop_id"] in stop_ids]
        write_csv(OUT_DIR / "stops.csv", out_stops)

        # 5) calendar.txt / calendar_dates.txt -> nur referenzierte service_ids
        reader = open_csv(zf, "calendar.txt")
        out_calendar = [row for row in reader if row["service_id"] in service_ids]
        write_csv(OUT_DIR / "calendar.csv", out_calendar)

        reader = open_csv(zf, "calendar_dates.txt")
        out_calendar_dates = [row for row in reader if row["service_id"] in service_ids]
        write_csv(OUT_DIR / "calendar_dates.csv", out_calendar_dates)

    print(f"\nFertig. Auszug liegt in {OUT_DIR}/")


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
