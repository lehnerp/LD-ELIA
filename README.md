# Entra Local Sign-in Dashboard

A local Flask application at **http://127.0.0.1:8585**. No CDN, telemetry, or external IP lookup. Import an Entra interactive user sign-in CSV; use **Explore sample** for a synthetic shared-IP investigation dataset. `sample.csv` is a minimal example and `sample-investigation.csv` demonstrates the broader workflow.

## Quick start on Windows

1. Double-click **`setup.bat`** once. It finds Python 3.10+ (and offers to install Python 3.12 with `winget` if none is found), creates `.venv`, installs the packages, prepares the local SQLite case file and runs the self-test. Running it again repairs the installation; your data is never deleted.
2. Double-click **`start.bat`** whenever you want to work. It runs setup automatically if needed, backs up the case file, starts the server and opens http://127.0.0.1:8585 in your browser. Keep the window open while you work; close it or press Ctrl+C to stop.

Optional before `start.bat`: `set DASHBOARD_PORT=8686` for another port, `set GEOIP_DB=C:\path\GeoLite2-City.mmdb` for offline GeoIP.

## Continuous investigation (local SQLite case file)

Everything lives in **`data.sqlite3`** beside the app: every imported sign-in, every analyst mark and the full decision history. It survives restarts and is never sent anywhere.

- **Add to case** (default once a case exists) merges a new export into the same investigation. Each sign-in gets a stable ID from its time, user, IP, result, application and device, so overlapping or repeated exports are not duplicated. Re-importing the same file is safe: everything is reported as "already in case". When a newer export has changed values for a known sign-in (for example, Entra risk state `atRisk` → `remediated`), the stored event is updated and its marks are kept. Exact duplicate rows inside one export are kept as separate events.
- **Start a new case** discards all sign-ins, marks and decisions. The browser downloads a backup first.
- All scores are recalculated over the whole case on each load, so 72-hour context and novelty signals use every import, not just the latest file.
- **Import history** (bottom of the page, under *Case file, imports & analysis limits*) shows each file with new, updated, already-in-case and skipped counts and its export window.
- **Backups:** `start.bat` writes a timestamped copy to `backups\` on every launch and keeps the newest 20. **↓ Back up case** in the sidebar downloads a consistent snapshot at any time. To restore one, stop the app and copy the backup over `data.sqlite3`.
- Command line (inside the folder): `.venv\Scripts\python app.py --status`, `--backup`, or `--init`.

Gaps between exports are not detected, so import contiguous date ranges where possible. Keep the database, backups and exported findings private.

### Upgrading from v4

Replace the application files and keep your existing `data.sqlite3`. On first start it is upgraded automatically: an untouched copy is saved as `backups\data-before-upgrade-*.sqlite3`, the dataset moves into the new events table, and existing marks and decision history are carried over to the new stable IDs. New Device ID or coordinate columns still require re-importing the export, which now merges instead of replacing.

## Manual run (macOS/Linux or without the .bat files)

Python 3.10 or newer:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-geoip.txt   # optional, only for offline GeoIP
python app.py --open
```

The application binds to localhost only (served by waitress, or Flask's server if waitress is absent). It does not provide dashboard authentication or live Entra SSO. "Interactive logins" means analyzing Entra interactive sign-in records. Use `DASHBOARD_DB` to keep the case file elsewhere. The session key is stored in `.secret_key` so a restart does not break an open browser tab.

## CSV support

UTF-8/BOM and Windows-1250; comma, semicolon or tab separators. Required: date (`Date (UTC)`, `CreatedDateTime`, `Timestamp`) and username (`Username`, `UserPrincipalName`, `UPN`), plus a valid IP (`IP address`, `IPAddress`, `IP`). Optional: `Country`, `CountryOrRegion`, `Location` (Entra country at end of comma-separated location or JSON), `Status`, `Sign-in error code`, `Application`, `isInteractive`, aggregate risk level, risk state, authentication requirement. Explicit non-interactive records are removed; absent interaction fields are assumed interactive and reported. Use ISO 8601 timestamps where possible; absent timezone means UTC. Ambiguous slash dates use month-first parsing. Limits: 300 MB / 100,000 input rows. Invalid dates/users/IPs are counted and skipped. Unknown outcomes are never treated as successes.

## Workspace design

The top metrics, shortlist, countries, network and activity share one filter scope. Search users, IPs, applications or signal names; combine assessment, country and time filters. Time presets are anchored to the most recent imported event, not the current clock. Custom bounds are explicitly UTC. Clear filters returns to the full export. Light/dark themes are saved in your browser.

The network aggregates repeated logins into one edge for each user/IP pair. One IP node shared by multiple users makes overlap visible; it does not by itself establish an attack. Line width represents login count; color represents the strongest active assessment. Up to 24 pairs are shown, ranked by assessment, score and count; exact truncation counts are visible. Node selection focuses related edges. Use the inspector to filter the workspace to the node, or inspect its highest-risk login. Mouse wheel/buttons zoom; drag empty space to pan. Large graphs scroll within their own canvas.

Investigations have four tabs: weighted evidence and previous-success comparison; the user's preceding 72-hour timeline; all users on the same IP in that window; and analyst decision history. The timeline bins activity into three-hour intervals with separate success/failure/unknown counts. Context always comes from the full export regardless of filters. Context tables show the latest 250 records; context export includes every matching record. Next/previous controls follow the currently filtered activity order. Draft notes survive event switches in the current browser session; save a mark to persist them.

When a case already exists, each import asks whether to **add to the case** or **start a new case**. This also applies to the sample data. Marks keep the investigation open and refresh the shortlist. **Investigating** records an open review without inventing a compromise verdict. Its score stays at the automatic score. Automatic evidence and score remain visible after overrides.

## Geographic connection map

The offline world map follows the same search, assessment, country and time filters as the workspace. Choose **Connections + locations** or **Locations only**. Click a marker to inspect its users, IPs and highest-priority login; click a line to inspect the preceding and destination logins. Click an active country's land or use the inspector button to filter that country. **Europe**, **World view**, zoom buttons, mouse wheel and drag controls adjust the viewport. All map assets are bundled locally.

- **Solid markers**: CSV latitude/longitude or optional offline GeoIP City coordinates. They indicate the reported network location, not verified physical user presence.
- **Dashed markers**: approximate country positions for country-only records. These are not IP geolocations. Unknown or unsupported countries without coordinates are explicitly counted as unlocated.
- Coordinate markers are grouped by country and coordinates rounded to two decimal places. Country-only events share one approximate marker per country. Marker size reflects event count; color reflects the strongest current assessment.
- Lines connect consecutive interactive sign-in records for the same user within 72 hours, using the full dataset to establish sequence. Both endpoints must be in the current filter scope. An intervening unlocated or filtered-out login is never skipped to invent a connection. Lines do not represent traffic, distance, impossible travel or new risk-score evidence.
- Up to 40 connection pairs are drawn, prioritized by assessment, score and count; displayed/total counts are shown. Events with no map location remain in the dashboard and investigations.

CSV coordinates are accepted as `Latitude` / `Longitude`, `Location latitude` / `Location longitude`, `geoCoordinates.latitude` / `geoCoordinates.longitude`, or a JSON `Location` with `geoCoordinates`. Coordinates must be finite, latitude ±90 and longitude ±180. Both must be supplied. Optional GeoIP City coordinates are used only when CSV coordinates are absent, and do not override a conflicting exported country. Existing JSON locations are recovered automatically.

Data credits and licenses are bundled under `static/map-sources`. The map uses coarse Natural Earth geography and approximate country positions from mledoze/countries.

## Investigate and mark

1. Import the interactive sign-in CSV. The dashboard recalculates scores from available history.
2. Start in **Highest-priority investigations**. Switch between Users and IP addresses. Ranking uses the highest active event score, then the number of flagged events. Click a row to open its highest-scoring login.
3. Use **Country overview** to filter the activity table. Country totals include all activity; the two colored counts show confirmed and suspicious events. Home countries remain visible for context.
4. Inspect each weighted signal and the preceding 72-hour user timeline. Review CSV completeness before drawing conclusions.
5. Add a note, then **Start investigating**, **Confirm compromise**, **Mark suspicious**, or **Dismiss**. Reset restores the automatic classification. Decisions apply to a single login event, update the rankings and persist with timestamped decision history across imports and restarts.
6. Use the assessment filter (including **Open**) or **Priority queue** for confirmed/suspicious activity, **Show all / clear** to clear filters, and sort by risk or recent time. Export uses the current filters.

## Analysis and score weights

Scores are deterministic triage indices, not calibrated breach probabilities. Signals may be correlated. The sum is capped at 100. Missing evidence does not imply safety.

| Signal | Points | Meaning |
| --- | ---: | --- |
| Foreign country | 10 | Outside the configured home countries (default CZ, SK); location alone only warrants review |
| Unknown country | 5 | Cannot apply a geographic exclusion |
| Entra low / medium / high risk | 10 / 35 / 55 | Explicit exported aggregate risk |
| Entra atRisk | 20 | Explicit exported risk state |
| Success after failures | 35 | At least 5 previous user failures in 30 minutes |
| Repeated user failures | 20 | At least 5 failures including current login in 30 minutes |
| Rapid country change | 30 | Previous successful sign-in in another country within one hour; VPNs may explain |
| New country / IP / device | 10 / 5 / 10 | Not seen for that user in imported preceding 72-hour history; requires prior history |
| Cross-user failure pattern | 40 | Same IP: at least 10 failures across 3 users in 30 minutes; possible spray or shared network |
| Confirmed compromised by Entra | 100 | Explicit confirmedCompromised risk state |

Non-excluded events scoring 35 or more are **Suspicious**; 1–34 are **Review**; 0 with no signals is **Clear**. Heuristics never automatically confirm compromise. Home-country sign-ins skip geographic and heuristic scores while the exclusion is on; explicit Entra confirmation remains visible. Cross-user detection evaluates each event against history available at its timestamp, including that event for failure patterns; it does not retroactively mark earlier attempts.

### Home-country exclusion (configurable)

By default, sign-ins from **CZ and SK** are labelled **Excluded** and skip all heuristics. Open **⚙ Scoring settings** in the sidebar to change this:

- **Home countries:** two-letter codes or names, comma separated (e.g. `CZ, SK, AT`). Sign-ins outside these get the +10 location signal. Leave it empty to drop the location signal for known countries; unknown countries still get +5.
- **Exclude home countries from scoring:** untick it to score home-country sign-ins like any other (failure bursts, password spray from a domestic IP, new IP/device, rapid country change, Entra risk level). Being at home never adds location points. Home sign-ins with no signals are labelled **Clear**.

Settings are stored in the case file (`meta` table), apply to the whole case and rescore every sign-in immediately. Analyst marks are not changed.

Analyst confirmation sets the ranking score to 100; marking suspicious gives a minimum score of 35; dismissal sets it to 0 and removes it from the investigation board. Original signals and automatic classification remain visible. Decision history is local and single-analyst, without authenticated author attribution or tamper resistance. Adding exports keeps analyst decisions; only *Start a new case* clears them.

Country/device/IP novelty is relative to imported 72-hour data, not a long-term learned baseline. A device signal requires a Device ID column. Multi-factor authentication requirement does not prove MFA was successfully completed, so the app does not infer an MFA bypass. No live tenant queries, threat intelligence feeds, IP blocking or Entra writeback.

## Optional offline GeoIP

Country is taken from the export first. To resolve missing countries, install the optional package (`setup.bat` tries this automatically; otherwise `pip install -r requirements-geoip.txt`) and supply your own licensed GeoLite2/GeoIP2 Country or City MMDB:

```bash
# macOS/Linux
export GEOIP_DB=/absolute/path/GeoLite2-Country.mmdb
# PowerShell: $env:GEOIP_DB = 'C:\path\GeoLite2-Country.mmdb'
python app.py
```

No countries are inferred from IP prefixes. IP geolocation reflects the network exit and may differ from the user's physical location.

## Interface

Dark responsive dashboard, ranked user/IP investigation board, clickable country overview, counts, searchable activity, country/risk filters, clickable user-to-IP graph ranked by risk, per-login 72-hour investigation, weighted signal explanations, persistent marking with decision history, and filtered CSV export. Graph shows up to 24 aggregated connections; table paginates by 50; context tables show the latest 250 events. Export includes all filtered findings. All browser assets are local.

## Verification

```bash
python -m unittest discover -s tests -v   # Windows: .venv\Scripts\python -m unittest discover -s tests -v
# Optional graph aggregation checks if Node.js is installed:
node tests/network.test.cjs
node tests/geomap.test.cjs
```

The Python suite also covers the continuous case: idempotent re-import, overlapping-export merge with marks kept, duplicate rows, new-case replacement, restart persistence, v4 database migration and backups. The import dialog, add-to-case flow, assessment filters, import history and backup download were exercised in headless Chromium.

Security: loopback binding, no debug mode, same-site session cookie, CSRF-protected writes, restrictive content policy, escaped CSV content in the UI, and formula-safe CSV export. This is a single-analyst local tool; authentication, multi-user access, Graph integration and audit-grade multi-user history are not implemented.
