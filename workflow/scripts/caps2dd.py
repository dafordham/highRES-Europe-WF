"""Extract a solved fleet from a highRES results.db and write it as a .dd file.

Climate-Robust Pathways. The pathway loop carries a fleet from one step to the
next and re-dispatches it against many climate realisations, so a solved system
has to become an input. This reads the capacity variables out of a solved run
and emits the parameters that the fix_fleet block in highres.gms expects.

Reads results.db rather than results.gdx: the workflow already produces it via
`gamstool sqlitewrite`, and sqlite3 + pandas need no GAMS Python bindings.

Also carries opex*, the reference operating cost, since the min-ENS dispatch
caps varom at opex* x (1 + delta) and that reference has to come from the run
that built the fleet.

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

# Matches the rest of the workflow, and matters here: the fixed values must
# round-trip consistently or eq_new_vre_pcap_z can be left infeasible by a
# mismatch between a zonal total and the cells that must sum to it.
ROUNDDP = 8

# Columns every gamstool sqlitewrite variable table carries; anything else is
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

# opex* is varom only. With the fleet fixed, capex, FOM, VRE connection and
# transmission costs are constants, so varom is the only spend the dispatch
# controls; capping the constants too would inflate a relative delta.
OPEX_TABLES = ["costs_gen_varom", "costs_store_varom"]


def table_exists(con, name):
    q = "select 1 from sqlite_master where type='table' and name=?"
    return con.execute(q, (name,)).fetchone() is not None


def fmt(v):
    # Fixed-point rather than repr: GAMS is picky about numeric literals and
    # numpy will happily hand back 1.81e-06.
    return f"{v:.{ROUNDDP}f}"


def param_block(con, table, parname):
    """Read one variable table and return its .dd parameter block."""
    if not table_exists(con, table):
        print(f"  {table:<26} MISSING - skipped", file=sys.stderr)
        return None, 0

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
    if df.empty:
        # No block at all rather than "parameter x / /", which GAMS may reject.
        # The parameter is declared in the fix_fleet block regardless, so it
        # defaults to zero and the capacities are fixed to zero, as intended.
        print(f"  {table:<26} all zero - no block emitted")
        return None, 0

    keys = df[dims].astype(str).agg(".".join, axis=1).to_numpy()
    vals = np.array([fmt(v) for v in df["level"].to_numpy()])
    rows = np.column_stack((keys, vals))

    return wrapdd(rows, parname, "parameter", outfile=""), len(df)


def opex_star(con):
    """Reference operating cost: total varom across all zones."""
    total = 0.0
    for table in OPEX_TABLES:
        if not table_exists(con, table):
            print(f"  {table:<26} MISSING - contributes 0 to opex*", file=sys.stderr)
            continue
        val = pd.read_sql_query(f"select sum(level) as s from [{table}]", con)
        total += float(val["s"].iloc[0] or 0.0)
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="src", required=True, help="solved results.db")
    ap.add_argument("--to", dest="dst", required=True, help="output .dd file")
    args = ap.parse_args()

    src, dst = Path(args.src), Path(args.dst)
    if not src.is_file():
        sys.exit(f"no results.db at {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        print(f"reading {src}")
        blocks, counts = [], {}
        for table, parname in FLEET.items():
            block, n = param_block(con, table, parname)
            if block is not None:
                blocks.append(block)
                counts[table] = n

        star = opex_star(con)
    finally:
        con.close()

    if star <= 0.0:
        # A zero reference would make the cap opex <= 0, infeasible for any
        # fleet that must generate. Better to stop than emit a poisoned file.
        sys.exit(f"opex* came out as {star}; refusing to write {dst}")

    header = [
        "* Fleet fixed for a Climate-Robust Pathways dispatch solve.",
        f"* Generated by caps2dd.py from {src}",
        "* Values are in the model's internal units (GW-based; see MWtoGW).",
        "",
    ]

    with open(dst, "w") as f:
        f.write("\n".join(header))
        for block in blocks:
            np.savetxt(f, block, delimiter=" ", fmt="%s")
        # Scalar written literally: wrapdd's "scalar" branch emits a one-column
        # block and declares the symbol as a scalar, but fix_fleet declares
        # par_opex_star as a parameter, and $ONMULTI will not reconcile the
        # two types.
        f.write(f"parameter\npar_opex_star /\n{fmt(star)}\n/\n\n")

    print(f"\nwrote {dst}")
    for table, n in counts.items():
        print(f"  {table:<26} {n:>7} nonzero")
    print(f"  {'opex*':<26} {star:>7.4f}")


if __name__ == "__main__":
    main()
