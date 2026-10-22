"""[cl] reads a solved fleet out of results.db and writes it as a .dd

the loop uses it to turn a solve into the fleet for the next solve: the
starting fleet (fleet_from_inputs), and the fleet after each expansion.
the .dd has the par_fix_* parameters that fix_fleet and fleet_floor read in
highres.gms, plus the fleet_zones set.

reads results.db (not results.gdx) since the solve already converts to it

Usage:
    python3 caps2dd.py --from <results.db> --to <fixed_caps.dd> [--zones AT,CZ]
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data2dd_funcs import wrapdd  # noqa: E402

# [cl] same rounding as the rest of the workflow
ROUNDDP = 8

# [cl] anything under 0.1 MW is solver dust, not a real asset. the barrier
# leaves values like 1e-8 GW, and fixing a fleet from 1e-8 to 1e5 GW makes the
# solve unstable. applied per asset, see floor_fleet.
# same limit for negatives: under it they're dust, over it something is wrong
MIN_CAPACITY_GW = 1e-4

# [cl] value columns in every variable table from gamstool sqlitewrite,
# the other columns are the sets (zone, tech, ...)
VALUE_COLS = {"level", "marginal", "lower", "upper", "scale"}

# [cl] table in results.db -> parameter name in the fix_fleet block
FLEET = {
    "var_new_pcap_z": "par_fix_new_pcap_z",
    "var_exist_pcap_z": "par_fix_exist_pcap_z",
    "var_new_vre_pcap_r": "par_fix_new_vre_pcap_r",
    "var_exist_vre_pcap_r": "par_fix_exist_vre_pcap_r",
    "var_new_store_pcap_z": "par_fix_new_store_pcap_z",
    "var_exist_store_pcap_z": "par_fix_exist_store_pcap_z",
    "var_new_store_ecap_z": "par_fix_new_store_ecap_z",
    "var_exist_store_ecap_z": "par_fix_exist_store_ecap_z",
    "var_new_hydro_ecap_z": "par_fix_new_hydro_ecap_z",
    "var_exist_hydro_ecap_z": "par_fix_exist_hydro_ecap_z",
    "var_new_trans_pcap": "par_fix_new_trans_pcap",
    "var_exist_trans_pcap": "par_fix_exist_trans_pcap",
}

def table_exists(con, name):
    """[cl] is this table in the database"""
    q = "select 1 from sqlite_master where type='table' and name=?"
    return con.execute(q, (name,)).fetchone() is not None


def fmt(v):
    # [cl] fixed-point, GAMS doesn't like some number formats
    # values are written exactly as solved. rounding them to 1 kW was tried and
    # broke Li-ion-8 (energy = 8 x power, so rounding power down cuts the energy
    # limit 8x more). floor_tol in highres.gms handles the floors instead
    return f"{v:.{ROUNDDP}f}"


def read_table(con, table):
    """[cl] one variable table as {"AT.Solar": level, ...}, None if the table is missing"""
    if not table_exists(con, table):
        print(f"  {table:<26} MISSING - skipped", file=sys.stderr)
        return None

    df = pd.read_sql_query(f"select * from [{table}]", con)
    dims = [c for c in df.columns if c not in VALUE_COLS]
    if not dims:
        raise ValueError(f"{table} has no set dimensions")
    # [cl] a variable with no records (all zero) comes out with only the set
    # columns
    if "level" not in df.columns:
        return {}

    df = df[dims + ["level"]].copy()
    df["level"] = df["level"].astype(float).round(ROUNDDP)


    # [cl] drop zeros, GAMS treats missing entries as zero anyway and fix_fleet
    # covers everything, so they still get fixed to zero
    df = df[df["level"] != 0.0]
    if df.empty:
        return {}
    keys = df[dims].astype(str).agg(".".join, axis=1)
    return dict(zip(keys, df["level"]))


def block_for(values, parname):
    """[cl] one parameter block of the .dd

    an empty table still gets an (empty) block. if it got none, GAMS would see
    the parameter declared but never assigned, same as a typo in its name, and
    we want that check to only catch real mistakes
    """
    if not values:
        rows = np.empty((0, 2), dtype=object)
    else:
        rows = np.array([[k, fmt(v)] for k, v in sorted(values.items())], dtype=object)
    return wrapdd(rows, parname, "parameter", outfile="")


def floor_fleet(data, floor):
    """[cl] remove dust: clamp small negatives to zero, drop assets under the floor

    works per asset (new or existing, zone, tech), not per entry. storage power
    and energy are linked (Li-ion-8: energy <= 8 x power), so dropping one and
    keeping the other is infeasible. if an asset's biggest part is under the
    floor all of it goes, otherwise all of it stays
    returns how many entries were dropped and clamped
    """
    # [cl] group entries by asset: storage and hydro have power and energy in
    # separate parameters but the same key (zone.tech)
    groups = {}
    for par, values in data.items():
        for key, val in values.items():
            vintage = "new" if "_new_" in par else "exist"
            groups.setdefault((vintage, key), []).append((par, key, val))

    # [cl] negatives first. the barrier leaves things like -3e-08 on positive
    # variables, and fixing one to a negative value is infeasible (and the
    # error shows up somewhere unrelated)
    clamped, bad = 0, []
    for par, values in data.items():
        for key, val in list(values.items()):
            if val >= 0.0:
                continue
            if abs(val) >= floor:
                bad.append(f"{par} {key} = {val}")
            else:
                values[key] = 0.0
                clamped += 1
    if bad:
        raise ValueError(
            "capacities more negative than the floor (" + str(-floor) + "):\n  "
            + "\n  ".join(bad)
            + "\nThese are declared positive variables, so this is not dust. "
              "Check the solve."
        )

    # [cl] then drop assets where every part is under the floor
    dropped = 0
    for (_vintage, _key), members in groups.items():
        if max(abs(v) for _p, _k, v in members) < floor:
            for par, key, _v in members:
                if key in data[par]:
                    del data[par][key]
                    dropped += 1
    return dropped, clamped


def main():
    # Parses the command line
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="src", required=True, help="solved results.db")
    ap.add_argument("--to", dest="dst", required=True, help="output .dd file")
    ap.add_argument("--zones", default="",
                    help="comma separated zones this fleet covers, written as "
                         "the fleet_zones set. Empty means the whole model")
    ap.add_argument("--floor", type=float, default=MIN_CAPACITY_GW,
                    help="drop assets below this many GW as solver dust "
                         f"(default {MIN_CAPACITY_GW}); applied per asset, so "
                         "storage power and energy go together")
    args = ap.parse_args()

    # Gives the pathlib.Path objects for the input and output files, and checks that the input exists.
    src, dst = Path(args.src), Path(args.dst)
    if not src.is_file():
        sys.exit(f"no results.db at {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Connect to the sqlite database in read-only mode and extract the parameter blocks.
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        print(f"reading {src}")
        data, missing = {}, []
        for table, parname in FLEET.items():
            values = read_table(con, table)
            if values is None:
                missing.append(table)
                continue
            data[parname] = values
    finally:
        con.close()

    # [cl] remove the dust, then build one block per parameter
    dropped, clamped = floor_fleet(data, args.floor)
    if clamped:
        print(f"  {clamped} negative dust entries clamped to zero")
    if dropped:
        print(f"  {dropped} entries below {args.floor} dropped as dust")

    blocks, counts = [], {}
    for table, parname in FLEET.items():
        if parname not in data:
            continue
        blocks.append(block_for(data[parname], parname))
        counts[table] = len(data[parname])

    header = [
        "* Fleet fixed for a Climate-Robust Pathways dispatch solve.",
        f"* Generated by caps2dd.py from {src}",
        "* Values are in the model's internal units, GW-based. See MWtoGW.",
        "",
    ]

    # [cl] write the header, the fleet_zones set, then the blocks
    zones = [z.strip() for z in args.zones.split(",") if z.strip()]
    with open(dst, "w") as f:
        f.write("\n".join(header))
        f.write("set\nfleet_zones /\n" + "\n".join(sorted(zones)) + "\n/\n\n")
        for block in blocks:
            np.savetxt(f, block, delimiter=" ", fmt="%s")

    print(f"\nwrote {dst}")
    for table, n in counts.items():
        print(f"  {table:<26} {n:>7} nonzero")


if __name__ == "__main__":
    main()
