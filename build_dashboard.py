"""
Schritt 6: Baut eine eigenständige lokale HTML-Karte (dashboard.html) aus
den Daten in data/puenktlichkeit.db + data/gtfs_subset/.

- Leaflet.js-Karte (OpenStreetMap-Tiles), Linien einzeln ein-/ausblendbar
- Haltestellen eingefärbt grün->gelb->rot nach Ø-Verspätung zum per
  Zeitschieber gewählten Zeitpunkt (echte Aufzeichnungs-Zeitachse,
  30-Sek.-Schritte)
- Busposition zum gewählten Zeitpunkt aus den geloggten Daten (raw_json
  enthält lat/lon), gestrichelter Marker-Rand
- Optionale echte Live-Verfolgung (durchgezogener Marker-Rand): verbindet
  sich bei Aktivierung client-seitig per SSE direkt mit der externen Quelle
- Blaue Corona an Haltestellen mit Standzeit >= 5/10 Min. (aus dwell_events)
- ⚡-Symbol am bekannten Pantograph-Standort
- Läuft komplett offline im Browser (außer Kartenkacheln von OSM und der
  optionalen Live-Verfolgung); keine Server-Komponente nötig.

Nutzung:
    python build_dashboard.py
    -> erzeugt dashboard.html im Projektordner, einfach per Doppelklick
       im Browser öffnen (funktioniert auf Desktop wie auf dem Handy).

    python build_dashboard.py --public
    -> erzeugt dashboard_public.html: OHNE Live-Verfolgung (kein Code, der
       sich mit der externen Firebase-Quelle verbindet) und OHNE
       eingebettete Positions-Historie (keine rohen GPS-Daten). Für die
       Veröffentlichung gedacht (siehe README "Veröffentlichung") — die
       volle Version mit beidem bleibt ausschließlich lokal.
"""

import argparse
import csv
import json
import math
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

from config import DB_PATH, SSE_URL as LIVE_SOURCE_URL
from db import get_connection
from gtfs_lookup import GtfsData

GTFS_SUBSET_DIR = Path("data/gtfs_subset")
OUT_FILE = Path("dashboard.html")
PUBLIC_OUT_FILE = Path("dashboard_public.html")
OSRM_CACHE_FILE = Path("data/osrm_cache.json")
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson"

# Echte Streckengeometrie aus OpenStreetMap-Routenrelationen (route=bus),
# deutlich präziser als die OSRM-Näherung (siehe OSM_CACHE_FILE). GTFS von
# NEW/VRR liefert keine shapes.txt (weder NRW-weites Feed noch VRR- noch
# gtfs.de-Bundesfeed, geprüft 2026-09-14) -> OSM-Relationen sind die beste
# verfügbare Quelle. Öffentliche Overpass-API, kein Auth nötig.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_CACHE_FILE = Path("data/osm_route_cache.json")
OSM_OPERATOR_HINT = "NEW mobil und aktiv"
# Overpass' Apache-Setup lehnt den Standard-User-Agent von requests
# ("python-requests/x.x") mit 406 Not Acceptable ab -> eigenen setzen.
OVERPASS_HEADERS = {"User-Agent": "NEW-Puenktlichkeits-PoC/1.0 (lokales Hobbyprojekt)"}

# Gut unterscheidbare Farben für bis zu 10 Linien (Sasha-Trubetskoy-Palette, Auszug)
LINE_COLORS = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#9A6324", "#000075", "#808000",
]

DELAY_MIN_FOR_RED = 8.0  # ab dieser Ø-Verspätung (Min.) volle Rot-Färbung
BUCKET_SECONDS = 300  # Granularität der Pünktlichkeits-Einfärbung (5 Min.)
POSITION_STEP_SECONDS = 30  # Granularität des Zeit-Schiebereglers/der Busposition
POSITION_STALE_SECONDS = 600  # Fahrzeug ausblenden, wenn letzte Position älter ist
POSITION_MOVE_EPSILON_DEG = 0.00005  # ~5m; kleinere Bewegungen werden beim Ausdünnen ignoriert

# Bekannte Pantograph-Ladestandorte (siehe docs/pantographen-standorte.md).
# Nur Künkelstraße ist namentlich bestätigt; die übrigen 6 der insgesamt 7
# Pantographen auf der Strecke MG-Viersen sind öffentlich nicht dokumentiert.
# Substring-Match (kleingeschrieben) gegen stop_name.
PANTOGRAPH_STOP_NAME_QUERIES = ["künkelstra"]


def load_stop_coords():
    with open(GTFS_SUBSET_DIR / "stops.csv", encoding="utf-8") as f:
        return {
            r["stop_id"]: (float(r["stop_lat"]), float(r["stop_lon"]), r["stop_name"])
            for r in csv.DictReader(f)
        }


def build_line_paths(gtfs: GtfsData, route_ids: list[str]):
    """Pro Linie: die Trip-Variante mit den meisten Haltestellen als
    repräsentative Streckenführung (GTFS shapes.txt ist im Feed leer)."""
    best_trip_by_route: dict[str, tuple[str, int]] = {}
    stop_count_by_trip: dict[str, int] = defaultdict(int)
    for st in gtfs.stop_times:
        stop_count_by_trip[st["trip_id"]] += 1

    for trip_id, trip in gtfs.trip_info.items():
        route_id = trip["route_id"]
        if route_id not in route_ids:
            continue
        n = stop_count_by_trip.get(trip_id, 0)
        if route_id not in best_trip_by_route or n > best_trip_by_route[route_id][1]:
            best_trip_by_route[route_id] = (trip_id, n)

    stops_by_trip: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for st in gtfs.stop_times:
        stops_by_trip[st["trip_id"]].append((int(st["stop_sequence"]), st["stop_id"]))

    paths = {}
    for route_id, (trip_id, _) in best_trip_by_route.items():
        ordered = [sid for _, sid in sorted(stops_by_trip.get(trip_id, []))]
        paths[route_id] = ordered
    return paths


def _load_osrm_cache() -> dict:
    if OSRM_CACHE_FILE.exists():
        try:
            return json.loads(OSRM_CACHE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_osrm_cache(cache: dict) -> None:
    OSRM_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    OSRM_CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")


def fetch_segment_latlon(lat1: float, lon1: float, lat2: float, lon2: float, cache: dict) -> list[list[float]]:
    """Straßenführung zwischen zwei Koordinaten über OSRM. Gecacht pro
    gerundetem Koordinatenpaar in data/osrm_cache.json (linien-übergreifend
    nutzbar, auch zum Stopfen von Lücken in OSM-Routenrelationen)."""
    straight = [[lat1, lon1], [lat2, lon2]]
    key = f"{lat1:.6f},{lon1:.6f}|{lat2:.6f},{lon2:.6f}"
    if key in cache:
        return cache[key]

    try:
        coord_str = f"{lon1},{lat1};{lon2},{lat2}"
        resp = requests.get(OSRM_URL.format(coords=coord_str), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            raise ValueError(str(data.get("code")))
        path = [[lat, lon] for lon, lat in data["routes"][0]["geometry"]["coordinates"]]
    except (requests.exceptions.RequestException, ValueError, KeyError):
        path = straight  # Lücke im Straßennetz o.ä. -> nur dieses Teilstück als Luftlinie

    cache[key] = path
    time.sleep(0.1)  # fair use des öffentlichen Demo-Servers
    return path


def fetch_segment(a: str, b: str, coords: dict, cache: dict) -> list[list[float]]:
    """Straßenführung zwischen ZWEI benachbarten Haltestellen über OSRM.
    Segment-weise statt als eine große Kette, damit eine einzelne Netzlücke
    (z.B. eine Haltestelle ohne sauberen Straßenanschluss im OSM-Graphen)
    nicht die ganze Linie auf Luftlinie zurückfallen lässt."""
    lat1, lon1, _ = coords[a]
    lat2, lon2, _ = coords[b]
    return fetch_segment_latlon(lat1, lon1, lat2, lon2, cache)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def patch_path_gaps(path: list[list[float]], cache: dict, threshold_m: float = 150.0) -> list[list[float]]:
    """Schließt Lücken in einer Streckengeometrie, die größer als
    threshold_m sind, mit OSRM-Straßenrouting. Notwendig bei OSM-
    Routenrelationen, die (wie bei Linie 033 beobachtet) unvollständig
    gemappt sind: fehlende Way-Segmente führen sonst zu einer geraden
    "Teleport"-Linie zwischen zwei entfernten Punkten statt einer
    Straßenführung, und lassen dabei ggf. dazwischenliegende Haltestellen
    unangebunden."""
    if len(path) < 2:
        return path
    patched = [path[0]]
    n_gaps = 0
    for prev, cur in zip(path, path[1:]):
        d = haversine_m(prev[0], prev[1], cur[0], cur[1])
        if d > threshold_m:
            n_gaps += 1
            fill = fetch_segment_latlon(prev[0], prev[1], cur[0], cur[1], cache)
            patched.extend(fill[1:])
        else:
            patched.append(cur)
    if n_gaps:
        print(f"    {n_gaps} Lücke(n) in der OSM-Geometrie mit OSRM-Routing geschlossen")
    return patched


def _load_osm_cache() -> dict:
    if OSM_CACHE_FILE.exists():
        try:
            return json.loads(OSM_CACHE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_osm_cache(cache: dict) -> None:
    OSM_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    OSM_CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")


def _stitch_osm_ways(ways: list[dict]) -> list[list[float]]:
    """Reiht die Way-Segmente einer OSM-Routenrelation zu einer
    durchgehenden Koordinatenliste auf. OSM-Relationen garantieren keine
    einheitliche Richtung je Way, deshalb wird pro Segment per
    Endpunkt-Nähe entschieden, ob es umgedreht werden muss."""
    coords: list[list[float]] = []
    for way in ways:
        pts = [[p["lat"], p["lon"]] for p in way.get("geometry") or [] if p]
        if not pts:
            continue
        if not coords:
            coords.extend(pts)
            continue
        last = coords[-1]

        def dist(a, b):
            return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2

        if dist(last, pts[-1]) < dist(last, pts[0]):
            pts = list(reversed(pts))
        if coords[-1] == pts[0]:
            pts = pts[1:]
        coords.extend(pts)
    return coords


def _overpass_post(query: str, timeout: float, attempt: int = 1, max_attempts: int = 3):
    """POST an Overpass mit Retry/Backoff bei 429/504 (öffentlicher Server,
    gelegentlich überlastet oder eine einzelne Relation ungewöhnlich
    langsam). Gibt das Response-Objekt zurück oder None nach Ausschöpfen
    der Versuche."""
    resp = requests.post(OVERPASS_URL, data={"data": query}, headers=OVERPASS_HEADERS, timeout=timeout)
    if resp.status_code in (429, 504) and attempt < max_attempts:
        wait = 15 * attempt
        time.sleep(wait)
        return _overpass_post(query, timeout, attempt + 1, max_attempts)
    return resp


def fetch_osm_route_geometry(route_short: str, cache: dict) -> list[list[float]] | None:
    """Holt die echte Streckengeometrie einer NEW-Buslinie aus OpenStreetMap
    (route=bus-Relation, siehe OSM_CACHE_FILE-Kommentar). Zweistufig:
    zuerst schnelle Tags-only-Abfrage für die Kandidaten-Relationen (Hin/
    Rück/Teilstrecken), dann Geometrie JE Relation EINZELN abgerufen. Das
    ist robuster als eine gemeinsame Abfrage: eine einzelne ungewöhnlich
    langsame/kaputte Relation (beobachtet: eine Linie-003-Variante brauchte
    serverseitig >50s und ließ dadurch die ganze Linie scheitern) blockiert
    so nicht mehr die anderen Varianten. Gewählt wird die Variante mit den
    meisten Way-Segmenten (= vollständigste) unter den erfolgreich
    abgerufenen. Gibt None zurück, wenn nichts gefunden wird oder alle
    Varianten fehlschlagen (Aufrufer fällt dann auf OSRM zurück)."""
    if route_short in cache:
        return cache[route_short]

    try:
        tag_query = (
            "[out:json][timeout:25][bbox:51.05,6.20,51.40,6.65];"
            f'relation["route"="bus"]["ref"="{route_short}"]["operator"~"{OSM_OPERATOR_HINT}"];'
            "out ids;"
        )
        resp = _overpass_post(tag_query, timeout=40)
        resp.raise_for_status()
        rel_ids = [r["id"] for r in resp.json().get("elements", [])]
        if not rel_ids:
            cache[route_short] = None
            return None
        time.sleep(1)

        best_path: list[list[float]] | None = None
        best_len = 0
        for rid in rel_ids:
            geom_query = f"[out:json][timeout:45];relation({rid});out geom;"
            try:
                resp = _overpass_post(geom_query, timeout=60, max_attempts=1)  # eine langsame Variante nicht mehrfach abwarten
                resp.raise_for_status()
                elements = resp.json().get("elements", [])
                if not elements:
                    continue
                ways = [m for m in elements[0].get("members", []) if m.get("type") == "way"]
                if len(ways) > best_len:
                    best_path = _stitch_osm_ways(ways)
                    best_len = len(ways)
            except (requests.exceptions.RequestException, ValueError, KeyError):
                continue  # diese Variante überspringen, nächste probieren
            time.sleep(1.5)

        result = best_path if best_path and len(best_path) >= 2 else None
        cache[route_short] = result
        return result
    except (requests.exceptions.RequestException, ValueError, KeyError) as exc:
        print(f"  WARNUNG: OSM-Routengeometrie für Linie {route_short} fehlgeschlagen ({exc}), nutze OSRM-Fallback.")
        return None


def fetch_road_path(route_short: str, stop_seq: list[str], coords: dict, cache: dict) -> list[list[float]]:
    """Verkettet die Straßenführung einer ganzen Linie aus einzelnen
    Segmenten zwischen aufeinanderfolgenden Haltestellen (siehe
    fetch_segment)."""
    seq = [sid for sid in stop_seq if sid in coords]
    if len(seq) < 2:
        return [[coords[sid][0], coords[sid][1]] for sid in seq]

    full_path: list[list[float]] = []
    for a, b in zip(seq, seq[1:]):
        seg = fetch_segment(a, b, coords, cache)
        if full_path:
            full_path.extend(seg[1:])
        else:
            full_path.extend(seg)
    return full_path


def get_timeline_bounds(conn):
    row = conn.execute(
        "SELECT MIN(event_time), MAX(event_time) FROM raw_events WHERE event_time IS NOT NULL"
    ).fetchone()
    return (
        datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S"),
        datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S"),
    )


def build_position_history(conn, min_ts: datetime) -> dict:
    """Baut je Fahrzeug eine zeitlich sortierte Liste von Positionen aus dem
    gespeicherten `raw_json` (lat/lon stehen nicht in eigenen Spalten, siehe
    README "Bekannte offene Fragen"). Nahezu unveränderte Positionen
    (< ~5m Bewegung) werden nicht erneut gespeichert, sondern übernehmen den
    Zeitraum des vorherigen Punkts — hält die eingebettete Datenmenge klein,
    ohne dass Playback-Abfragen ("Position zum Zeitpunkt t") dadurch
    Löcher bekommen (der erste Zeitpunkt einer Position bleibt erhalten und
    gilt bis zur nächsten tatsächlich unterschiedlichen Position)."""
    cur = conn.execute(
        """
        SELECT vehicle, route, event_time, raw_json FROM raw_events
        WHERE event_time IS NOT NULL
        ORDER BY vehicle, event_time
        """
    )
    history = defaultdict(list)
    last_pos = {}
    skipped = 0
    for vehicle, route, event_time, raw_json in cur:
        try:
            obj = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            continue
        lat, lon = obj.get("lat"), obj.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            skipped += 1
            continue
        dt = datetime.strptime(event_time, "%Y-%m-%d %H:%M:%S")
        offset = round((dt - min_ts).total_seconds())
        prev = last_pos.get(vehicle)
        if (
            prev
            and abs(prev[0] - lat) < POSITION_MOVE_EPSILON_DEG
            and abs(prev[1] - lon) < POSITION_MOVE_EPSILON_DEG
        ):
            continue  # keine relevante Bewegung -> vorherigen Punkt nicht duplizieren
        history[vehicle].append([offset, round(lat, 5), round(lon, 5), route])
        last_pos[vehicle] = (lat, lon)

    total_points = sum(len(v) for v in history.values())
    print(
        f"Positionsverlauf: {total_points} Punkte über {len(history)} Fahrzeuge "
        f"({skipped} Events ohne lat/lon übersprungen)"
    )
    return dict(history)


def build_delay_history(conn, min_ts: datetime) -> dict:
    """Baut je Fahrzeug eine zeitlich sortierte Liste (Zeit-Offset,
    Verspätung in Minuten) aus der `matches`-Tabelle — genutzt, um am
    Bus-Symbol die zuletzt bekannte Verspätung anzuzeigen (JS sucht sich
    per Zeitschieber-Position den nächstgelegenen zurückliegenden Wert,
    analog zu build_position_history)."""
    cur = conn.execute(
        """
        SELECT r.vehicle, m.actual_time, m.delay_seconds
        FROM matches m JOIN raw_events r ON r.id = m.raw_event_id
        WHERE m.actual_time IS NOT NULL
        ORDER BY r.vehicle, m.actual_time
        """
    )
    history = defaultdict(list)
    for vehicle, actual_time, delay_seconds in cur:
        dt = datetime.strptime(actual_time, "%Y-%m-%d %H:%M:%S")
        offset = round((dt - min_ts).total_seconds())
        history[vehicle].append([offset, round(delay_seconds / 60.0, 1)])
    print(f"Verspätungs-Historie: {sum(len(v) for v in history.values())} Werte über {len(history)} Fahrzeuge")
    return dict(history)


def strip_marked_block(html: str, start_marker: str, end_marker: str) -> str:
    """Entfernt einen mit `// START`/`// END`- bzw. `<!-- START -->`/
    `<!-- END -->`-Kommentaren markierten Abschnitt (inkl. der Marker
    selbst) aus dem generierten HTML — genutzt für den --public-Build, um
    Live-Verfolgung/Positions-Historie komplett aus der veröffentlichten
    Datei rauszuschneiden, nicht nur zu verstecken."""
    pattern = re.compile(
        r"(<!--\s*" + re.escape(start_marker) + r"\s*-->|//\s*" + re.escape(start_marker)
        + r"|/\*\s*" + re.escape(start_marker) + r"\s*\*/)"
        r".*?"
        r"(<!--\s*" + re.escape(end_marker) + r"\s*-->|//\s*" + re.escape(end_marker)
        + r"|/\*\s*" + re.escape(end_marker) + r"\s*\*/)",
        re.DOTALL,
    )
    stripped, n = pattern.subn("", html)
    if n == 0:
        raise RuntimeError(f"Marker {start_marker}/{end_marker} nicht gefunden — Template geändert?")
    return stripped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--public", action="store_true",
        help="Baut dashboard_public.html ohne Live-Verfolgung und ohne Positions-Historie (siehe README).",
    )
    args = parser.parse_args()

    conn = get_connection(DB_PATH)
    gtfs = GtfsData()
    coords = load_stop_coords()

    min_ts, max_ts = get_timeline_bounds(conn)
    print(f"Zeitachse: {min_ts} bis {max_ts} ({(max_ts - min_ts).total_seconds() / 3600:.1f}h)")
    # Historische Positions-Wiedergabe ist auch im --public-Build enthalten
    # (bewusste Entscheidung, siehe README "Hinweis zur Live-Datenquelle") —
    # nur die LIVE-Verfolgung (Verbindung zur externen Quelle) wird entfernt.
    position_history = build_position_history(conn, min_ts)
    delay_history = build_delay_history(conn, min_ts)

    route_short_to_id = gtfs.route_id_by_short_name
    route_id_to_short = {v: k for k, v in route_short_to_id.items()}
    route_ids = list(route_short_to_id.values())

    line_paths = build_line_paths(gtfs, route_ids)
    osrm_cache = _load_osrm_cache()
    osm_cache = _load_osm_cache()

    # --- Linien + zugehörige Haltestellen ---
    lines = {}
    all_stop_ids: set[str] = set()
    for i, (short, route_id) in enumerate(sorted(route_short_to_id.items())):
        stop_seq = line_paths.get(route_id, [])
        print(f"Hole Streckengeometrie für Linie {short} ({len(stop_seq)} Haltestellen) ...")
        path_latlon = fetch_osm_route_geometry(short, osm_cache)
        if path_latlon:
            print(f"  -> OSM-Routenrelation genutzt ({len(path_latlon)} Punkte)")
            path_latlon = patch_path_gaps(path_latlon, osrm_cache)
        else:
            print("  -> keine OSM-Relation gefunden, nutze OSRM-Straßenrouting je Haltestellen-Segment")
            path_latlon = fetch_road_path(short, stop_seq, coords, osrm_cache)
        route_row = next((r for r in gtfs.routes if r["route_id"] == route_id), None)
        long_name = route_row["route_long_name"] if route_row else short
        lines[short] = {
            "color": LINE_COLORS[i % len(LINE_COLORS)],
            "long_name": long_name,
            "path": path_latlon,
            "stops": [sid for sid in gtfs.stops_by_route.get(route_id, []) if sid in coords],
        }
        all_stop_ids.update(lines[short]["stops"])

    # --- Haltestellen-Stammdaten ---
    stops = {
        sid: {"name": coords[sid][2], "lat": coords[sid][0], "lon": coords[sid][1]}
        for sid in all_stop_ids
    }

    # --- Bekannte Pantograph-Ladestandorte (siehe docs/pantographen-standorte.md) ---
    pantograph_stop_ids = [
        sid
        for sid, s in stops.items()
        if any(q in s["name"].lower() for q in PANTOGRAPH_STOP_NAME_QUERIES)
    ]

    # --- Pünktlichkeit je (Linie, Haltestelle, Zeit-Slot) ---
    # Zeit-Slot = absolute Sekunden seit min_ts // BUCKET_SECONDS, verankert
    # auf der echten aufgezeichneten Zeitachse (nicht mehr "Uhrzeit
    # unabhängig vom Kalendertag") -> eindeutig mit der Busposition zum
    # selben Zeitpunkt verknüpfbar (siehe position_history/Zeitschieber).
    punctuality = defaultdict(lambda: defaultdict(lambda: {"sum_delay": 0.0, "count": 0}))
    for route, stop_id, actual_time, delay_seconds in conn.execute(
        "SELECT route, stop_id, actual_time, delay_seconds FROM matches WHERE stop_id IS NOT NULL"
    ):
        if not actual_time:
            continue
        dt = datetime.strptime(actual_time, "%Y-%m-%d %H:%M:%S")
        slot = int((dt - min_ts).total_seconds()) // BUCKET_SECONDS
        bucket = punctuality[route].setdefault(stop_id, {})
        hb = bucket.setdefault(slot, {"sum_delay": 0.0, "count": 0})
        hb["sum_delay"] += delay_seconds / 60.0
        hb["count"] += 1

    punctuality_out = {
        route: {
            stop_id: {
                str(slot): {
                    "avg_delay_min": round(v["sum_delay"] / v["count"], 2),
                    "count": v["count"],
                }
                for slot, v in slots.items()
            }
            for stop_id, slots in stops_dict.items()
        }
        for route, stops_dict in punctuality.items()
    }

    # --- Standzeiten (für den 5/10-Min.-Schalter im Dashboard) ---
    # Alle Einzeldauern ab 5 Min. werden mitgegeben (nicht nur ein fixes
    # Flag), damit der Schwellwert im Browser umgeschaltet werden kann,
    # ohne das Dashboard neu zu bauen.
    MIN_DWELL_MINUTES_STORED = 5
    dwell_durations = defaultdict(lambda: defaultdict(list))  # route -> stop_id -> [minuten, ...]
    for route, stationname, dwell_seconds in conn.execute(
        "SELECT route, stationname, dwell_seconds FROM dwell_events WHERE dwell_seconds >= ?",
        (MIN_DWELL_MINUTES_STORED * 60,),
    ):
        route_id = route_short_to_id.get(route)
        if not route_id:
            continue
        stop_id = gtfs.match_stop_by_name(route_id, stationname)
        if not stop_id:
            continue
        dwell_durations[route][stop_id].append(round(dwell_seconds / 60.0, 1))

    data = {
        "lines": lines,
        "stops": stops,
        "punctuality": punctuality_out,
        "dwell_durations": dwell_durations,
        "min_dwell_minutes_stored": MIN_DWELL_MINUTES_STORED,
        "pantograph_stops": pantograph_stop_ids,
        "delay_min_for_red": DELAY_MIN_FOR_RED,
        "bucket_seconds": BUCKET_SECONDS,
        "position_step_seconds": POSITION_STEP_SECONDS,
        "position_stale_seconds": POSITION_STALE_SECONDS,
        "timeline_min_iso": min_ts.strftime("%Y-%m-%dT%H:%M:%S"),
        "timeline_max_offset": int((max_ts - min_ts).total_seconds()),
        "position_history": position_history,
        "delay_history": delay_history,
        "generated_at": conn.execute("SELECT datetime('now')").fetchone()[0] + " UTC",
    }

    print(f"Pantograph-Standorte markiert: {len(pantograph_stop_ids)} ({[stops[s]['name'] for s in pantograph_stop_ids]})")

    _save_osrm_cache(osrm_cache)
    _save_osm_cache(osm_cache)

    html = TEMPLATE.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__LIVE_SSE_URL__", LIVE_SOURCE_URL)
    if args.public:
        html = strip_marked_block(html, "LIVE_SECTION_START", "LIVE_SECTION_END")
        html = strip_marked_block(html, "LIVE_LEGEND_START", "LIVE_LEGEND_END")
        html = strip_marked_block(html, "LIVE_CSS_START", "LIVE_CSS_END")
        html = strip_marked_block(html, "LIVE_JS_START", "LIVE_JS_END")
        out_path = PUBLIC_OUT_FILE
    else:
        out_path = OUT_FILE
    out_path.write_text(html, encoding="utf-8")
    print(f"Dashboard geschrieben: {out_path.resolve()}")
    print(f"Linien: {len(lines)}, Haltestellen: {len(stops)}")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEW Pünktlichkeit &ndash; Karte</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css" />
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
  html, body { margin:0; padding:0; height:100%; font-family: system-ui, sans-serif; background:#f4f4f4; }
  #app { display:flex; height:100vh; width:100vw; }
  #sidebar {
    width: 300px; min-width: 300px; background:#1c2733; color:#eee;
    padding: 14px 16px; overflow-y:auto; box-sizing:border-box;
  }
  #sidebar h1 { font-size: 16px; margin: 0 0 4px; }
  #sidebar .sub { font-size: 11px; color:#9aa7b4; margin-bottom: 14px; }
  .section { margin-bottom: 18px; }
  .section h2 { font-size: 12px; text-transform:uppercase; letter-spacing:.05em; color:#9aa7b4; margin:0 0 8px; }
  .line-row { display:flex; align-items:center; gap:8px; padding:4px 0; font-size:13px; cursor:pointer; }
  .line-row input { cursor:pointer; }
  .swatch { width:14px; height:14px; border-radius:3px; flex-shrink:0; }
  #hourLabel { font-weight:600; font-size:20px; }
  input[type=range] { width:100%; }
  .legend-row { display:flex; align-items:center; gap:8px; font-size:12px; margin:3px 0; }
  .legend-dot { width:12px; height:12px; border-radius:50%; flex-shrink:0; }
  #map { flex:1; }
  .stop-corona {
    position:absolute; top:50%; left:50%; width:34px; height:34px;
    margin-left:-17px; margin-top:-17px; border-radius:50%; pointer-events:none;
    background: radial-gradient(circle, rgba(6,20,60,0.9) 0%, rgba(60,140,255,0.65) 55%, rgba(140,200,255,0) 100%);
  }
  .legend-corona {
    width:20px; height:20px; border-radius:50%; flex-shrink:0;
    background: radial-gradient(circle, rgba(6,20,60,0.9) 0%, rgba(60,140,255,0.65) 55%, rgba(140,200,255,0) 100%);
  }
  .pantograph-badge {
    position:absolute; bottom:-8px; right:-8px; width:16px; height:16px; line-height:16px;
    text-align:center; font-size:11px; border-radius:50%; background:#ffb703; color:#1c2733;
    border:1px solid #7a5200; box-shadow:0 0 2px rgba(0,0,0,.6);
  }
  .legend-pantograph {
    width:18px; height:18px; line-height:18px; text-align:center; font-size:12px;
    border-radius:50%; background:#ffb703; color:#1c2733; border:1px solid #7a5200; flex-shrink:0;
  }
  .legend-bus { width:18px; text-align:center; flex-shrink:0; }
  /* LIVE_CSS_START */
  #liveToggle.live-active { background:#c62828; border-color:#c62828; color:#fff; }
  #liveToggle.live-active .live-dot { color:#ffb3b3; }
  /* LIVE_CSS_END */
  .live-bus-icon {
    width:22px; height:22px; border-radius:50%; display:flex; align-items:center; justify-content:center;
    font-size:13px; border:2px solid #fff; box-shadow:0 1px 3px rgba(0,0,0,.6);
  }
  .hist-bus-icon {
    width:20px; height:20px; border-radius:50%; display:flex; align-items:center; justify-content:center;
    font-size:12px; border:2px dashed #fff; box-shadow:0 1px 3px rgba(0,0,0,.6); opacity:0.9;
  }
  .hist-bus-delay {
    position:absolute; bottom:-7px; right:-9px; min-width:16px; height:14px; padding:0 2px;
    border-radius:7px; color:#fff; font-size:10px; font-weight:600; line-height:14px; text-align:center;
    border:1px solid #fff; box-shadow:0 1px 2px rgba(0,0,0,.6);
  }
  .legend-bus.hist { opacity:0.9; }
  .leaflet-popup-content { font-size:13px; }
  .btnrow { display:flex; gap:6px; margin-top:6px; }
  .btnrow button {
    flex:1; background:#2c3b4c; color:#eee; border:1px solid #3a4c60; border-radius:4px;
    padding:4px 0; font-size:11px; cursor:pointer;
  }
  .btnrow button:hover { background:#3a4c60; }
  .btnrow select {
    background:#2c3b4c; color:#eee; border:1px solid #3a4c60; border-radius:4px;
    padding:4px 6px; font-size:11px;
  }
  #playToggle.playing { background:#2e7d32; border-color:#2e7d32; color:#fff; }
  .dwell-toggle.active { background:#1565ff; border-color:#1565ff; color:#fff; }
  #generated { font-size:10px; color:#6b7a89; margin-top: 20px; }

  @media (max-width: 760px) {
    #app { flex-direction: column; }
    #sidebar { width:100%; min-width:0; max-height: 46vh; box-sizing:border-box; }
    #map { flex:1; min-height: 54vh; }
  }
</style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <h1>NEW Pünktlichkeits-Monitoring</h1>
    <div class="sub">Proof of Concept &middot; nicht offiziell, siehe README</div>
    <div class="sub" style="margin-top:4px;"><a href="tabellen.html" style="color:#8ab4ff;">&#8594; Tabellarische Auswertung</a></div>

    <div class="section">
      <h2>Zeitpunkt (aufgezeichnet)</h2>
      <div id="hourLabel">--:--</div>
      <input type="range" id="hourSlider" min="0" max="0" step="1" value="0">
      <div class="btnrow" style="margin-top:8px;">
        <button id="playToggle">&#9654; Abspielen</button>
        <select id="playSpeed">
          <option value="60">1 Min./s</option>
          <option value="300" selected>5 Min./s</option>
          <option value="900">15 Min./s</option>
          <option value="3600">1 Std./s</option>
          <option value="7200">2 Std./s</option>
        </select>
      </div>
      <div class="sub" style="margin-top:4px;">
        <!-- POSITION_TEXT_START -->Busposition + <!-- POSITION_TEXT_END -->Pünktlichkeitsfärbung zum gewählten Zeitpunkt, aus den
        geloggten Daten (nicht live) — 30-Sek.-Auflösung, abspielbar.
      </div>
    </div>

    <!-- LIVE_SECTION_START -->
    <div class="section">
      <h2>Live-Verfolgung</h2>
      <div class="btnrow">
        <button id="liveToggle">&#9679; Live starten</button>
      </div>
      <div id="liveStatus" class="sub" style="margin-top:6px;">aus</div>
      <div class="sub" style="margin-top:4px;">
        Verbindet bei Aktivierung direkt mit der externen, nicht offiziell
        dokumentierten NEW-Datenquelle (siehe README). Läuft nur, solange
        diese Seite offen und "Live" aktiv ist.
      </div>
    </div>
    <!-- LIVE_SECTION_END -->

    <div class="section">
      <h2>Standzeit-Schwelle (Corona)</h2>
      <div class="btnrow">
        <button id="dwell5" class="dwell-toggle active">&ge; 5 Min.</button>
        <button id="dwell10" class="dwell-toggle">&ge; 10 Min.</button>
        <button id="dwellOff" class="dwell-toggle">Aus</button>
      </div>
    </div>

    <div class="section">
      <h2>Linien</h2>
      <div class="btnrow">
        <button id="allOn">Alle an</button>
        <button id="allOff">Alle aus</button>
      </div>
      <div id="lineList"></div>
    </div>

    <div class="section">
      <h2>Legende</h2>
      <div class="legend-row"><span class="legend-dot" style="background:hsl(120,70%,45%)"></span> pünktlich (&Oslash; ~0 Min.)</div>
      <div class="legend-row"><span class="legend-dot" style="background:hsl(60,70%,45%)"></span> mittlere Verspätung</div>
      <div class="legend-row"><span class="legend-dot" style="background:hsl(0,70%,45%)"></span> hohe Verspätung (&ge; <span id="redThresh"></span> Min.)</div>
      <div class="legend-row"><span class="legend-dot" style="background:#aaa"></span> keine Daten zu dieser Stunde</div>
      <div class="legend-row" id="dwellLegendRow"><span class="legend-corona"></span> Standzeit &ge; <span id="dwellThreshLabel">5</span> Min. beobachtet</div>
      <div class="legend-row"><span class="legend-pantograph">&#9889;</span> bekannter Pantograph-Standort</div>
      <!-- LIVE_LEGEND_START -->
      <div class="legend-row"><span class="legend-bus">&#128652;</span> Live-Busposition (bei aktiver Live-Verfolgung)</div>
      <!-- LIVE_LEGEND_END -->
      <div class="legend-row"><span class="legend-bus hist">&#128652;</span> Busposition zum Zeitschieber-Zeitpunkt (aus Logger-Daten)</div>
      <div class="legend-row"><span style="display:inline-flex;gap:3px;"><span style="background:#2e7d32;color:#fff;border-radius:7px;padding:0 4px;font-size:10px;">+1</span><span style="background:#c62828;color:#fff;border-radius:7px;padding:0 4px;font-size:10px;">+7</span></span> Zahl am Bus-Symbol = zuletzt bekannte Verspätung (Min.)</div>
    </div>

    <div id="generated"></div>
  </div>
  <div id="map"></div>
</div>

<script>
const DATA = __DATA_JSON__;

const map = L.map('map', { zoomControl: true });
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap-Mitwirkende'
}).addTo(map);

// Karte auf alle Haltestellen zentrieren
const allLatLon = Object.values(DATA.stops).map(s => [s.lat, s.lon]);
if (allLatLon.length) {
  map.fitBounds(allLatLon, { padding: [20, 20] });
} else {
  map.setView([51.1805, 6.4428], 12); // Mönchengladbach
}

const lineKeys = Object.keys(DATA.lines).sort();
const checked = {};
lineKeys.forEach(k => checked[k] = true);

const polylineLayers = {};
lineKeys.forEach(short => {
  const line = DATA.lines[short];
  if (line.path && line.path.length > 1) {
    polylineLayers[short] = L.polyline(line.path, { color: line.color, weight: 4, opacity: 0.75 })
      .addTo(map)
      .bindTooltip(short + " – " + line.long_name);
  }
});

let stopMarkers = {}; // stop_id -> L.marker
let historicalVehicleMarkers = {}; // vehicle -> L.marker (aus Logger-Daten, gesteuert vom Zeitschieber)

// Zeitschieber-Bereich aus der tatsächlichen Aufzeichnungs-Zeitachse ableiten
const timelineSlider = document.getElementById('hourSlider');
const maxSlot = Math.floor(DATA.timeline_max_offset / DATA.position_step_seconds);
timelineSlider.max = String(maxSlot);
timelineSlider.value = String(Math.floor(maxSlot * 0.15)); // Default: etwas in den Datensatz hinein, nicht ganz am (spärlichen) Anfang

function findPositionAt(vehicle, absSeconds) {
  const points = DATA.position_history[vehicle];
  if (!points || points.length === 0) return null;
  // Letzten Punkt mit offset <= absSeconds per Binärsuche finden
  // (Punkte sind je Fahrzeug aufsteigend nach Zeit sortiert, siehe build_dashboard.py)
  let lo = 0, hi = points.length - 1, result = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid][0] <= absSeconds) { result = mid; lo = mid + 1; }
    else hi = mid - 1;
  }
  if (result === -1) return null;
  const [offset, lat, lon, route] = points[result];
  if (absSeconds - offset > DATA.position_stale_seconds) return null; // zu alt -> Fahrzeug vermutlich nicht mehr aktiv
  return { lat, lon, route, offset };
}

function findDelayAt(vehicle, absSeconds) {
  const points = DATA.delay_history[vehicle];
  if (!points || points.length === 0) return null;
  let lo = 0, hi = points.length - 1, result = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid][0] <= absSeconds) { result = mid; lo = mid + 1; }
    else hi = mid - 1;
  }
  if (result === -1) return null;
  const [offset, delayMin] = points[result];
  if (absSeconds - offset > DATA.position_stale_seconds) return null; // zu alter Messwert -> nicht mehr anzeigen
  return delayMin;
}

function delayBadgeColor(delayMin) {
  if (delayMin <= 1) return "#2e7d32";   // gruen: puenktlich
  if (delayMin <= 4) return "#e0a800";   // gelb: leicht verspaetet
  return "#c62828";                       // rot: deutlich verspaetet
}

function renderHistoricalBuses(absSeconds) {
  const seen = new Set();
  for (const vehicle of Object.keys(DATA.position_history)) {
    const pos = findPositionAt(vehicle, absSeconds);
    if (!pos || !checked[pos.route]) continue;
    seen.add(vehicle);

    const delay = findDelayAt(vehicle, absSeconds);
    const color = (DATA.lines[pos.route] || {}).color || '#333';
    const delayHtml = delay !== null
      ? `<div class="hist-bus-delay" style="background:${delayBadgeColor(delay)}">${delay > 0 ? '+' : ''}${Math.round(delay)}</div>`
      : "";
    const icon = L.divIcon({
      className: "",
      html: `<div style="position:relative;width:20px;height:20px;">
               <div class="hist-bus-icon" style="background:${color}">&#128652;</div>
               ${delayHtml}
             </div>`,
      iconSize: [20, 20],
      iconAnchor: [10, 10],
    });
    const delayInfo = delay !== null
      ? `<br>Letzte bekannte Verspätung: ${delay > 0 ? '+' : ''}${delay.toFixed(1)} Min.`
      : "<br>Keine aktuelle Verspätungsangabe";
    const popup = `<b>Fahrzeug ${vehicle}</b> &middot; Linie ${pos.route}<br>` +
      `Letzte bekannte Position: ${formatDateLabel(slotToDate(Math.floor(pos.offset / DATA.position_step_seconds)))}` +
      delayInfo;

    if (historicalVehicleMarkers[vehicle]) {
      historicalVehicleMarkers[vehicle].setLatLng([pos.lat, pos.lon]);
      historicalVehicleMarkers[vehicle].setIcon(icon);
      historicalVehicleMarkers[vehicle].setPopupContent(popup);
    } else {
      historicalVehicleMarkers[vehicle] = L.marker([pos.lat, pos.lon], { icon, zIndexOffset: 900 })
        .addTo(map)
        .bindPopup(popup);
    }
  }
  Object.keys(historicalVehicleMarkers).forEach(id => {
    if (!seen.has(id)) {
      map.removeLayer(historicalVehicleMarkers[id]);
      delete historicalVehicleMarkers[id];
    }
  });
}

function colorForDelay(avgDelayMin) {
  const clipped = Math.max(0, Math.min(DATA.delay_min_for_red, avgDelayMin));
  const hue = 120 - (clipped / DATA.delay_min_for_red) * 120;
  return `hsl(${hue.toFixed(0)}, 70%, 45%)`;
}

// ---------------------------------------------------------------------
// Abspielen: rückt den Zeitschieber automatisch in einstellbarem Tempo vor
// (statt manuell ziehen zu müssen). Tempo = simulierte Sekunden pro reale
// Sekunde (z.B. "5 Min./s" = 300).
// ---------------------------------------------------------------------
const PLAYBACK_TICK_MS = 200;
let playing = false;
let playTimer = null;

function stepsPerTick() {
  const speedPerRealSecond = parseInt(document.getElementById('playSpeed').value, 10);
  return Math.max(1, Math.round(speedPerRealSecond * (PLAYBACK_TICK_MS / 1000) / DATA.position_step_seconds));
}

function playTick() {
  const slider = document.getElementById('hourSlider');
  const max = parseInt(slider.max, 10);
  const next = parseInt(slider.value, 10) + stepsPerTick();
  slider.value = Math.min(next, max);
  redraw();
  if (next >= max) stopPlayback();
}

function startPlayback() {
  playing = true;
  const btn = document.getElementById('playToggle');
  btn.innerHTML = '&#10074;&#10074; Pause';
  btn.classList.add('playing');
  playTimer = setInterval(playTick, PLAYBACK_TICK_MS);
}

function stopPlayback() {
  playing = false;
  const btn = document.getElementById('playToggle');
  btn.innerHTML = '&#9654; Abspielen';
  btn.classList.remove('playing');
  if (playTimer) {
    clearInterval(playTimer);
    playTimer = null;
  }
}

document.getElementById('playToggle').addEventListener('click', () => {
  if (playing) stopPlayback(); else startPlayback();
});
// Manuelles Ziehen am Regler soll die Wiedergabe pausieren, nicht dagegen anlaufen
['mousedown', 'touchstart'].forEach(evt =>
  timelineSlider.addEventListener(evt, () => { if (playing) stopPlayback(); })
);

function computeStopColorAndInfo(stopId, positionSlot) {
  // Pünktlichkeits-Buckets sind gröber (BUCKET_SECONDS) als der
  // Positions-/Zeitschieber (POSITION_STEP_SECONDS) -> umrechnen.
  const puncBucket = Math.floor((positionSlot * DATA.position_step_seconds) / DATA.bucket_seconds);
  let sum = 0, count = 0;
  const perLine = [];
  lineKeys.forEach(short => {
    if (!checked[short]) return;
    const pl = DATA.punctuality[short];
    if (!pl || !pl[stopId]) return;
    const hb = pl[stopId][String(puncBucket)];
    if (!hb) return;
    sum += hb.avg_delay_min * hb.count;
    count += hb.count;
    perLine.push(`${short}: ${hb.avg_delay_min.toFixed(1)} Min. (n=${hb.count})`);
  });
  if (count === 0) return { color: "#aaa", info: "Keine Daten in diesem Zeitfenster", hasData: false };
  const avg = sum / count;
  return {
    color: colorForDelay(avg),
    info: `Ø ${avg.toFixed(1)} Min. Verspätung (n=${count})<br>${perLine.join("<br>")}`,
    hasData: true,
  };
}

let dwellThresholdMin = 5;

function hasStar(stopId) {
  let count = 0, maxMin = 0;
  for (const short of lineKeys) {
    if (!checked[short]) continue;
    const byStop = DATA.dwell_durations[short];
    const durations = byStop && byStop[stopId];
    if (!durations) continue;
    for (const min of durations) {
      if (min >= dwellThresholdMin) {
        count += 1;
        maxMin = Math.max(maxMin, min);
      }
    }
  }
  return count > 0 ? { count, max_min: maxMin } : null;
}

const STOP_DOT_SIZE = 14;
const STOP_DOT_SIZE_NO_DATA = Math.round(STOP_DOT_SIZE * 0.2); // 20% Größe für Haltestellen ohne Daten im gewählten Zeitfenster

function makeIcon(color, star, isPantograph, hasData) {
  const size = hasData ? STOP_DOT_SIZE : STOP_DOT_SIZE_NO_DATA;
  const half = size / 2;
  const coronaHtml = star ? `<div class="stop-corona"></div>` : "";
  const pantoHtml = isPantograph ? `<div class="pantograph-badge">&#9889;</div>` : "";
  return L.divIcon({
    className: "",
    html: `<div style="position:relative;width:${size}px;height:${size}px;">
             ${coronaHtml}
             <div style="position:relative;width:${size}px;height:${size}px;border-radius:50%;background:${color};border:1px solid #333;box-shadow:0 0 2px rgba(0,0,0,.5);"></div>
             ${pantoHtml}
           </div>`,
    iconSize: [size, size],
    iconAnchor: [half, half],
  });
}

function visibleStopIds() {
  const ids = new Set();
  lineKeys.forEach(short => {
    if (!checked[short]) return;
    (DATA.lines[short].stops || []).forEach(id => ids.add(id));
  });
  return ids;
}

const TIMELINE_MIN = new Date(DATA.timeline_min_iso);
const WEEKDAYS_DE = ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'];

function slotToDate(slot) {
  return new Date(TIMELINE_MIN.getTime() + slot * DATA.position_step_seconds * 1000);
}

function formatDateLabel(d) {
  const pad = n => String(n).padStart(2, '0');
  return `${WEEKDAYS_DE[d.getDay()]} ${pad(d.getDate())}.${pad(d.getMonth() + 1)}. ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function redraw() {
  const slot = parseInt(document.getElementById('hourSlider').value, 10);
  document.getElementById('hourLabel').textContent = formatDateLabel(slotToDate(slot));
  renderHistoricalBuses(slot * DATA.position_step_seconds);

  // Linien ein-/ausblenden
  lineKeys.forEach(short => {
    const layer = polylineLayers[short];
    if (!layer) return;
    if (checked[short] && !map.hasLayer(layer)) layer.addTo(map);
    if (!checked[short] && map.hasLayer(layer)) map.removeLayer(layer);
  });

  const visible = visibleStopIds();

  // alte Marker entfernen, die nicht mehr sichtbar sein sollen
  Object.keys(stopMarkers).forEach(id => {
    if (!visible.has(id)) {
      map.removeLayer(stopMarkers[id]);
      delete stopMarkers[id];
    }
  });

  visible.forEach(stopId => {
    const stop = DATA.stops[stopId];
    if (!stop) return;
    const { color, info, hasData } = computeStopColorAndInfo(stopId, slot);
    const star = hasStar(stopId);
    const isPantograph = DATA.pantograph_stops.includes(stopId);
    const icon = makeIcon(color, !!star, isPantograph, hasData);

    let popup = `<b>${stop.name}</b><br>${info}`;
    if (star) popup += `<br><span style="color:#1565ff">Standzeit beobachtet: ${star.count}x, max ${star.max_min} Min.</span>`;
    if (isPantograph) popup += `<br><span style="color:#946200">&#9889; Bekannter Pantograph-Standort</span>`;

    if (stopMarkers[stopId]) {
      stopMarkers[stopId].setIcon(icon);
      stopMarkers[stopId].setPopupContent(popup);
    } else {
      stopMarkers[stopId] = L.marker([stop.lat, stop.lon], { icon }).addTo(map).bindPopup(popup);
    }
  });
}

// Sidebar: Linienliste aufbauen
const lineListEl = document.getElementById('lineList');
lineKeys.forEach(short => {
  const line = DATA.lines[short];
  const row = document.createElement('label');
  row.className = 'line-row';
  row.innerHTML = `<input type="checkbox" checked data-line="${short}">
                    <span class="swatch" style="background:${line.color}"></span>
                    <span>${short} &ndash; ${line.long_name}</span>`;
  lineListEl.appendChild(row);
});
lineListEl.addEventListener('change', e => {
  const short = e.target.getAttribute('data-line');
  if (!short) return;
  checked[short] = e.target.checked;
  redraw();
  if (liveActive) syncLiveMarkers();
});

document.getElementById('allOn').addEventListener('click', () => {
  lineKeys.forEach(k => checked[k] = true);
  document.querySelectorAll('#lineList input').forEach(el => el.checked = true);
  redraw();
  if (liveActive) syncLiveMarkers();
});
document.getElementById('allOff').addEventListener('click', () => {
  lineKeys.forEach(k => checked[k] = false);
  document.querySelectorAll('#lineList input').forEach(el => el.checked = false);
  redraw();
  if (liveActive) syncLiveMarkers();
});

document.getElementById('hourSlider').addEventListener('input', redraw);

const dwell5Btn = document.getElementById('dwell5');
const dwell10Btn = document.getElementById('dwell10');
const dwellOffBtn = document.getElementById('dwellOff');
function setDwellThreshold(min) {
  dwellThresholdMin = min; // Infinity = "Aus", nie erreicht -> hasStar() liefert immer null
  dwell5Btn.classList.toggle('active', min === 5);
  dwell10Btn.classList.toggle('active', min === 10);
  dwellOffBtn.classList.toggle('active', min === Infinity);
  document.getElementById('dwellLegendRow').hidden = min === Infinity;
  document.getElementById('dwellThreshLabel').textContent = min;
  redraw();
}
dwell5Btn.addEventListener('click', () => setDwellThreshold(5));
dwell10Btn.addEventListener('click', () => setDwellThreshold(10));
dwellOffBtn.addEventListener('click', () => setDwellThreshold(Infinity));

document.getElementById('redThresh').textContent = DATA.delay_min_for_red;
document.getElementById('generated').textContent = 'Erzeugt: ' + DATA.generated_at;

redraw();

let liveActive = false; // von Linien-Checkboxen abgefragt, daher außerhalb des entfernbaren Live-Blocks deklariert

// LIVE_JS_START
// ---------------------------------------------------------------------
// Live-Verfolgung: verbindet bei Aktivierung direkt (client-seitig, im
// Browser) per Server-Sent-Events mit der externen, nicht offiziell
// dokumentierten Firebase-Quelle von livebus.new.de. Zeigt echte
// GPS-Positionen (lat/lon im Rohdatensatz enthalten). Läuft NUR solange
// diese Seite offen und der Schalter aktiv ist — keine dauerhafte
// Hintergrund-Verbindung. Siehe README für Hinweise zur Datenquelle.
// ---------------------------------------------------------------------
const LIVE_SSE_URL = "__LIVE_SSE_URL__"; // aus config.py eingesetzt, nicht im Quellcode hartkodiert (siehe README "Veröffentlichung")

let liveSource = null;
let liveRoot = {};
let liveVehicleMarkers = {};

function applyLiveEvent(eventType, path, data) {
  const parts = path.split('/').filter(p => p.length);
  if (eventType === 'put') {
    if (parts.length === 0) {
      Object.keys(liveRoot).forEach(k => delete liveRoot[k]);
      if (data && typeof data === 'object') Object.assign(liveRoot, data);
      return;
    }
    let node = liveRoot;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!node[parts[i]] || typeof node[parts[i]] !== 'object') node[parts[i]] = {};
      node = node[parts[i]];
    }
    const key = parts[parts.length - 1];
    if (data === null || data === undefined) delete node[key];
    else node[key] = data;
  } else if (eventType === 'patch') {
    let node = liveRoot;
    for (const p of parts) {
      if (!node[p] || typeof node[p] !== 'object') node[p] = {};
      node = node[p];
    }
    if (data && typeof data === 'object') Object.assign(node, data);
  }
}

function syncLiveMarkers() {
  const seen = new Set();
  for (const [vehicleId, node] of Object.entries(liveRoot)) {
    const raw = node && node.raw;
    if (!raw || typeof raw.lat !== 'number' || typeof raw.lon !== 'number') continue;
    const route = raw.route || raw.route_translated;
    if (!route || !checked[route]) continue;
    seen.add(vehicleId);

    const color = (DATA.lines[route] || {}).color || '#333';
    const icon = L.divIcon({
      className: "",
      html: `<div class="live-bus-icon" style="background:${color}">&#128652;</div>`,
      iconSize: [22, 22],
      iconAnchor: [11, 11],
    });
    const popup = `<b>Fahrzeug ${vehicleId}</b> &middot; Linie ${route}<br>` +
      `${raw.stationname || '(unterwegs)'}<br>${raw.time || ''}` +
      (raw.capacity ? `<br>Kapazität: ${raw.capacity}` : "");

    if (liveVehicleMarkers[vehicleId]) {
      liveVehicleMarkers[vehicleId].setLatLng([raw.lat, raw.lon]);
      liveVehicleMarkers[vehicleId].setIcon(icon);
      liveVehicleMarkers[vehicleId].setPopupContent(popup);
    } else {
      liveVehicleMarkers[vehicleId] = L.marker([raw.lat, raw.lon], { icon, zIndexOffset: 1000 })
        .addTo(map)
        .bindPopup(popup);
    }
  }
  Object.keys(liveVehicleMarkers).forEach(id => {
    if (!seen.has(id)) {
      map.removeLayer(liveVehicleMarkers[id]);
      delete liveVehicleMarkers[id];
    }
  });
  if (liveActive) {
    document.getElementById('liveStatus').textContent = `aktiv – ${seen.size} Fahrzeuge sichtbar`;
  }
}

function startLive() {
  liveRoot = {};
  liveSource = new EventSource(LIVE_SSE_URL);
  const handle = (eventType) => (e) => {
    try {
      const obj = JSON.parse(e.data);
      applyLiveEvent(eventType, obj.path || "/", obj.data);
      syncLiveMarkers();
    } catch (err) {
      console.error("Live-Event-Fehler:", err);
    }
  };
  liveSource.addEventListener('put', handle('put'));
  liveSource.addEventListener('patch', handle('patch'));
  liveSource.onopen = () => {
    if (liveActive) document.getElementById('liveStatus').textContent = 'verbunden, warte auf Daten...';
  };
  liveSource.onerror = () => {
    if (liveActive) document.getElementById('liveStatus').textContent = 'Verbindung unterbrochen, versuche erneut...';
  };
}

function stopLive() {
  if (liveSource) {
    liveSource.close();
    liveSource = null;
  }
  Object.values(liveVehicleMarkers).forEach(m => map.removeLayer(m));
  liveVehicleMarkers = {};
  liveRoot = {};
  document.getElementById('liveStatus').textContent = 'aus';
}

const liveToggleBtn = document.getElementById('liveToggle');
liveToggleBtn.addEventListener('click', () => {
  liveActive = !liveActive;
  if (liveActive) {
    liveToggleBtn.innerHTML = '&#9632; Live stoppen';
    liveToggleBtn.classList.add('live-active');
    document.getElementById('liveStatus').textContent = 'verbinde...';
    startLive();
  } else {
    liveToggleBtn.innerHTML = '&#9679; Live starten';
    liveToggleBtn.classList.remove('live-active');
    stopLive();
  }
});
// LIVE_JS_END
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
