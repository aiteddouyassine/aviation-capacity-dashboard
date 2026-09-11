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
