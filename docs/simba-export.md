# SimBA-Fahrplan-Export (externe Anfrage, nicht Teil der Pünktlichkeits-Pipeline)

`export_simba.py` exportiert den **kompletten NEW-Linienfahrplan** (alle 46
Linien, nicht nur die 10 Testlinien des Pünktlichkeits-Projekts) im
SimBA-CSV-Format (Reiner-Lemoine-Institut,
[Doku](https://rli-simba.readthedocs.io/en/dev/simulation_parameters.html)).

Erstellt am 2026-09-12 als separate, externe Anfrage — unabhängig vom
Pünktlichkeits-Tool, nutzt aber dieselbe bereits heruntergeladene
GTFS-Quelle (`data/gtfs/google_transit.zip`).

## Nutzung

```powershell
python export_simba.py                          # nächster Mittwoch als Referenztag
python export_simba.py --date 2026-09-16
python export_simba.py --date 2026-09-16 --out mein_export.csv
```

## Format (SimBA-Pflichtfelder + `line`)

| Spalte | Inhalt |
|---|---|
| `rotation_id` | GTFS `trip_id` (siehe Einschränkung unten) |
| `departure_name` / `arrival_name` | Erste/letzte Haltestelle der Fahrt |
| `departure_time` / `arrival_time` | ISO-Format, verankert auf den gewählten Referenztag |
| `distance` | Streckenlänge in Metern (siehe Einschränkung unten) |
| `vehicle_type` | Platzhalter `solobus_12m` (siehe Einschränkung unten) |
| `line` | GTFS `route_short_name` (optionales SimBA-Feld) |

## Wichtige Einschränkungen

1. **Kein echter Umlaufplan.** Ein Umlaufplan verkettet mehrere Fahrten zu
   einer durchgehenden Fahrzeug-Einsatzfolge (inkl. Leerfahrten,
   Pausen/Ladezeiten zwischen Fahrten). Das NRW-GTFS-Feed enthält dafür
   keine Daten (kein `block_id`-Feld in `trips.txt`). `rotation_id` ist
   daher 1:1 die GTFS `trip_id` — **jede Zeile ist eine einzelne,
   eigenständige Fahrt**, keine mehrteilige Umlaufkette. Für einen echten
   Umlaufplan bräuchte man NEWs internes Dispositionssystem als Quelle.
2. **Distanz ist Luftlinie**, nicht die reale Streckenlänge: `shapes.txt`
   im Feed ist leer (keine Streckengeometrie), `shape_dist_traveled` in
   `stop_times.txt` ebenfalls überall leer. Berechnet als Summe der
   Haversine-Abstände zwischen den Haltestellen in Fahrtreihenfolge —
   die reale (straßengebundene) Distanz liegt typischerweise 10-30% höher.
   (Das Pünktlichkeits-Dashboard nutzt für die Kartenanzeige echtes
   OSRM-Straßenrouting — für alle ~2.700 Fahrten des Gesamtnetzes wäre das
   beim öffentlichen Demo-Server nicht fair-use-konform gewesen, daher hier
   die einfachere Luftlinien-Näherung.)
3. **`vehicle_type` ist ein Platzhalter** (`solobus_12m`), da GTFS keine
   Fahrzeugtypen je Fahrt führt. Vor Nutzung in SimBA durch echte
   Flottenzuordnung ersetzen.
4. **Ein Referenztag**, nicht der ganze Fahrplanzeitraum: Das Feed nutzt für
   praktisch alle Linien ausschließlich `calendar_dates.txt`-Ausnahmen
   (keine wiederkehrenden Wochentags-Muster in `calendar.txt`) — es gibt
   also keinen einzelnen "typischen Tag", der für den ganzen
   Gültigkeitszeitraum (bis Ende Januar 2027) repräsentativ wäre. Der
   Export bildet exakt EINEN gewählten Kalendertag ab (Default: nächster
   Mittwoch), wie es SimBA für eine Simulation ohnehin erwartet.

## Ergebnis (Referenztag 2026-09-16, Mittwoch)

- 46 NEW-Linien im Feed
- 2.661 aktive Fahrten an diesem Tag
- Datei: `simba_new_linienfahrplan_2026-09-16.csv`
