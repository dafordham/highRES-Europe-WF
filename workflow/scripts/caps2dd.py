"""Extract a solved fleet from a highRES results.db and write it as a .dd file.

Climate-Robust Pathways. The pathway loop carries a fleet from one step to the
next and re-dispatches it against many climate realisations, so a solved system
has to become an input. This reads the capacity variables out of a solved run
and emits the parameters that the fix_fleet block in highres.gms expects.

Reads results.db rather than results.gdx: the workflow already produces it via
`gamstool sqlitewrite`.

Usage:
    python3 caps2dd.py --from <results.db> --to <fixed_caps.dd>
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data2dd_funcs import wrapdd  # noqa: E402

# Matches the rest of the workflow
ROUNDDP = 8

# Capacities below this are not assets, they are solver dust: a barrier solve
# leaves entries at 1e-8 GW (ten watts), and a fleet spanning 1e-8 to 1e5
# cannot be solved reliably when every one of those is a fixed bound. Applied
# PER ASSET, never per entry -- see floor_fleet.
MIN_CAPACITY_GW = 1e-4

# Dust is judged against the capacity floor and nothing else. A separate, much
# tighter threshold for negatives was wrong: it called -1e-6 GWh -- one
# thousandth of a MWh of H2 tank capacity -- a real negative and refused to
# write the file. Below the floor is dust whatever its sign; a negative BIGGER
# than the floor is a genuine problem, since these variables are declared
# positive.

# Columns every gamstool sqlitewrite variable table carries. Anything else is
# a set dimension.
VALUE_COLS = {"level", "marginal", "lower", "upper", "scale"}

# results.db table -> parameter name declared in the fix_fleet block.
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
    q = "select 1 from sqlite_master where type='table' and name=?"
    return con.execute(q, (name,)).fetchone() is not None


def fmt(v):
    # Fixed-point, because GAMS is picky about numeric literals.
    #
    # Values are written EXACTLY as solved, deliberately. Truncating them onto a
    # 1 kW grid was tried and broke eq_store_ecap_max: a fix_fleet dispatch pins
    # power and energy, Li-ion-8 ties energy to 8 x power, and rounding the
    # power down shaves eight times more off the right-hand side than off the
    # left. Any adjustment to a capacity has to respect the asset's internal
    # ratios, which is the same lesson as flooring per asset rather than per
    # entry. The floor slack that keeps a lower bound under its upper bound
    # lives in highres.gms instead, where it touches only the floors.
    return f"{v:.{ROUNDDP}f}"


def read_table(con, table):
    """Read one variable table into {dotted key: level}, or None if absent."""
    if not table_exists(con, table):
        print(f"  {table:<26} MISSING - skipped", file=sys.stderr)
        return None

    df = pd.read_sql_query(f"select * from [{table}]", con)
    dims = [c for c in df.columns if c not in VALUE_COLS]
    if not dims:
        raise ValueError(f"{table} has no set dimensions")

    df = df[dims + ["level"]].copy()
    df["level"] = df["level"].astype(float).round(ROUNDDP)


    # Absent entries default to zero in GAMS, and the fix_fleet block assigns
    # over the full domain, so a dropped zero is still fixed to zero. Dropping
    # them keeps the file to the fleet that actually exists.
    df = df[df["level"] != 0.0]

    # An all-zero table still gets a block, an empty one. Writing nothing would
    # leave the parameter declared but unassigned in highres.gms, which is the
    # same state a misspelled name produces. Emitting the empty block means a
    # missing block only ever signals a real mismatch, so the model can keep
    # GAMS's own check on rather than suppressing it.
    if df.empty:
        return {}
    keys = df[dims].astype(str).agg(".".join, axis=1)
    return dict(zip(keys, df["level"]))


def block_for(values, parname):
    """One .dd parameter block. Empty input still produces an empty block.

    An all-zero table still gets a block, an empty one. Writing nothing would
    leave the parameter declared but unassigned in highres.gms, which is the
    same state a misspelled name produces. Emitting the empty block means a
    missing block only ever signals a real mismatch, so the model can keep
    GAMS's own check on rather than suppressing it.
    """
    if not values:
        rows = np.empty((0, 2), dtype=object)
    else:
        rows = np.array([[k, fmt(v)] for k, v in sorted(values.items())], dtype=object)
    return wrapdd(rows, parname, "parameter", outfile="")


def floor_fleet(data, floor):
    """Drop assets below the floor, keeping each asset's components together.

    Storage power and energy are tied by eq_store_ecap_max -- Li-ion-8 is an
    8 hour battery, so energy <= 8 x power. Dropping one component and keeping
    the other makes that constraint infeasible, which is how a naive per-entry
    floor fails: energy fixed at 1.03e-06 against power fixed at 0.

    So the unit of decision is the asset (new or existing, zone, technology).
    If its largest component is below the floor it does not exist and every
    component goes; otherwise all of them stay, however small.
    """
    # Which parameters describe the same asset, by the key they share.
    groups = {}
    for par, values in data.items():
        for key, val in values.items():
            # storage and hydro: zone.tech, with pcap and ecap in separate
            # parameters. generation and transmission are single-component.
            vintage = "new" if "_new_" in par else "exist"
            groups.setdefault((vintage, key), []).append((par, key, val))

    # Negatives first. Barrier solutions leave levels like -3e-08 on variables
    # declared positive, and fix_fleet would pin a positive variable to a
    # negative number: infeasible, surfacing as a conflict on an unrelated
    # equation rather than as anything about dust.
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
                         "the fleet_zones set. Empty means the whole model. "
                         "Must match what ledger2dd.py used, or an expansion "
                         "starts building outside the intended scope")
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
