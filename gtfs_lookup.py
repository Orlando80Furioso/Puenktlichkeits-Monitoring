"""
Lädt den kompakten GTFS-Auszug (data/gtfs_subset/, siehe extract_gtfs_subset.py)
und stellt Nachschlage-Strukturen für das Matching bereit:
- Haltestellennamen-Zuordnung (Live-Feed-Name -> GTFS stop_id) per Fuzzy-Match
- aktive service_ids für ein Datum (Kalender + Ausnahmen)
- geplante Ankunftszeiten je (route_id, stop_id)
"""

import csv
import difflib
import re
from datetime import date
from pathlib import Path

GTFS_SUBSET_DIR = Path("data/gtfs_subset")

WEEKDAY_FIELDS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _read_csv(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def gtfs_time_to_seconds(t: str) -> int:
    """GTFS-Zeit "HH:MM:SS" -> Sekunden seit Mitternacht. HH kann >=24 sein
    (Fahrten nach Mitternacht, gehören noch zum vorherigen Servicetag)."""
    h, m, s = (int(x) for x in t.strip().split(":"))
    return h * 3600 + m * 60 + s


class GtfsData:
    def __init__(self, subset_dir: Path = GTFS_SUBSET_DIR):
        self.routes = _read_csv(subset_dir / "routes.csv")
        self.trips = _read_csv(subset_dir / "trips.csv")
        self.stop_times = _read_csv(subset_dir / "stop_times.csv")
        self.stops = _read_csv(subset_dir / "stops.csv")
        self.calendar = _read_csv(subset_dir / "calendar.csv")
        self.calendar_dates = _read_csv(subset_dir / "calendar_dates.csv")

        # route_short_name -> route_id
        self.route_id_by_short_name = {r["route_short_name"]: r["route_id"] for r in self.routes}

        # trip_id -> {route_id, service_id}
        self.trip_info = {t["trip_id"]: t for t in self.trips}

        # stop_id -> stop_name
        self.stop_name_by_id = {s["stop_id"]: s["stop_name"] for s in self.stops}

        # (route_id) -> set(stop_id) tatsächlich bedient von dieser Route
        self.stops_by_route: dict[str, set] = {}
        # (route_id, stop_id) -> list[(trip_id, service_id, arrival_seconds, arrival_str)]
        self.schedule_by_route_stop: dict[tuple, list] = {}
        for st in self.stop_times:
            trip = self.trip_info.get(st["trip_id"])
            if not trip:
                continue
            route_id = trip["route_id"]
            stop_id = st["stop_id"]
            self.stops_by_route.setdefault(route_id, set()).add(stop_id)
            key = (route_id, stop_id)
            self.schedule_by_route_stop.setdefault(key, []).append(
                (st["trip_id"], trip["service_id"], gtfs_time_to_seconds(st["arrival_time"]), st["arrival_time"])
            )

        # service_id -> calendar row
        self.calendar_by_service = {c["service_id"]: c for c in self.calendar}
        # (service_id, date "YYYYMMDD") -> exception_type ("1"=added, "2"=removed)
        self.calendar_dates_by_service_date = {
            (c["service_id"], c["date"]): c["exception_type"] for c in self.calendar_dates
        }

        # Cache für Namens-Matching: (route_id, live_name) -> stop_id oder None
        self._name_match_cache: dict[tuple, str | None] = {}

    def active_service_ids(self, d: date) -> set:
        date_str = d.strftime("%Y%m%d")
        weekday_field = WEEKDAY_FIELDS[d.weekday()]
        active = set()
        for service_id, cal in self.calendar_by_service.items():
            if cal.get(weekday_field) == "1" and cal["start_date"] <= date_str <= cal["end_date"]:
                active.add(service_id)
        for (service_id, ex_date), ex_type in self.calendar_dates_by_service_date.items():
            if ex_date != date_str:
                continue
            if ex_type == "1":
                active.add(service_id)
            elif ex_type == "2":
                active.discard(service_id)
        return active

    def match_stop_by_name(self, route_id: str, live_name: str) -> str | None:
        """Findet die GTFS stop_id, deren Name am besten zu `live_name` passt,
        eingeschränkt auf Haltestellen, die tatsächlich von `route_id` bedient
        werden. Nutzt difflib (Sequenzähnlichkeit), Ergebnis wird gecacht."""
        cache_key = (route_id, live_name)
        if cache_key in self._name_match_cache:
            return self._name_match_cache[cache_key]

        candidate_ids = self.stops_by_route.get(route_id, set())
        candidates = {sid: self.stop_name_by_id[sid] for sid in candidate_ids}
        if not candidates:
            self._name_match_cache[cache_key] = None
            return None

        best_id, best_score = None, 0.0
        live_norm = _normalize(live_name)
        for sid, name in candidates.items():
            score = _similarity(live_norm, _normalize(name))
            if score > best_score:
                best_id, best_score = sid, score

        result = best_id if best_score >= 0.6 else None
        self._name_match_cache[cache_key] = result
        return result

    def scheduled_arrivals(self, route_id: str, stop_id: str, active_service_ids: set):
        """Liste (arrival_seconds, arrival_str, trip_id) für aktive Services."""
        entries = self.schedule_by_route_stop.get((route_id, stop_id), [])
        return [
            (secs, s, trip_id)
            for trip_id, service_id, secs, s in entries
            if service_id in active_service_ids
        ]


def _normalize(name: str) -> str:
    n = (
        name.lower()
        .replace("str.", "straße")
        .replace(".", "")
        .replace(",", "")
    )
    for prefix in ("mönchengladbach ", "mönchengl ", "mg "):
        if n.startswith(prefix):
            n = n[len(prefix):]
    # Bahnsteig-/Bussteig-Suffixe entfernen (z.B. "bstg 1", "bstg 2")
    n = re.sub(r"\bbstg\s*\d*\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def _similarity(a: str, b: str) -> float:
    """Ähnlichkeitsmaß, das kurze (abgekürzte) Live-Namen fair gegen lange
    offizielle GTFS-Namen bewertet: kombiniert die normale Sequenzähnlichkeit
    mit einem Containment-Score (längster gemeinsamer Block relativ zur
    KÜRZEREN der beiden Zeichenketten) — ein Live-Name, der als Teilstring
    im offiziellen Namen steckt (z.B. "Terminal" in "Flughafen Terminal"),
    bekommt so einen hohen Score statt durch die Längendifferenz bestraft
    zu werden."""
    sm = difflib.SequenceMatcher(None, a, b)
    ratio = sm.ratio()
    match = sm.find_longest_match(0, len(a), 0, len(b))
    shorter = max(1, min(len(a), len(b)))
    containment = match.size / shorter
    return max(ratio, containment)
