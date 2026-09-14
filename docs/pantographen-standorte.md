# Pantographen-Standorte der NEW (Mönchengladbach/Viersen)

Ergänzung zum Livebus-Projekt (siehe [README.md](../README.md)). Relevant,
falls Lade-/Standzeiten an Pantographen später als zusätzlicher Faktor in
die Pünktlichkeitsauswertung einbezogen werden sollen (z.B. um zu prüfen,
ob Verspätungen mit Ladevorgängen korrelieren).

Stand: Recherche 09/2026.

## Bestätigte Fakten

**Lieferant Ladeinfrastruktur**: Kempower (Finnland), bestätigt durch
Pressemitteilung März 2026 (kempower.com, electrive.net, busplaner.de).

**Depot Rheinstraße, Mönchengladbach** — zentraler Ladehub, kein
Pantograph, klassische Ladesäulen:
- 4 Kempower Power Units
- 16 Kempower Satellites (Ladepunkte)
- Gesamtleistung 1.200 kW, dynamisch verteilt
- (Zweite Quelle GP Joule/Connect nennt abweichend 14 Satelliten + 4 Power
  Units — kleine Diskrepanz, nicht aufgelöst)

**Pantographen auf der Strecke** (Zwischenladung während Fahrerpausen):
- Anzahl: **7 Pantographen**, mehrfach bestätigt (Kempower Success Story,
  electrive.net, busplaner.de)
- Ladeleistung bis zu 350 kW
- Vollautomatisches Andocken (Kempower Pantograph Up/Down System)
- Einzelstandorte der 7 Punkte NICHT öffentlich benannt — nur der
  Streckenkorridor Mönchengladbach–Viersen als Region

**Einzig namentlich bekannter Standort**: Künkelstraße (Eicken) —
Endhaltestelle Linie 033, in Betrieb seit November 2021.
- Ursprünglicher Lieferant laut Presseartikel 2021: Schunk ("Panto up")
- Wikipedia nennt abweichend Siemens — Widerspruch nicht aufgelöst
- Bus dockt von unten an, Station spannungsfrei ohne ladenden Bus,
  Bodenmarkierungen für Parkposition

## Aufgelöste falsche Fährte

"12 Pantographen" war im Umlauf — das ist eine technische
Kapazitätsangabe aus Kempowers Produktdatenblatt (max. 12 Satellites pro
Power Unit anschließbar), keine tatsächliche Standortzahl bei NEW. Reale,
bestätigte Zahl: **7**.

## Offene Punkte

- Standorte der übrigen 6 Pantographen (außer Künkelstraße) nicht
  öffentlich dokumentiert gefunden. Möglicher Ansatz: Lade-Symbol-Marker
  auf der Livebus-Karte (https://livebus.new.de/maps/tlnp) visuell
  identifizieren.
- Widerspruch Schunk vs. Siemens (Künkelstraße, 2021) ungeklärt.
- Für die aktuelle Pünktlichkeits-Pipeline nicht kritisch — nur relevant,
  falls Standzeiten an Pantograph-Haltestellen (siehe `standzeit.py`)
  gezielt mit Ladevorgängen in Verbindung gebracht werden sollen.

## Quellen

- kempower.com (Success Story, März 2026)
- electrive.net (09.03.2026)
- busplaner.de (Fotostrecke, März 2026)
- de.wikipedia.org/wiki/NEW_mobil_und_aktiv_Mönchengladbach
- urban-transport-magazine.com (Dezember 2021, zur ersten Linie 033)
