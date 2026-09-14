"""
Konfiguration für das NEW-Stadtbus Pünktlichkeits-Monitoring (PoC).

HINWEIS ZU DIESEM ÖFFENTLICHEN REPO:
Die Live-Datenquelle (Fahrzeugpositionen/Fahrgastzählung) läuft über eine
nicht offiziell für Drittnutzung dokumentierte externe Schnittstelle. Die
konkrete URL sowie das Ingestion-Skript (logger.py), das sich damit
verbindet, sind deshalb bewusst NICHT Teil dieses öffentlichen Repos —
siehe README. Diese Datei ist ein Platzhalter, der den restlichen Code
(GTFS-Verarbeitung, Matching, Standzeit-Analyse, Dashboard-Build) lauffähig
hält, sofern eine eigene, lokal befüllte SQLite-DB (data/puenktlichkeit.db)
vorhanden ist.
"""

# Bewusst kein echter Wert — siehe Hinweis oben. Live-Verfolgung im
# Dashboard-Build bleibt dadurch inaktiv/leer.
FIREBASE_BASE_URL = None
BUSSES_PATH = "livegeomatching/busses"
SSE_URL = None

# SQLite-Datenbankdatei
DB_PATH = "data/puenktlichkeit.db"

# Felder aus dem "raw"-Knoten, die für die Pünktlichkeitsauswertung relevant sind
RELEVANT_FIELDS = [
    "route",
    "route_translated",
    "trip",
    "vehicle",
    "stationid",
    "stationname",
    "time",
    "sendtime",
]
