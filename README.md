# Aviation Capacity vs Demand Data Model

A Power BI data model for aviation operations planning: how full U.S. domestic flights actually run, which routes carry more seats than demand justifies, and how reliably each carrier delivers its scheduled departures.

**Stack:** Power BI (star schema, DAX) · Python (pandas) · pytest
**Data:** U.S. DOT / BTS T-100 Domestic Segment (Data Bank 28DS), June 2024 – May 2026

| | |
|---|---|
| Segment records processed | 904,336 raw → **818,552 clean** |
| Routes | **38,656** |
| Carriers | **95** |
| Seats / passengers | 2.14bn / 1.70bn |
| **Network load factor** | **79.4%** |

## What it answers

**How full are U.S. domestic flights?**
79.4% of seats were filled across two years of data — passengers divided by seats, the standard load-factor measure. That is the baseline every route is judged against.

**Which routes are carrying more capacity than demand justifies?**
`Capacity Gap (Seats)` computes the seats needed to carry current demand at a target load factor, then subtracts the seats actually flown. Positive means under-capacity; negative means seats are being spent where demand isn't. The target is a slicer, so the whole picture re-ranks when a planner changes the assumption from 80% to 85%.

**Is demand growing faster than capacity?**
`Passengers YoY %` against `Seats YoY %`. Where demand growth outruns capacity growth, routes are tightening and will need seats added.

**How reliably does each carrier fly what it schedules?**
`Completion Rate` compares departures performed against departures scheduled, capped per record so extra sections can't mask cancellations elsewhere. Carriers here stand in for third-party operators on contract.

## How it works

```
BTS Data Bank 28 ZIPs ──► scripts/prepare_data.py ──► star schema CSVs ──► Power BI model
  (pipe-separated,          clean · dedupe · flag       Segments (fact)      5 relationships
   no header row)           aggregate · report          Routes · Carriers · Service Class
                                                        Aircraft · Date (DAX calendar)
```

**Pipeline** (`scripts/prepare_data.py`) reads BTS Data Bank 28 files straight out of their ZIPs — both the current 28-field layout and the pre-2019 32-field one — and also accepts TranStats CSV exports. It removes duplicate records from overlapping downloads, drops cargo-only and zero-departure rows, flags records where reported passengers exceed seats, aggregates to a month × carrier × route × service class × aircraft grain, and writes a data-quality report of everything it dropped and why.

From this run (`data/processed/data_quality.txt`): 7 duplicates removed, 1,287 zero-departure rows dropped, 83,879 cargo-only rows dropped, 122 rows flagged for passengers exceeding seats.

**Tests** — eight pytest cases covering both file layouts, the filtering rules, the aggregation grain, and dimension integrity, run against a synthetic fixture rather than the real download.

**Model** — five many-to-one relationships from the fact table out to Date, Routes, Carriers, Service Class and Aircraft, plus a numeric-range parameter for the load-factor target.

**Measures** (`powerbi/measures.dax`) — load factor (both plain and distance-weighted RPM/ASM), the target-driven capacity gap and status, route priority ranking by seats misallocated, carrier completion rate and charter share, and year-over-year comparisons guarded against comparing a partial year to a full one.

## Limitations

- T-100 is monthly and aggregated. No fares, no bookings, no daily detail.
- Segment passengers measure carried traffic, not true demand: a route at 100% load factor may have turned passengers away, so demand on full routes is understated.
- `DEPARTURES_SCHEDULED` is not meaningful for charter service, so completion rate uses only records that report a schedule.
- The Data Bank format carries no carrier names or state codes; names come from a built-in list of U.S. carriers and states are parsed from city names.

## Run it

```bash
pip3 install -r requirements.txt
python3 -m pytest -q                 # 8 tests
python3 scripts/prepare_data.py      # expects BTS ZIPs in data/raw/
```

Raw and processed data stay out of the repo — download two 12-month ZIPs from the [BTS Data Bank 28DS page](https://www.bts.gov/browse-statistical-products-and-data/bts-publications/data-bank-28ds-t-100-domestic-segment-data) and drop them in `data/raw/`. [docs/BUILD_GUIDE.md](docs/BUILD_GUIDE.md) covers the Power BI side: model setup, relationships, and the measures.
