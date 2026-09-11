# Aviation Capacity vs Demand Dashboard

Power BI dashboard for aviation operations planning: where seat capacity matches passenger demand, which routes need capacity added or pulled back first, and how reliably each operator delivers its scheduled flights.

**Stack:** Power BI (DAX, star schema) · Python (pandas) · pytest
**Data:** U.S. DOT / BTS T-100 Domestic Segment (Data Bank 28DS), `[PERIOD]` — `[N]` segment records, `[N]` routes, `[N]` carriers

![Overview](screenshots/01_overview.png)

## What it answers

| Page | Question |
|---|---|
| Network Overview | How much capacity was flown, how full was it, and how is that changing year over year? |
| Occupancy vs Demand | Which routes run above or below a target load factor, and is demand growing faster than capacity? |
| Priority Queue | Given a load factor target, which routes have the most seats misallocated, ranked into P1/P2/P3 tiers? |
| Operator Performance | Which operators complete their scheduled departures, and how much of their flying is charter? |

## How it works

```
BTS T-100 CSVs ──► scripts/prepare_data.py ──► star schema CSVs ──► Power BI model ──► 4-page report
                   (clean, dedupe, flag,         Segments (fact)
                    aggregate, quality report)   Routes · Carriers · Service Class · Aircraft · Date
```

**Data pipeline** (`scripts/prepare_data.py`): reads BTS Data Bank 28 pipe-separated files (both the current 28-field and pre-2019 32-field layouts) straight out of their ZIPs, or TranStats CSV exports. It removes duplicate rows from overlapping downloads, drops cargo-only and zero-departure records, and flags (keeps) records where reported passengers exceed seats. Eight pytest tests cover both file layouts, filtering, aggregation grain, and dimension integrity.

**Key DAX** (`powerbi/measures.dax`):
- `Load Factor` and distance-weighted `Load Factor (RPM/ASM)`
- `Capacity Gap (Seats)`: seats needed to carry current demand at the target load factor, minus seats flown
- `Route Priority Rank` / `Priority Tier`: ranks routes by absolute seats misallocated, so volume matters, not just percentage
- `Completion Rate`: departures performed vs scheduled, capped per record so extra sections can't mask cancellations
- Year-over-year measures guarded against comparing a partial year to a full one

## Findings

<!-- Replace each line with a real result from your dashboard. Numbers only from your own data. -->
1. Network load factor was `[X]%` in `[YEAR]`, `[up/down]` `[X]` pts year over year; passengers grew `[X]%` vs seats `[X]%`.
2. At an 80% target, `[N]` routes are under-capacity and `[N]` over-capacity; the top 10 priority routes account for `[N]` misallocated seats.
3. Seasonal pattern: `[month]` peaks at `[X]%` load factor while `[month]` troughs at `[X]%`.
4. Operator completion rates range from `[X]%` to `[X]%` among carriers with 1,000+ scheduled departures.

## Limitations

- T-100 is monthly and aggregated; it has no fares, bookings, or daily detail.
- Segment passengers measure carried traffic, not true demand: a route at 100% load factor may have turned passengers away (spill), so demand on full routes is understated.
- `DEPARTURES_SCHEDULED` is not meaningful for non-scheduled (charter) service; completion rate uses only records with scheduled departures.

## Run it

```bash
pip3 install -r requirements.txt
python3 -m pytest -q
python3 scripts/prepare_data.py   # expects raw BTS files in data/raw/
```

Then follow [docs/BUILD_GUIDE.md](docs/BUILD_GUIDE.md) to rebuild the report, or open `powerbi/aviation_capacity_dashboard.pbix`.
