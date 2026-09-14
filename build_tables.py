"""
Baut eine zweite HTML-Seite (tabellen.html) mit tabellarischer Auswertung:
- Pünktlichkeit je Linie
- Pünktlichkeit je Linie + Fahrzeug (Näherung für "Umlauf" — echte
  Umlaufdaten/Fahrzeug-Blöcke liegen nicht vor, siehe README/
  docs/simba-export.md; das Fahrzeug ist der nächstbeste verfügbare Proxy)
- Haltestellen mit Standzeit >= 5 Min.
- Haltestellen mit Standzeit >= 10 Min.

Enthält ausschließlich aggregierte Kennzahlen (keine Live-Daten, keine
GPS-Rohdaten) — unproblematisch für beide Dashboard-Varianten (privat wie
öffentlich), daher kein --public-Schalter nötig.

Nutzung:
    python build_tables.py
    -> erzeugt tabellen.html im Projektordner
"""

from config import DB_PATH
from db import get_connection

OUT_FILE = "tabellen.html"


def fetch_line_stats(conn):
    return conn.execute(
        """
        SELECT route,
               COUNT(*) AS n,
               ROUND(AVG(delay_seconds) / 60.0, 1) AS avg_delay_min,
               ROUND(100.0 * SUM(CASE WHEN delay_seconds <= 180 THEN 1 ELSE 0 END) / COUNT(*), 1) AS puenktlichkeit
        FROM matches
        GROUP BY route
        ORDER BY route
        """
    ).fetchall()


def fetch_line_vehicle_stats(conn):
    return conn.execute(
        """
        SELECT m.route, r.vehicle,
               COUNT(*) AS n,
               ROUND(AVG(m.delay_seconds) / 60.0, 1) AS avg_delay_min,
               ROUND(100.0 * SUM(CASE WHEN m.delay_seconds <= 180 THEN 1 ELSE 0 END) / COUNT(*), 1) AS puenktlichkeit
        FROM matches m
        JOIN raw_events r ON r.id = m.raw_event_id
        GROUP BY m.route, r.vehicle
        HAVING COUNT(*) >= 5
        ORDER BY m.route, r.vehicle
        """
    ).fetchall()


def fetch_dwell_stats(conn, min_seconds):
    return conn.execute(
        """
        SELECT stationname, route,
               COUNT(*) AS n,
               ROUND(AVG(dwell_seconds) / 60.0, 1) AS avg_min,
               ROUND(MAX(dwell_seconds) / 60.0, 1) AS max_min
        FROM dwell_events
        WHERE dwell_seconds >= ?
        GROUP BY stationname, route
        ORDER BY n DESC, avg_min DESC
        """,
        (min_seconds,),
    ).fetchall()


def render_table(headers, rows, table_id, empty_msg="Keine Daten."):
    if not rows:
        return f'<p class="empty">{empty_msg}</p>'
    thead = "".join(f'<th onclick="sortTable(\'{table_id}\', {i})">{h} <span class="sort-arrow"></span></th>' for i, h in enumerate(headers))
    tbody = ""
    for row in rows:
        cells = "".join(f"<td>{c}</td>" for c in row)
        tbody += f"<tr>{cells}</tr>"
    return f'<table id="{table_id}"><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>'


def main():
    conn = get_connection(DB_PATH)

    line_stats = fetch_line_stats(conn)
    line_vehicle_stats = fetch_line_vehicle_stats(conn)
    dwell5 = fetch_dwell_stats(conn, 300)
    dwell10 = fetch_dwell_stats(conn, 600)

    total_matched = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    total_raw = conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
    generated_at = conn.execute("SELECT datetime('now')").fetchone()[0]

    table_line = render_table(
        ["Linie", "Events", "Ø Verspätung (Min.)", "Pünktlichkeit (%)"],
        line_stats,
        "tblLine",
    )
    table_line_vehicle = render_table(
        ["Linie", "Fahrzeug", "Events", "Ø Verspätung (Min.)", "Pünktlichkeit (%)"],
        line_vehicle_stats,
        "tblLineVehicle",
        empty_msg="Keine Fahrzeuge mit ausreichend Events (min. 5).",
    )
    table_dwell5 = render_table(
        ["Haltestelle", "Linie", "Anzahl", "Ø Standzeit (Min.)", "Max. Standzeit (Min.)"],
        dwell5,
        "tblDwell5",
    )
    table_dwell10 = render_table(
        ["Haltestelle", "Linie", "Anzahl", "Ø Standzeit (Min.)", "Max. Standzeit (Min.)"],
        dwell10,
        "tblDwell10",
    )

    html = TEMPLATE.format(
        total_raw=total_raw,
        total_matched=total_matched,
        match_pct=round(100 * total_matched / total_raw, 1) if total_raw else 0,
        table_line=table_line,
        table_line_vehicle=table_line_vehicle,
        table_dwell5=table_dwell5,
        table_dwell10=table_dwell10,
        generated_at=generated_at,
    )
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Tabellen-Seite geschrieben: {OUT_FILE}")
    print(f"Linien: {len(line_stats)}, Linie+Fahrzeug-Zeilen: {len(line_vehicle_stats)}, "
          f"Standzeit>=5min: {len(dwell5)}, Standzeit>=10min: {len(dwell10)}")


TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEW Pünktlichkeit &ndash; Tabellen</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    margin:0; background:#f4f4f4; color:#222; font-family: system-ui, sans-serif;
  }}
  header {{
    background:#1c2733; color:#eee; padding:16px 20px;
  }}
  header h1 {{ margin:0 0 4px; font-size:18px; }}
  header .sub {{ font-size:12px; color:#9aa7b4; }}
  nav {{ margin-top:10px; }}
  nav a {{
    color:#8ab4ff; text-decoration:none; font-size:13px; margin-right:16px;
  }}
  nav a:hover {{ text-decoration:underline; }}
  main {{ padding: 16px 20px 40px; max-width: 1100px; margin: 0 auto; }}
  section {{ margin-bottom: 36px; }}
  h2 {{ font-size:15px; margin:0 0 4px; }}
  .hint {{ font-size:12px; color:#666; margin:0 0 10px; }}
  .table-wrap {{ overflow-x:auto; background:#fff; border-radius:6px; box-shadow:0 1px 3px rgba(0,0,0,.15); }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; white-space:nowrap; }}
  th, td {{ padding:7px 12px; text-align:left; border-bottom:1px solid #eee; }}
  th {{ background:#eef1f4; cursor:pointer; user-select:none; position:sticky; top:0; }}
  th:hover {{ background:#e0e5ea; }}
  .sort-arrow {{ font-size:10px; color:#888; }}
  tbody tr:hover {{ background:#f8fafc; }}
  .empty {{ color:#888; font-size:13px; font-style:italic; }}
  footer {{ text-align:center; font-size:11px; color:#999; padding:20px; }}

  @media (prefers-color-scheme: dark) {{
    body {{ background:#14181d; color:#ddd; }}
    .table-wrap {{ background:#1e252c; }}
    th {{ background:#242c35; color:#ddd; }}
    th:hover {{ background:#2c3540; }}
    td {{ border-bottom:1px solid #2a323c; }}
    tbody tr:hover {{ background:#232b33; }}
    .hint {{ color:#9aa7b4; }}
  }}
</style>
</head>
<body>
<header>
  <h1>NEW Pünktlichkeits-Monitoring &ndash; Tabellarische Auswertung</h1>
  <div class="sub">Proof of Concept &middot; {total_raw} Roh-Events, {total_matched} gematcht ({match_pct}%) &middot; erzeugt {generated_at} UTC</div>
  <nav>
    <a href="index.html">&larr; Zur Karte</a>
  </nav>
</header>
<main>

  <section>
    <h2>Pünktlichkeit je Linie</h2>
    <p class="hint">Pünktlichkeit = Anteil Ankünfte mit Verspätung &le; 3 Min.</p>
    <div class="table-wrap">{table_line}</div>
  </section>

  <section>
    <h2>Pünktlichkeit je Linie und Fahrzeug</h2>
    <p class="hint">
      Fahrzeug als Näherung für einen "Umlauf": echte Fahrzeug-Umlauf-/Block-Daten
      liegen nicht vor (GTFS enthält kein <code>block_id</code>-Feld, siehe
      docs/simba-export.md) &mdash; ein Fahrzeug kann im Tagesverlauf auch andere
      Linien bedient haben. Nur Kombinationen mit mindestens 5 Events gezeigt.
    </p>
    <div class="table-wrap">{table_line_vehicle}</div>
  </section>

  <section>
    <h2>Haltestellen mit Standzeit &ge; 5 Min.</h2>
    <p class="hint">Aufenthalte je (Fahrzeug, Haltestelle), siehe standzeit.py. Sortiert nach Häufigkeit.</p>
    <div class="table-wrap">{table_dwell5}</div>
  </section>

  <section>
    <h2>Haltestellen mit Standzeit &ge; 10 Min.</h2>
    <div class="table-wrap">{table_dwell10}</div>
  </section>

</main>
<footer>NEW Pünktlichkeits-Monitoring &middot; Proof of Concept, nicht offiziell</footer>

<script>
function sortTable(tableId, colIdx) {{
  const table = document.getElementById(tableId);
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  const th = table.tHead.rows[0].cells[colIdx];
  const asc = th.getAttribute('data-asc') !== 'true';
  Array.from(table.tHead.rows[0].cells).forEach(c => {{
    c.removeAttribute('data-asc');
    c.querySelector('.sort-arrow').textContent = '';
  }});
  th.setAttribute('data-asc', asc);
  th.querySelector('.sort-arrow').textContent = asc ? '\\u25B2' : '\\u25BC';

  rows.sort((a, b) => {{
    const av = a.cells[colIdx].textContent.trim();
    const bv = b.cells[colIdx].textContent.trim();
    const an = parseFloat(av.replace(',', '.'));
    const bn = parseFloat(bv.replace(',', '.'));
    let cmp;
    if (!isNaN(an) && !isNaN(bn)) cmp = an - bn;
    else cmp = av.localeCompare(bv, 'de');
    return asc ? cmp : -cmp;
  }});
  rows.forEach(r => tbody.appendChild(r));
}}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
