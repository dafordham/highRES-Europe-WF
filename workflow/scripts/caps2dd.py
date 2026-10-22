"""reads a solved fleet out of results.db and writes it as a .dd

the loop uses it to turn a solve into the fleet for the next solve: the
starting fleet (fleet_from_inputs), and the fleet after each expansion.
the .dd has the par_fix_* parameters that fix_fleet and fleet_floor read in
highres.gms, plus the fleet_zones set.

Usage:
    python3 caps2dd.py --from <results.db> --to <fixed_caps.dd> [--zones AT,CZ]
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

# [cl] anything under 0.1 MW is solver dust, not a real asset. the barrier
# leaves values like 1e-8 GW, and fixing a fleet from 1e-8 to 1e5 GW makes the
# solve unstable. same limit for negatives: under it they're dust, over it
# something is wrong
FLOOR_GW = 1e-4

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


def read_table(con, table):
    """[cl] one variable table as {"AT.Solar": level, ...}, None if the table is missing"""
    if not con.execute("select 1 from sqlite_master where name=?", (table,)).fetchone():
        return None
    df = pd.read_sql_query(f"select * from [{table}]", con)
    # [cl] a variable that is zero everywhere comes out with only the set columns
    if "level" not in df.columns:
        return {}
    # [cl] drop zeros, GAMS treats missing entries as zero anyway and fix_fleet
    # covers everything, so they still get fixed to zero
    df["level"] = df["level"].astype(float).round(8)
    df = df[df["level"] != 0.0]
    if df.empty:
        return {}
    sets = [c for c in df.columns if c not in ("level", "marginal", "lower", "upper", "scale")]
    keys = df[sets].astype(str).agg(".".join, axis=1)
    return dict(zip(keys, df["level"]))


def remove_dust(data):
    """[cl] drop assets under the floor and clamp small negatives to zero

    works per asset (new or existing, zone, tech), not per entry. storage power
    and energy are linked (Li-ion-8: energy <= 8 x power), so dropping one and
    keeping the other is infeasible. if an asset's biggest part is under the
    floor all of it goes, otherwise all of it stays
    """
    # [cl] the barrier leaves things like -3e-08 on positive variables, fine,
    # but a big negative means the solve went wrong
    for par, values in data.items():
        for key, v in values.items():
            if v <= -FLOOR_GW:
                sys.exit(f"{par} {key} = {v}: too negative to be dust, check the solve")

    # [cl] biggest part of each asset. storage and hydro have power and energy
    # in separate parameters but the same key (zone.tech)
    biggest = {}
    for par, values in data.items():
        vintage = "new" if "_new_" in par else "exist"
        for key, v in values.items():
            biggest[vintage, key] = max(biggest.get((vintage, key), 0.0), abs(v))

    for par, values in data.items():
        vintage = "new" if "_new_" in par else "exist"
        for key in list(values):
            if biggest[vintage, key] < FLOOR_GW:
                del values[key]
            elif values[key] < 0:
                values[key] = 0.0


def main():
    # Parses the command line
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="src", required=True, help="solved results.db")
    ap.add_argument("--to", dest="dst", required=True, help="output .dd file")
    ap.add_argument("--zones", default="",
                    help="comma separated zones this fleet covers, written as "
                         "the fleet_zones set. Empty means the whole model")
    args = ap.parse_args()
    if not Path(args.src).is_file():
        sys.exit(f"no results.db at {args.src}")

    # Connect to the sqlite database in read-only mode and extract the parameter blocks.
    con = sqlite3.connect(f"file:{args.src}?mode=ro", uri=True)
    data = {par: read_table(con, table) for table, par in FLEET.items()}
    con.close()
    data = {par: values for par, values in data.items() if values is not None}

    remove_dust(data)

    # [cl] header, the fleet_zones set, then one block per parameter. an empty
    # table still gets an empty block: with none, GAMS would see the parameter
    # declared but never assigned, same as a typo in its name.
    # values are written as solved. rounding them to 1 kW was tried and broke
    # Li-ion-8 (energy = 8 x power, so rounding power down cuts the energy limit
    # 8x more). floor_tol in highres.gms handles the floors instead
    zones = sorted(z.strip() for z in args.zones.split(",") if z.strip())
    with open(args.dst, "w") as f:
        f.write("* Fleet fixed for a Climate-Robust Pathways dispatch solve.\n"
                f"* Generated by caps2dd.py from {args.src}\n"
                "* Values are in the model's internal units, GW-based. See MWtoGW.\n")
        f.write("set\nfleet_zones /\n" + "\n".join(zones) + "\n/\n\n")
        for par, values in data.items():
            f.write(f"parameter\n{par} /\n")
            f.writelines(f"{key} {v:.8f}\n" for key, v in sorted(values.items()))
            f.write("/\n\n")


if __name__ == "__main__":
    main()
