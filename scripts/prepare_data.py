"""
Prepare BTS T-100 Domestic Segment data for the Power BI capacity dashboard.

Input : raw T-100 files in data/raw/, in either format (can be mixed):
          - BTS "Data Bank 28DS" ZIPs (pipe-separated, no header), e.g.
            DB28SEG.DD.WAC.202501.202512.REL01.03MAR2026.zip  <- easiest, direct download
          - TranStats CSV exports (with a header row), .csv or .zip
Output: star-schema tables in data/processed/
    segments.csv       fact table, grain = month x carrier x route x service class x aircraft type
    routes.csv         route dimension (directional, e.g. JFK-LAX)
    carriers.csv       carrier dimension
    service_class.csv  service class dimension
    aircraft.csv       aircraft type dimension
    data_quality.txt   what was dropped or flagged, and why

Usage:
    python scripts/prepare_data.py
    python scripts/prepare_data.py --raw data/raw --out data/processed
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

import pandas as pd

# Columns the pipeline needs after reading either format.
REQUIRED = [
    "YEAR", "MONTH",
    "UNIQUE_CARRIER",
    "ORIGIN", "ORIGIN_CITY_NAME",
    "DEST", "DEST_CITY_NAME",
    "DISTANCE", "SEATS", "PASSENGERS",
    "DEPARTURES_SCHEDULED", "DEPARTURES_PERFORMED",
    "CLASS", "AIRCRAFT_TYPE",
]
OPTIONAL = ["UNIQUE_CARRIER_NAME", "ORIGIN_STATE_ABR", "DEST_STATE_ABR", "FREIGHT", "MAIL"]

NUMERIC = ["DISTANCE", "SEATS", "PASSENGERS", "DEPARTURES_SCHEDULED",
           "DEPARTURES_PERFORMED", "FREIGHT", "MAIL"]

# Data Bank 28 segment record layout (BTS "File and Record Description - Data Bank 28 Segment Data").
# Current layout: 28 pipe-separated fields. Pre-Oct-2019 layout: 32 fields (extra zeroed cabin columns).
DB28_FIELDS_28 = [
    "YEAR", "MONTH", "ORIGIN", "ORIGIN_CITY_CODE", "ORIGIN_WAC", "ORIGIN_CITY_NAME",
    "DEST", "DEST_CITY_CODE", "DEST_WAC", "DEST_CITY_NAME",
    "UNIQUE_CARRIER", "CARRIER_ENTITY", "CARRIER_GROUP", "DISTANCE", "CLASS",
    "AIRCRAFT_GROUP", "AIRCRAFT_TYPE", "AIRCRAFT_CONFIG",
    "DEPARTURES_PERFORMED", "DEPARTURES_SCHEDULED", "PAYLOAD", "SEATS",
    "PASSENGERS", "FREIGHT", "MAIL", "RAMP_TO_RAMP", "AIR_TIME", "CARRIER_WAC",
]
DB28_FIELDS_32 = (
    DB28_FIELDS_28[:22] + ["SEATS_MIDDLE", "SEATS_COACH", "PASSENGERS",
                           "PASSENGERS_MIDDLE", "PASSENGERS_COACH"] + DB28_FIELDS_28[23:]
)

# Data Bank files carry carrier codes only. Names for common U.S. carriers; others show their code.
CARRIER_NAMES = {
    "AA": "American Airlines", "DL": "Delta Air Lines", "UA": "United Airlines",
    "WN": "Southwest Airlines", "B6": "JetBlue Airways", "AS": "Alaska Airlines",
    "NK": "Spirit Airlines", "F9": "Frontier Airlines", "G4": "Allegiant Air",
    "HA": "Hawaiian Airlines", "SY": "Sun Country Airlines", "MX": "Breeze Airways",
    "XP": "Avelo Airlines", "OO": "SkyWest Airlines", "YX": "Republic Airways",
    "9E": "Endeavor Air", "MQ": "Envoy Air", "OH": "PSA Airlines", "YV": "Mesa Airlines",
    "QX": "Horizon Air", "ZW": "Air Wisconsin", "G7": "GoJet Airlines", "C5": "CommuteAir",
    "PT": "Piedmont Airlines", "9K": "Cape Air", "3M": "Silver Airways",
    "5X": "UPS Airlines", "FX": "FedEx Express", "5Y": "Atlas Air",
}
# Default service class labels. If data/lookups/L_SERVICE_CLASS.csv exists it overrides these.
SERVICE_CLASS_DEFAULT = {
    "F": "Scheduled passenger/cargo",
    "G": "Scheduled all-cargo",
    "L": "Non-scheduled (charter) passenger/cargo",
    "P": "Non-scheduled (charter) all-cargo",
}

DISTANCE_BANDS = [
    (0, 500, "1. Under 500 mi"),
    (500, 1000, "2. 500-999 mi"),
    (1000, 2000, "3. 1,000-1,999 mi"),
    (2000, float("inf"), "4. 2,000+ mi"),
]


def read_raw(raw_dir: Path) -> pd.DataFrame:
    files = sorted([*raw_dir.glob("*.csv"), *raw_dir.glob("*.zip")])
    if not files:
        sys.exit(f"No .csv or .zip files found in {raw_dir}. Download T-100 data first (see docs/BUILD_GUIDE.md).")

    frames = []
    for f in files:
        header = pd.read_csv(f, nrows=0)
        colmap = {c: c.strip().upper() for c in header.columns}
        available = set(colmap.values())
        missing = [c for c in REQUIRED if c not in available]
        if missing:
            sys.exit(f"{f.name} is missing required columns: {missing}. Re-download with these fields selected.")
        wanted = [orig for orig, norm in colmap.items() if norm in REQUIRED + OPTIONAL]
        df = pd.read_csv(f, usecols=wanted, dtype=str, low_memory=False).rename(columns=colmap)
        df["SOURCE_FILE"] = f.name
        frames.append(df)
        print(f"  read {f.name}: {len(df):,} rows")

    df = pd.concat(frames, ignore_index=True)
    for c in OPTIONAL:
        if c not in df.columns:
            df[c] = "0"
    return df


def _open_text(f: Path):
    """Return (text_handle, display_name). Handles plain files and ZIPs (largest member)."""
    if f.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(f)
        members = [m for m in zf.infolist() if not m.is_dir()]
        if not members:
            sys.exit(f"{f.name} is an empty ZIP.")
        m = max(members, key=lambda x: x.file_size)
        return io.TextIOWrapper(zf.open(m), encoding="latin-1", newline=""), f"{f.name}/{m.filename}"
    return open(f, encoding="latin-1", newline=""), f.name


def _first_line(f: Path) -> str:
    fh, _ = _open_text(f)
    with fh:
        return fh.readline().strip()


def _read_db28(f: Path, first: str) -> pd.DataFrame:
    """Pipe-separated Data Bank 28 file. Detects header row, trailing pipe, and 28 vs 32 field layout."""
    parts = first.split("|")
    has_header = not parts[0].strip().isdigit()
    n = len(parts) - (1 if parts[-1].strip() == "" else 0)
    if has_header:
        fh, name = _open_text(f)
        with fh:
            df = pd.read_csv(fh, sep="|", dtype=str, low_memory=False)
        df.columns = [c.strip().upper() for c in df.columns]
        df = df.loc[:, ~df.columns.str.startswith("UNNAMED")]
        return df, name
    if n == 28:
        names = DB28_FIELDS_28
    elif n == 32:
        names = DB28_FIELDS_32
    else:
        sys.exit(f"{f.name}: expected 28 or 32 pipe-separated fields, found {n}.\n"
                 f"First line was:\n  {first[:300]}\nSend this line to get the layout fixed.")
    fh, name = _open_text(f)
    with fh:
        df = pd.read_csv(fh, sep="|", header=None, dtype=str, low_memory=False,
                         usecols=range(n), names=names)
    return df, name


def _read_csv_export(f: Path) -> pd.DataFrame:
    fh, name = _open_text(f)
    with fh:
        df = pd.read_csv(fh, dtype=str, low_memory=False)
    df.columns = [c.strip().upper() for c in df.columns]
    df = df.loc[:, ~df.columns.str.startswith("UNNAMED")]
    return df, name


def read_raw(raw_dir: Path) -> pd.DataFrame:
    files = sorted(p for p in raw_dir.iterdir()
                   if p.is_file() and not p.name.startswith(".")
                   and p.suffix.lower() in (".csv", ".zip", ".txt", ".asc", ".dat"))
    if not files:
        sys.exit(f"No data files found in {raw_dir}. Download T-100 data first (see docs/BUILD_GUIDE.md).")

    frames = []
    for f in files:
        first = _first_line(f)
        if "|" in first:
            df, name = _read_db28(f, first)
            fmt = "Data Bank 28 (pipe)"
        else:
            df, name = _read_csv_export(f)
            fmt = "TranStats CSV"
        missing = [c for c in REQUIRED if c not in df.columns]
        if missing:
            sys.exit(f"{name} is missing required columns: {missing}.")
        df = df[[c for c in REQUIRED + OPTIONAL if c in df.columns]].copy()
        df["SOURCE_FILE"] = f.name
        frames.append(df)
        print(f"  read {name} [{fmt}]: {len(df):,} rows")

    df = pd.concat(frames, ignore_index=True)
    for c in REQUIRED + OPTIONAL:
        df[c] = df[c].astype("string").str.strip() if c in df.columns else pd.NA

    # Fill what the Data Bank format doesn't carry
    df["UNIQUE_CARRIER"] = df["UNIQUE_CARRIER"].str.upper()
    df["UNIQUE_CARRIER_NAME"] = (df["UNIQUE_CARRIER_NAME"]
                                 .fillna(df["UNIQUE_CARRIER"].map(CARRIER_NAMES))
                                 .fillna(df["UNIQUE_CARRIER"]))
    for side in ("ORIGIN", "DEST"):
        city = df[f"{side}_CITY_NAME"].fillna("")
        state_from_city = city.str.extract(r",\s*([A-Za-z]{2})\s*$")[0].str.upper()
        df[f"{side}_STATE_ABR"] = df[f"{side}_STATE_ABR"].fillna(state_from_city).fillna("")
    for c in ("FREIGHT", "MAIL"):
        df[c] = df[c].fillna("0")

    # Sanity check: catches a layout mismatch before it silently produces nonsense
    yrs = pd.to_numeric(df["YEAR"], errors="coerce")
    if yrs.between(1990, 2100).mean() < 0.95:
        sys.exit("YEAR column doesn't look like years - the file layout was not recognised. "
                 "Send the first line of the raw file to get it fixed.")
    return df.astype(object).where(df.notna(), None)


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    report = [f"Raw rows: {len(df):,}"]

    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    for c in ["UNIQUE_CARRIER", "ORIGIN", "DEST", "CLASS", "AIRCRAFT_TYPE"]:
        df[c] = df[c].fillna("").str.strip().str.upper()
    df["YEAR"] = pd.to_numeric(df["YEAR"], errors="coerce")
    df["MONTH"] = pd.to_numeric(df["MONTH"], errors="coerce")

    # Exact duplicate rows across overlapping downloads
    before = len(df)
    df = df.drop_duplicates(subset=[c for c in df.columns if c != "SOURCE_FILE"])
    report.append(f"Dropped exact duplicates (overlapping files): {before - len(df):,}")

    bad_date = df["YEAR"].isna() | df["MONTH"].isna() | ~df["MONTH"].between(1, 12)
    report.append(f"Dropped rows with invalid year/month: {int(bad_date.sum()):,}")
    df = df[~bad_date]

    no_flights = df["DEPARTURES_PERFORMED"] <= 0
    report.append(f"Dropped rows with zero departures performed: {int(no_flights.sum()):,}")
    df = df[~no_flights]

    no_seats = df["SEATS"] <= 0
    report.append(f"Dropped rows with zero seats (cargo-only / no passenger capacity): {int(no_seats.sum()):,}")
    df = df[~no_seats]

    over = df["PASSENGERS"] > df["SEATS"]
    report.append(f"Flagged rows with passengers > seats (kept, flagged as data issue): {int(over.sum()):,}")
    df = df.assign(PAX_EXCEEDS_SEATS=over.astype(int))

    report.append(f"Clean rows: {len(df):,}")
    return df, report


def build_tables(df: pd.DataFrame, lookups_dir: Path) -> dict[str, pd.DataFrame]:
    df = df.copy()
    df["month_start"] = pd.to_datetime(
        dict(year=df["YEAR"].astype(int), month=df["MONTH"].astype(int), day=1)
    )
    df["route_key"] = df["ORIGIN"] + "-" + df["DEST"]

    # Fact table: aggregate to month x carrier x route x class x aircraft type
    keys = ["month_start", "UNIQUE_CARRIER", "route_key", "CLASS", "AIRCRAFT_TYPE"]
    fact = (
        df.groupby(keys, as_index=False)
        .agg(
            departures_scheduled=("DEPARTURES_SCHEDULED", "sum"),
            departures_performed=("DEPARTURES_PERFORMED", "sum"),
            seats=("SEATS", "sum"),
            passengers=("PASSENGERS", "sum"),
            freight_lbs=("FREIGHT", "sum"),
            mail_lbs=("MAIL", "sum"),
            distance_mi=("DISTANCE", "max"),
            pax_exceeds_seats_rows=("PAX_EXCEEDS_SEATS", "sum"),
        )
        .rename(columns={"UNIQUE_CARRIER": "carrier_code", "CLASS": "service_class",
                         "AIRCRAFT_TYPE": "aircraft_type"})
    )
    # Distance-weighted capacity and traffic (industry-standard load factor = RPM / ASM)
    fact["asm"] = fact["seats"] * fact["distance_mi"]
    fact["rpm"] = fact["passengers"] * fact["distance_mi"]
    fact["month_start"] = fact["month_start"].dt.strftime("%Y-%m-%d")

    # Route dimension
    routes = (
        df.sort_values("month_start")
        .groupby("route_key", as_index=False)
        .agg(
            origin=("ORIGIN", "last"),
            origin_city=("ORIGIN_CITY_NAME", "last"),
            origin_state=("ORIGIN_STATE_ABR", "last"),
            dest=("DEST", "last"),
            dest_city=("DEST_CITY_NAME", "last"),
            dest_state=("DEST_STATE_ABR", "last"),
            distance_mi=("DISTANCE", "max"),
        )
    )
    # Non-directional market, e.g. JFK-LAX and LAX-JFK both -> JFK-LAX
    routes["market"] = routes.apply(lambda r: "-".join(sorted([r["origin"], r["dest"]])), axis=1)
    routes["distance_band"] = routes["distance_mi"].apply(_band)

    # Carrier dimension (latest name wins if a carrier renamed)
    carriers = (
        df.sort_values("month_start")
        .groupby("UNIQUE_CARRIER", as_index=False)
        .agg(carrier_name=("UNIQUE_CARRIER_NAME", "last"))
        .rename(columns={"UNIQUE_CARRIER": "carrier_code"})
    )

    # Service class dimension
    sc_labels = _load_lookup(lookups_dir / "L_SERVICE_CLASS.csv") or SERVICE_CLASS_DEFAULT
    service_class = pd.DataFrame({"service_class": sorted(fact["service_class"].unique())})
    service_class["service_class_name"] = service_class["service_class"].map(sc_labels).fillna("Other (see BTS lookup)")
    service_class["service_type"] = service_class["service_class"].map(
        lambda c: "Scheduled" if c in ("F", "G") else ("Charter" if c in ("L", "P") else "Other")
    )

    # Aircraft dimension (names only if the BTS lookup file is provided)
    ac_labels = _load_lookup(lookups_dir / "L_AIRCRAFT_TYPE.csv") or {}
    aircraft = pd.DataFrame({"aircraft_type": sorted(fact["aircraft_type"].unique())})
    aircraft["aircraft_name"] = aircraft["aircraft_type"].map(ac_labels).fillna("Type " + aircraft["aircraft_type"])

    return {"segments": fact, "routes": routes, "carriers": carriers,
            "service_class": service_class, "aircraft": aircraft}


def _band(miles: float) -> str:
    for lo, hi, label in DISTANCE_BANDS:
        if lo <= miles < hi:
            return label
    return "Unknown"


def _load_lookup(path: Path) -> dict[str, str] | None:
    """BTS lookup tables have two columns: Code, Description."""
    if not path.exists():
        return None
    lk = pd.read_csv(path, dtype=str)
    return dict(zip(lk.iloc[:, 0].str.strip().str.upper(), lk.iloc[:, 1].str.strip()))


def main(argv: list[str] | None = None) -> None:
    root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--raw", type=Path, default=root / "data" / "raw")
    p.add_argument("--out", type=Path, default=root / "data" / "processed")
    p.add_argument("--lookups", type=Path, default=root / "data" / "lookups")
    args = p.parse_args(argv)

    print("Reading raw files...")
    df = read_raw(args.raw)
    df, report = clean(df)
    tables = build_tables(df, args.lookups)

    args.out.mkdir(parents=True, exist_ok=True)
    for name, t in tables.items():
        t.to_csv(args.out / f"{name}.csv", index=False)
        report.append(f"Wrote {name}.csv: {len(t):,} rows")

    fact = tables["segments"]
    lf = fact["passengers"].sum() / fact["seats"].sum()
    report += [
        "",
        f"Period: {fact['month_start'].min()} to {fact['month_start'].max()}",
        f"Carriers: {len(tables['carriers']):,} | Routes: {len(tables['routes']):,}",
        f"Total seats: {fact['seats'].sum():,.0f} | Total passengers: {fact['passengers'].sum():,.0f}",
        f"Network load factor (pax/seats): {lf:.1%}",
    ]
    (args.out / "data_quality.txt").write_text("\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main()
