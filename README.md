# Pünktlichkeits-Monitoring — NEW Stadtbusse Mönchengladbach/Viersen

Proof-of-Concept-Tool, das Ist-Ankunftszeiten von NEW-Bussen
(Mönchengladbach/Viersen) mit dem Soll-Fahrplan (GTFS) vergleicht und die
Pünktlichkeit pro Linie/Haltestelle im Zeitverlauf auf einer interaktiven
Karte darstellt.

**[➜ Live-Demo ansehen](https://orlando80furioso.github.io/Puenktlichkeits-Monitoring/)**

## Was das Tool zeigt

- Interaktive Karte (Leaflet/OpenStreetMap) mit 10 Buslinien, echter
  Straßengeometrie (aus OpenStreetMap-Routenrelationen, mit automatischer
  Lücken-Reparatur per OSRM-Routing)
- Haltestellen eingefärbt grün→gelb→rot nach durchschnittlicher Verspätung
  zum per Zeitschieber gewählten Zeitpunkt (30-Sekunden-Auflösung,
  abspielbar mit einstellbarer Geschwindigkeit)
- Blaue Corona-Markierung an Haltestellen mit auffälliger Standzeit
  (≥5/≥10 Min., umschaltbar)
- Markierung des bekannten Pantograph-Ladestandorts (E-Bus-Schnellladung)
- Responsive Layout für Desktop und Mobile

## Technischer Hintergrund

- **Soll-Fahrplan**: GTFS-Daten (NRW-weites Feed, DELFI/gtfs.de)
- **Streckengeometrie**: OpenStreetMap-Routenrelationen (`route=bus`) über
  die öffentliche Overpass-API — das GTFS-Feed selbst liefert keine
  `shapes.txt`. Bei unvollständig gemappten Relationen (reale
  Baustellen-Umleitungen, die noch nicht in OSM nachgezogen wurden) wird
  die Lücke automatisch mit OSRM-Straßenrouting geschlossen.
- **Matching-Logik**: Ist-Ankünfte werden über Linie + Haltestellenname
  (Fuzzy-Match) + nächstgelegene Soll-Zeit dem Fahrplan zugeordnet, da eine
  eindeutige `trip_id`-Zuordnung aus den Live-Daten nicht möglich war.
- **Stack**: Python (SQLite, `requests`), Leaflet.js, reines HTML/CSS/JS
  ohne Build-Tooling — die Dashboard-Datei ist eine einzelne, eigenständige
  HTML-Datei.

## Hinweis zur Live-Datenquelle

Die Ist-Ankunftszeiten stammen aus einer Live-Datenquelle, die beim
Reverse-Engineering der öffentlichen Live-Bus-Karte von NEW identifiziert
wurde. Dieser Kanal ist **nicht offiziell für Drittnutzung dokumentiert** —
er wirkt eher wie eine unbeabsichtigt offene interne Schnittstelle als wie
eine bewusst öffentliche API.

**Deshalb sind die konkrete URL sowie das Ingestion-Skript, das sich damit
verbindet, bewusst nicht Teil dieses öffentlichen Repos.** Das Dashboard
hier ist eine Auswertung bereits erhobener, historischer Daten aus einem
begrenzten Testzeitraum — keine Live-Anbindung. `config.py` ist ein
Platzhalter ohne echte Zugangsdaten.

Dieses Projekt ist als Proof of Concept / Diskussionsgrundlage gedacht —
u.a. um mit NEW über eine offizielle Schnittstelle ins Gespräch zu kommen,
nicht als dauerhaft betriebenes Tool auf Basis der gefundenen Schnittstelle.

## Struktur

```
build_dashboard.py          Baut die Dashboard-HTML aus DB + GTFS-Daten
extract_gtfs_subset.py      Extrahiert einen kompakten GTFS-Auszug
gtfs_lookup.py              Fahrplan-Nachschlagelogik (Kalender, Matching)
match_puenktlichkeit.py     Gleicht Ist-Events mit Soll-Fahrplan ab
standzeit.py                Analysiert auffällige Standzeiten
export_simba.py             Export des Liniennetzes im SimBA-CSV-Format
                             (separate Anfrage, siehe docs/simba-export.md)
docs/                        Zusatzdokumentation (Pantograph-Standorte, SimBA-Export)
index.html                   Fertig gebautes Dashboard (diese Datei wird
                             per GitHub Pages ausgeliefert)
```

## Eigenständig ausführen

Der volle Pipeline-Code (Matching, Standzeit-Analyse, Dashboard-Build)
funktioniert mit einer eigenen `data/puenktlichkeit.db` im GTFS-Schema
dieses Projekts (siehe `db.py` für die Tabellenstruktur):

```bash
pip install -r requirements.txt
python extract_gtfs_subset.py --routes 002,003,008,014,015,020,021,025,033,035
python match_puenktlichkeit.py
python standzeit.py
python build_dashboard.py --public
```

`--public` erzeugt `dashboard_public.html` ohne Live-Verfolgungs-Code und
ohne eingebettete GPS-Positionsdaten — die Variante, die auch hier im Repo
als `index.html` veröffentlicht ist.

## Kontext

Entstanden als Showcase-Projekt im Bereich ÖPNV-Digitalisierung/
Datenanalyse — u.a. mit Blick auf Flottenelektrifizierung im öffentlichen
Nahverkehr.
