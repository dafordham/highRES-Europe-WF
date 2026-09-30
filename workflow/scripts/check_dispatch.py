"""Compare a fixed-fleet dispatch solve against the t0 run that built the fleet.

Climate-Robust Pathways verification. Two things this answers:

Test A -- the dispatch is run against the same weather year the fleet was built
on, under a cap of opex* x (1 + delta). The t0 dispatch is feasible by
construction: it served all load at exactly opex*. So the answer is known in
advance and the solve must return ENS = 0 with the cap slack. Anything else
means the fleet is being fixed incompletely -- most likely hydro, or VRE cells
dropped from the .dd. It is the only point in this project with a ground truth,
so it is worth spending a solve on before trusting any later ENS.

It also prints the capex:varom ratio from t0, which settles empirically whether
a relative delta on *total* cost would ever have bound, or whether it would
have exceeded varom entirely and silently removed all cost discipline.

Usage:
    python3 check_dispatch.py --t0 <t0 results.db> [--dispatch <results.db>]
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

# With the fleet fixed these are constants; only varom moves.
FIXED_COST = [
    "costs_gen_capex",
    "costs_gen_fom",
    "costs_gen_vreconnection",
    "costs_store_capex",
    "costs_store_fom",
    "costs_trans_capex",
    "costs_trans_fom",
]
VAROM_COST = ["costs_gen_varom", "costs_store_varom"]

# Solver tolerance. barepcomp is 1E-7 in the local config; ENS is summed over
# zones and hours, so allow a little more slack than that before calling a
# value nonzero.
TOL = 1e-4


def connect(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def has(con, table):
    q = "select 1 from sqlite_master where type='table' and name=?"
    return con.execute(q, (table,)).fetchone() is not None


def total(con, tables, col="level"):
    """Sum a column across one or more tables, skipping absent ones."""
    out = 0.0
    for t in tables:
        if not has(con, t):
            continue
        df = pd.read_sql_query(f"select sum([{col}]) as s from [{t}]", con)
        out += float(df["s"].iloc[0] or 0.0)
    return out


def scalar(con, name, col="level"):
    """Read a 0-dimensional symbol.

    gamstool sqlitewrite does not give 0-dim symbols a table of their own --
    they are rows in scalars (parameters), scalarvariables and
    scalarequations. Only symbols with at least one set dimension become
    tables, which is why o_ens_z exists but o_ens_total does not.
    """
    for table in ("scalars", "scalarvariables", "scalarequations"):
        if not has(con, table):
            continue
        df = pd.read_sql_query(
            f"select * from [{table}] where name = ?", con, params=(name,)
        )
        if df.empty:
            continue
        for c in (col, "value", "level"):
            if c in df.columns:
                return float(df[c].iloc[0])
    return None


def report_t0(con):
    fixed = total(con, FIXED_COST)
    varom = total(con, VAROM_COST)

    print("t0 run")
    print(f"  varom (opex*)            {varom:>16.4f}")
    print(f"  capex + FOM + other      {fixed:>16.4f}")
    if varom > 0:
        ratio = fixed / varom
        print(f"  ratio fixed:varom        {ratio:>16.2f}")
        print()
        print("  A relative delta on TOTAL cost would give slack")
        print(f"  delta x (fixed+varom) = delta x {(fixed+varom)/varom:.2f} x varom,")
        print(f"  i.e. a delta of 0.05 would allow varom to rise by "
              f"{0.05*(fixed+varom)/varom*100:.1f}%.")
        if 0.05 * (fixed + varom) / varom >= 1.0:
            print("  >> That exceeds varom entirely: on total cost the cap")
            print("     would never bind. Capping varom only is doing real work.")
    return varom


def report_dispatch(con, varom_t0):
    print("\ndispatch run")

    ens = scalar(con, "o_ens_total")
    share = scalar(con, "o_ens_share")
    varom = total(con, VAROM_COST)

    if ens is None:
        print("  o_ens_total absent -- was ens_obj actually ON?")
    else:
        verdict = "PASS" if abs(ens) <= TOL else "FAIL"
        print(f"  ens_total                {ens:>16.6f}   [{verdict}] expected 0")
        if share is not None:
            print(f"  ens_share                {share:>16.8f}")

    cap_dual = scalar(con, "eq_opex_cap", col="marginal")
    if cap_dual is None:
        print("  eq_opex_cap absent")
    else:
        verdict = "PASS" if abs(cap_dual) <= TOL else "note"
        print(f"  eq_opex_cap.M            {cap_dual:>16.6f}   [{verdict}] expected 0 (slack)")
        if abs(cap_dual) > TOL:
            print(f"     cap is binding; 1/dual = {1.0/cap_dual:,.2f} per unit energy")

    co2_dual = total(con, ["eq_co2_target"], col="marginal")
    print(f"  eq_co2_target.M          {co2_dual:>16.6f}")
    if abs(co2_dual) > TOL:
        print("     carbon cap is binding -- some ENS may be carbon-driven")
        print("     rather than adequacy-driven. The intensity allowance is")
        print("     built from demand, not served demand, so shedding relieves it.")

    print(f"\n  varom dispatch           {varom:>16.4f}")
    print(f"  varom t0                 {varom_t0:>16.4f}")
    diff = varom - varom_t0
    rel = diff / varom_t0 if varom_t0 else float("nan")
    print(f"  difference               {diff:>16.4f}  ({rel:+.4%})")

    # varom sitting at the cap looks alarming and usually is not. Once ENS
    # hits zero the objective is indifferent to cost, so every point under the
    # cap is equally optimal and the simplex stops wherever it stops. The dual
    # is what says whether the cap actually constrained anything.
    if ens is not None and abs(ens) <= TOL and cap_dual is not None:
        if abs(cap_dual) <= TOL:
            print("\n  varom near the cap with a zero dual is LP degeneracy,")
            print("  not a binding budget: with ENS at zero the objective no")
            print("  longer cares what the dispatch costs.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--t0", required=True, help="results.db of the expansion")
    ap.add_argument("--dispatch", help="results.db of the fixed-fleet dispatch")
    args = ap.parse_args()

    t0 = Path(args.t0)
    if not t0.is_file():
        sys.exit(f"no results.db at {t0}")

    con = connect(t0)
    try:
        varom_t0 = report_t0(con)
    finally:
        con.close()

    if not args.dispatch:
        print("\n(no --dispatch given; t0 figures only)")
        return

    dsp = Path(args.dispatch)
    if not dsp.is_file():
        sys.exit(f"no results.db at {dsp}")

    con = connect(dsp)
    try:
        report_dispatch(con, varom_t0)
    finally:
        con.close()

    print("\nAlso check highres.lst for the resolved fix_fleet / ens_obj values")
    print("and for model status (optimal).")


if __name__ == "__main__":
    main()
