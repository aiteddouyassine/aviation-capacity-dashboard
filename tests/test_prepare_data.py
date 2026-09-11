"""Tests use a tiny synthetic file shaped like the BTS T-100 export (not real data)."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_data as pdp  # noqa: E402

HEADER = ("DEPARTURES_SCHEDULED,DEPARTURES_PERFORMED,PAYLOAD,SEATS,PASSENGERS,FREIGHT,MAIL,DISTANCE,"
          "UNIQUE_CARRIER,UNIQUE_CARRIER_NAME,ORIGIN,ORIGIN_CITY_NAME,ORIGIN_STATE_ABR,"
          "DEST,DEST_CITY_NAME,DEST_STATE_ABR,AIRCRAFT_TYPE,CLASS,YEAR,MONTH,")  # BTS files end with a trailing comma

ROWS = [
    # normal scheduled rows, same route/month/carrier split across two aircraft configs -> should aggregate
    "30,30,0,4500,3600,0,0,2475,AA,American Airlines Inc.,JFK,\"New York, NY\",NY,LAX,\"Los Angeles, CA\",CA,614,F,2025,1,",
    "30,29,0,4350,3900,0,0,2475,AA,American Airlines Inc.,JFK,\"New York, NY\",NY,LAX,\"Los Angeles, CA\",CA,614,F,2025,1,",
    # exact duplicate of the row above (overlapping download) -> dropped
    "30,29,0,4350,3900,0,0,2475,AA,American Airlines Inc.,JFK,\"New York, NY\",NY,LAX,\"Los Angeles, CA\",CA,614,F,2025,1,",
    # charter row
    "0,4,0,600,300,0,0,1200,XP,Avelo,BUR,\"Burbank, CA\",CA,ANC,\"Anchorage, AK\",AK,622,L,2025,2,",
    # cargo-only (no seats) -> dropped
    "10,10,0,0,0,50000,0,500,5X,UPS,SDF,\"Louisville, KY\",KY,ORD,\"Chicago, IL\",IL,888,G,2025,1,",
    # zero departures performed -> dropped
    "5,0,0,0,0,0,0,300,AA,American Airlines Inc.,JFK,\"New York, NY\",NY,BOS,\"Boston, MA\",MA,614,F,2025,1,",
    # passengers > seats (data issue) -> kept and flagged
    "2,2,0,100,120,0,0,300,AA,American Airlines Inc.,JFK,\"New York, NY\",NY,BOS,\"Boston, MA\",MA,614,F,2025,1,",
]


@pytest.fixture
def run(tmp_path):
    raw, out, lk = tmp_path / "raw", tmp_path / "out", tmp_path / "lookups"
    raw.mkdir()
    lk.mkdir()
    (raw / "t100_sample.csv").write_text(HEADER + "\n" + "\n".join(ROWS) + "\n")
    pdp.main(["--raw", str(raw), "--out", str(out), "--lookups", str(lk)])
    return {name: pd.read_csv(out / f"{name}.csv") for name in
            ["segments", "routes", "carriers", "service_class", "aircraft"]} | {"out": out}


def test_filters_and_dedup(run):
    seg = run["segments"]
    assert "SDF-ORD" not in set(seg["route_key"])          # cargo-only dropped
    assert seg["seats"].gt(0).all()
    assert seg["departures_performed"].gt(0).all()


def test_aggregates_to_grain(run):
    seg = run["segments"]
    jfk_lax = seg[seg["route_key"] == "JFK-LAX"]
    assert len(jfk_lax) == 1                                 # two configs -> one row, duplicate removed
    row = jfk_lax.iloc[0]
    assert row["seats"] == 4500 + 4350
    assert row["passengers"] == 3600 + 3900
    assert row["asm"] == row["seats"] * 2475
    assert row["rpm"] == row["passengers"] * 2475


def test_flags_pax_over_seats(run):
    seg = run["segments"]
    assert seg.loc[seg["route_key"] == "JFK-BOS", "pax_exceeds_seats_rows"].iloc[0] == 1


def test_dimensions_cover_fact_keys(run):
    seg = run["segments"]
    assert set(seg["route_key"]) <= set(run["routes"]["route_key"])
    assert set(seg["carrier_code"]) <= set(run["carriers"]["carrier_code"])
    assert set(seg["service_class"]) <= set(run["service_class"]["service_class"])
    assert run["routes"]["route_key"].is_unique
    assert run["carriers"]["carrier_code"].is_unique


def test_service_type_and_market(run):
    sc = run["service_class"].set_index("service_class")
    assert sc.loc["F", "service_type"] == "Scheduled"
    assert sc.loc["L", "service_type"] == "Charter"
    r = run["routes"].set_index("route_key")
    assert r.loc["BUR-ANC", "market"] == "ANC-BUR"


def test_quality_report_written(run):
    text = (run["out"] / "data_quality.txt").read_text()
    assert "Dropped exact duplicates" in text and "passengers > seats" in text


# --- Data Bank 28 pipe-separated format (direct BTS ZIP downloads) ---

# Field order per BTS "File and Record Description - Data Bank 28 Segment Data".
DB28_ROWS = [
    # trailing pipe, as BTS emits it
    "2025|1|JFK|31703|22|New York, NY|LAX|32575|91|Los Angeles, CA|AA|09031|3|2475|F|6|614|1|30|30|900000|4500|3600|0|0|18000|15000|10|",
    # cargo-only, no seats -> dropped
    "2025|1|SDF|31454|43|Louisville, KY|ORD|30977|41|Chicago, IL|5X|55555|8|500|G|7|888|2|10|10|300000|0|0|50000|0|1000|800|10|",
]
# Pre-Oct-2019 layout: zeroed middle/coach cabin columns split seats from passengers.
DB28_LEGACY_ROW = (
    "2018|3|JFK|31703|22|New York, NY|LAX|32575|91|Los Angeles, CA|AA|09031|3|2475|F|6|614|1|"
    "30|30|900000|4500|0|0|3600|0|0|0|0|18000|15000|10"
)


def _run_pipeline(tmp_path, filename, text):
    raw, out, lk = tmp_path / "raw", tmp_path / "out", tmp_path / "lookups"
    raw.mkdir()
    lk.mkdir()
    path = raw / filename
    if filename.endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(filename.replace(".zip", ".asc"), text)
    else:
        path.write_text(text)
    pdp.main(["--raw", str(raw), "--out", str(out), "--lookups", str(lk)])
    return pd.read_csv(out / "segments.csv"), pd.read_csv(out / "carriers.csv"), pd.read_csv(out / "routes.csv")


def test_reads_zipped_db28_pipe_file(tmp_path):
    seg, carriers, routes = _run_pipeline(
        tmp_path, "DB28SEG.DD.WAC.202501.202512.REL01.03MAR2026.zip", "\n".join(DB28_ROWS) + "\n"
    )
    assert len(seg) == 1                                   # cargo-only row dropped
    row = seg.iloc[0]
    assert (row["seats"], row["passengers"], row["distance_mi"]) == (4500, 3600, 2475)
    assert row["route_key"] == "JFK-LAX" and row["service_class"] == "F"
    # carrier names and states aren't in this format; they're filled in
    assert carriers.set_index("carrier_code").loc["AA", "carrier_name"] == "American Airlines"
    assert routes.set_index("route_key").loc["JFK-LAX", "origin_state"] == "NY"


def test_reads_legacy_32_field_layout(tmp_path):
    seg, _, _ = _run_pipeline(tmp_path, "legacy.txt", DB28_LEGACY_ROW + "\n")
    row = seg.iloc[0]
    assert (row["seats"], row["passengers"]) == (4500, 3600)  # not shifted by the zeroed cabin columns
