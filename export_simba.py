"""
Export des GESAMTEN NEW-Liniennetzes (nicht nur die 10 Testlinien des
Pünktlichkeits-Projekts) als SimBA-Fahrplan-CSV (Reiner-Lemoine-Institut,
https://rli-simba.readthedocs.io/en/dev/simulation_parameters.html).

WICHTIGE EINSCHRÄNKUNGEN (siehe auch docs/simba-export.md):
- Das ist ein SOLL-LINIENFAHRPLAN, KEIN echter Umlaufplan: GTFS enthält
  keine Fahrzeug-Block-/Umlaufdaten (kein `block_id`-Feld in trips.txt).
  `rotation_id` wird deshalb 1:1 aus der GTFS `trip_id` übernommen —
  jede Zeile ist EINE einzelne Fahrt, keine verkettete Fahrzeug-Umlaufkette.
- `distance`: Luftlinie zwischen den Haltestellen in Fahrtreihenfolge
  (Haversine), da `shapes.txt` im Feed leer ist und keine echte
  Streckengeometrie liefert. Reale Streckenlänge liegt i.d.R. höher.
- `vehicle_type`: Platzhalter ("solobus_12m"), da GTFS keine Fahrzeugtypen
  je Fahrt/Umlauf führt. Vor Nutzung in SimBA an echte Flottendaten anpassen.
- Exportiert wird EIN repräsentativer Tag (Default: nächster Mittwoch als
  typischer Schultag/Werktag), da SimBA pro Lauf einen Betriebstag simuliert
  und das Feed pro Linie viele unterschiedliche Kalendervarianten hat.

Nutzung:
    python export_simba.py                       # nächster Mittwoch
    python export_simba.py --date 2026-09-16
    python export_simba.py --date 2026-09-16 --out simba_new_2026-09-16.csv
"""

import argparse
import csv
import io
import math
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

GTFS_ZIP = "data/gtfs/google_transit.zip"
AGENCY_ID = "new-80"
VEHICLE_TYPE_PLACEHOLDER = "solobus_12m"
WEEKDAY_FIELDS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def open_csv(zf: zipfile.ZipFile, name: str):
    raw = zf.open(name)
    text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
    return csv.DictReader(text)


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def next_weekday(target_weekday: int, from_date: date) -> date:
    """target_weekday: 0=Montag ... 6=Sonntag."""
    days_ahead = (target_weekday - from_date.weekday()) % 7
    return from_date + timedelta(days=days_ahead or 7 if from_date.weekday() == target_weekday else days_ahead)


def gtfs_time_to_timedelta(t: str) -> timedelta:
    h, m, s = (int(x) for x in t.strip().split(":"))
    return timedelta(hours=h, minutes=m, seconds=s)


def active_service_ids(calendar: dict, calendar_dates: list, d: date) -> set:
    date_str = d.strftime("%Y%m%d")
    weekday_field = WEEKDAY_FIELDS[d.weekday()]
    active = {sid for sid, c in calendar.items() if c.get(weekday_field) == "1" and c["start_date"] <= date_str <= c["end_date"]}
    for c in calendar_dates:
        if c["date"] != date_str:
            continue
        if c["exception_type"] == "1":
            active.add(c["service_id"])
        elif c["exception_type"] == "2":
            active.discard(c["service_id"])
    return active


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD, Default: nächster Mittwoch")
    parser.add_argument("--zip", default=GTFS_ZIP)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    if args.date:
        ref_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        ref_date = next_weekday(2, date.today())  # 2 = Mittwoch

    out_path = Path(args.out) if args.out else Path(f"simba_new_linienfahrplan_{ref_date.isoformat()}.csv")

    print(f"Referenztag: {ref_date.isoformat()} ({ref_date.strftime('%A')})")

    with zipfile.ZipFile(args.zip) as zf:
        route_short_by_id = {
            r["route_id"]: r["route_short_name"]
            for r in open_csv(zf, "routes.txt")
            if r["agency_id"] == AGENCY_ID
        }
        print(f"NEW-Linien im Feed: {len(route_short_by_id)}")

        trips = [t for t in open_csv(zf, "trips.txt") if t["route_id"] in route_short_by_id]
        print(f"Trips gesamt (alle Kalendertage): {len(trips)}")

        calendar = {c["service_id"]: c for c in open_csv(zf, "calendar.txt")}
        calendar_dates = [c for c in open_csv(zf, "calendar_dates.txt")]
        # Nur auf die für unsere Trips relevanten service_ids einschränken (Speicher/Zeit sparen)
        relevant_service_ids = {t["service_id"] for t in trips}
        calendar = {k: v for k, v in calendar.items() if k in relevant_service_ids}
        calendar_dates = [c for c in calendar_dates if c["service_id"] in relevant_service_ids]

        active = active_service_ids(calendar, calendar_dates, ref_date)
        print(f"Aktive service_ids am {ref_date.isoformat()}: {len(active)}")

        active_trips = {t["trip_id"]: t for t in trips if t["service_id"] in active}
        print(f"Aktive Trips am {ref_date.isoformat()}: {len(active_trips)}")

        stops = {s["stop_id"]: (float(s["stop_lat"]), float(s["stop_lon"]), s["stop_name"]) for s in open_csv(zf, "stops.txt")}

        stop_times_by_trip = defaultdict(list)
        for st in open_csv(zf, "stop_times.txt"):
            if st["trip_id"] in active_trips:
                stop_times_by_trip[st["trip_id"]].append(st)

    rows = []
    for trip_id, trip in active_trips.items():
        sts = sorted(stop_times_by_trip.get(trip_id, []), key=lambda r: int(r["stop_sequence"]))
        if len(sts) < 2:
            continue
        first, last = sts[0], sts[-1]
        if first["stop_id"] not in stops or last["stop_id"] not in stops:
            continue

        dep_dt = datetime.combine(ref_date, datetime.min.time()) + gtfs_time_to_timedelta(first["departure_time"])
        arr_dt = datetime.combine(ref_date, datetime.min.time()) + gtfs_time_to_timedelta(last["arrival_time"])

        distance_m = 0.0
        prev = None
        for st in sts:
            if st["stop_id"] not in stops:
                continue
            lat, lon, _ = stops[st["stop_id"]]
            if prev:
                distance_m += haversine_m(prev[0], prev[1], lat, lon)
            prev = (lat, lon)

        rows.append({
            "rotation_id": trip_id,
            "departure_name": stops[first["stop_id"]][2],
            "departure_time": dep_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "arrival_name": stops[last["stop_id"]][2],
            "arrival_time": arr_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "distance": round(distance_m),
            "vehicle_type": VEHICLE_TYPE_PLACEHOLDER,
            "line": route_short_by_id.get(trip["route_id"], ""),
        })

    rows.sort(key=lambda r: r["departure_time"])

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "rotation_id", "departure_name", "departure_time", "arrival_name",
            "arrival_time", "distance", "vehicle_type", "line",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nGeschrieben: {out_path.resolve()} ({len(rows)} Fahrten)")


if __name__ == "__main__":
    main()
