# Build Guide

Budget: one evening for steps 1–7, a second session for polish and findings.

## 0. Setup

Power BI Desktop is free but **Windows only**. On a Mac, use a Windows VM (Parallels or UTM) or a USC campus/virtual lab machine. Python 3.10+ is needed for the data prep step.

## 1. Download the data

The old TranStats query form is slow and often fails. Use the BTS **Data Bank 28DS** page instead, which offers plain ZIP downloads:
**https://www.bts.gov/browse-statistical-products-and-data/bts-publications/data-bank-28ds-t-100-domestic-segment-data**

Each file on that page holds **12 months** of data ending in the month named in the link. Two files cover two years:

| Click this link | Covers |
|---|---|
| the most recent month at the top of the list | the 12 months ending then |
| the same month one year earlier | the 12 months before that |

At the time of writing, "May 2026" and "May 2025" together cover June 2024 through May 2026. Overlapping files are fine — duplicate records are removed automatically.

Put both ZIPs in `data/raw/` without unzipping them. The pipeline reads inside ZIPs directly.

These files are pipe-separated with no header row, and the pipeline detects that. It also accepts TranStats CSV exports if you'd rather use the query form, and the two can be mixed.

Two fields aren't in the Data Bank format: carrier names (filled from a built-in list of U.S. carriers; anything unlisted keeps its code) and state abbreviations (read off the city names). Aircraft names need the optional lookup below.

Optional: on the TranStats download page, the `AIRCRAFT_TYPE` field links to a lookup table. Save it as `data/lookups/L_AIRCRAFT_TYPE.csv` and the dashboard shows aircraft names rather than numeric codes. Same for `CLASS` as `L_SERVICE_CLASS.csv`.

## 2. Run the pipeline

```bash
pip3 install -r requirements.txt
python3 -m pytest -q                # 8 tests should pass
python3 scripts/prepare_data.py
```

Open `data/processed/data_quality.txt`. Write down the row counts, period, route count, carrier count, and network load factor. You will use them in the README and on your resume.

## 3. Load into Power BI

Home > Get data > Text/CSV, one at a time: `segments`, `routes`, `carriers`, `service_class`, `aircraft`. Click **Transform Data** (not Load) and check:

| Table | Column | Type |
|---|---|---|
| segments | month_start | **Date** |
| segments | route_key, carrier_code, service_class, aircraft_type | **Text** |
| segments | seats, passengers, departures_*, freight_lbs, mail_lbs, pax_exceeds_seats_rows | Whole number |
| segments | asm, rpm, distance_mi | Decimal number |
| aircraft | aircraft_type | **Text** (must match segments) |

Rename the queries to `Segments`, `Routes`, `Carriers`, `Service Class`, `Aircraft`. Close & Apply.

## 4. Date table and relationships

1. Modeling > New table > paste the `Date` definition from `powerbi/measures.dax`.
2. Select the table > Mark as date table > column `Date`.
3. Select `Month` > Column tools > Sort by column > `Month Number`.
4. Model view, create these (all one-to-many, single direction, dimension on the "one" side):

```
'Date'[Date]                     1 ──► * Segments[month_start]
Routes[route_key]                1 ──► * Segments[route_key]
Carriers[carrier_code]           1 ──► * Segments[carrier_code]
'Service Class'[service_class]   1 ──► * Segments[service_class]
Aircraft[aircraft_type]          1 ──► * Segments[aircraft_type]
```

5. Hide the key columns in `Segments` (right-click > Hide in report view) so people slice from the dimensions.

## 5. Target parameter and measures

1. Modeling > New parameter > Numeric range. Name `LF Target`, min 0.50, max 0.95, increment 0.01, default 0.80. Keep "Add slicer" checked.
2. Home > Enter data > create an empty table called `_Measures`.
3. Paste each measure from `powerbi/measures.dax` (one per New measure).
4. Formatting: Load Factor, Completion Rate, YoY measures → Percentage, 1 decimal. Seats, Passengers, Capacity Gap → Whole number with thousands separator. `Load Factor YoY (pts)` → custom format `+0.0%;-0.0%;0.0%`.

## 6. Theme

View > Themes > Browse for themes > `powerbi/theme.json`.

## 7. Build the four pages

Put the same slicers on every page (View > Sync slicers): `Date[Year]`, `Carriers[carrier_name]` (dropdown with search), `Service Class[service_type]`, `Routes[origin_state]`.

### Page 1 — Network Overview
- Four cards across the top: **Passengers** (reference label: Passengers YoY %), **Seats** (Seats YoY %), **Load Factor** (Load Factor YoY (pts)), **Completion Rate**. Small card with `Data Through`.
- Line and clustered column chart: X = `Date[Year-Month]`, columns = Seats, Passengers, line = Load Factor.
- Clustered bar: Y = `Carriers[carrier_name]`, X = Load Factor. Visual filter: Top N = 15 by Seats.

### Page 2 — Occupancy vs Demand
- `LF Target` slicer.
- Scatter: Values = `Routes[route_key]`, X = Seats, Y = Load Factor, Size = Departures Performed. Visual filter: Top N = 300 routes by Seats. Analytics pane > Y-axis constant line, value = fx > `LF Target Value` (or type 0.8).
- Matrix heatmap: Rows = `Routes[route_key]` (Top N 20 by Seats), Columns = `Date[Year-Month]`, Values = Load Factor. Conditional formatting > Background color > Gradient, add a middle color centered at your target.
- Line chart: X = `Date[Year-Month]`, lines = Passengers YoY % and Seats YoY %. Where demand growth runs above capacity growth, routes are tightening.

### Page 3 — Priority Queue
- Card with `Title Priority` as the page title (it updates with the slicer).
- Three cards: Routes Under-capacity, Routes Balanced, Routes Over-capacity.
- Table, **route level only** (don't add carrier as a column, it changes what the rank means): `route_key`, `origin_city`, `dest_city`, Seats, Passengers, Load Factor, Capacity Gap (Seats), Capacity Status, Priority Tier, Route Priority Rank. Visual filter: Top N 50 by Priority Score. Sort by rank.
  - Capacity Status: Font color > fx > Field value > `Status Color`.
  - Capacity Gap (Seats): Data bars.
- `LF Target` slicer synced with page 2. Move it and watch routes change tier: that's your demo moment in an interview.

### Page 4 — Operator Performance
- Table: `carrier_name`, Departures Scheduled, Departures Performed, Completion Rate, Load Factor, Seats per Departure, Charter Share (Departures). Visual filter: Departures Scheduled ≥ 1,000.
- Clustered bar: Completion Rate by carrier, sorted ascending (worst first), same filter, constant line at 0.98.
- Stacked column: X = `Date[Year-Month]`, Y = Departures Performed, Legend = `Service Class[service_type]`.

## 8. Finish

1. Save as `powerbi/aviation_capacity_dashboard.pbix`. Commit it if it's under ~100 MB (GitHub's file limit).
2. Screenshot each page into `screenshots/` (01_overview.png … 04_operators.png).
3. Answer the questions in the README "Findings" section with real numbers from your dashboard.
4. Push to GitHub, pin the repo on your profile.

## Interview angle

Carriers here play the role of third-party air operators; completion rate is contract delivery; the LF target slider is a planning assumption someone like a unit chief would set. If you want a closer analog to field missions, filter `origin_state = AK`: Alaska regional flying is small aircraft, remote strips, and heavy charter use.
