"""Turn a capacity ledger into the .dd file that fix_fleet reads.

Climate-Robust Pathways. TEST SCAFFOLDING: in the real pipeline the fleet for
each pathway step arrives from Guillermo and Shahzad's code, with retirement
already applied. This stands in for that until it exists, so the .dd format it
writes is the part that has to last, not this script.

The ledger is cumulative vintages, not a snapshot. Each row is an addition
stamped with the horizon it was built in, so the fleet alive at horizon H is
every row with planning_horizon <= H that has not reached installed_year +
lifetime by H. The 2030 rows therefore mix legacy plant (installed 1983
onwards) with what the pathway model itself chose to build at 2030.

Everything from a ledger enters as EXISTING capacity. The par_fix_new_*
parameters are written as empty blocks: nothing has been built by this
project's loop yet.

Units: the ledger is MW and MWh, the model is GW and GWh. Divided by 1000.
Verified against the scenario data, where the pinned technologies agree to the
digit (AT HydroRes 5079.9 MW = 5.0799 GW, 3 200 000 MWh = 3200 GWh).

Usage:
    python3 ledger2dd.py --ledger capacity_ledger.csv --horizon 2030 \
        --zones AT,CZ,HU,PL --to <modelpath>/fixed_caps.dd \
        [--scenario-dir <modelpath>]
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data2dd_funcs import wrapdd  # noqa: E402

# Matches caps2dd.py and the rest of the workflow
ROUNDDP = 8

# Ledger MW/MWh -> model GW/GWh
MWtoGW = 1000.0

# The model's own sets, from BASE_gen.dd and BASE_store.dd. HydroRes sits in
# hydro_res, which is neither vre nor non_vre -- see the HydroRes note below.
NON_VRE = {
    "NaturalgasCCGTwithCCSnewOT",
    "NaturalgasOCGTnew",
    "Biomass",
    "BiomassCCS",
    "NuclearEPR",
    "SynCon",
}
VRE = {"Solar", "Windoffshore", "Windonshore", "HydroRoR", "WindoffshoreFloat"}
HYDRO_RES = {"HydroRes"}
STORAGE = {"PumpedHydro", "Li-ion-8", "H2-Tanks-OCGT", "H2-Tanks-CCGT", "EV"}
TRANS = {"HVAC_OHL", "HVDC_MarineIC"}

# Every parameter the fix_fleet block declares, in the order caps2dd.py emits
# them. All of them must appear in the file: fix_fleet assigns over the full
# domain, so a parameter left unassigned is the same state a misspelled name
# produces, and GAMS error 141 catches that at compile time.
PARAMS = [
    "par_fix_new_pcap_z",
    "par_fix_exist_pcap_z",
    "par_fix_new_vre_pcap_r",
    "par_fix_exist_vre_pcap_r",
    "par_fix_new_store_pcap_z",
    "par_fix_exist_store_pcap_z",
    "par_fix_new_store_ecap_z",
    "par_fix_exist_store_ecap_z",
    "par_fix_new_hydro_ecap_z",
    "par_fix_exist_hydro_ecap_z",
    "par_fix_new_trans_pcap",
    "par_fix_exist_trans_pcap",
]


def fmt(v):
    # Fixed-point, because GAMS is picky about numeric literals
    return f"{v:.{ROUNDDP}f}"


def alive(df, horizon):
    """Rows that exist at this horizon: built by then, not yet retired."""
    built = df.planning_horizon <= horizon
    retired = df.installed_year + df.lifetime <= horizon
    return df[built & ~retired].copy()


def check_technologies(df):
    """Refuse unknown technology names rather than dropping them silently."""
    known = NON_VRE | VRE | HYDRO_RES | STORAGE | TRANS
    unknown = sorted(set(df.Technology) - known)
    if unknown:
        sys.exit(
            "ledger names technologies the model does not have: "
            + ", ".join(unknown)
            + "\nAdd them to the model's sets or map them, do not ignore them."
        )


def block(keys_values, parname):
    """One .dd parameter block. Empty input still produces an empty block."""
    if not keys_values:
        rows = np.empty((0, 2), dtype=object)
    else:
        rows = np.array([[k, fmt(v)] for k, v in sorted(keys_values.items())], dtype=object)
    return wrapdd(rows, parname, "parameter", outfile=""), len(keys_values)


def collect(df, zones):
    """Ledger rows -> {parameter: {dotted key: GW or GWh}}."""
    out = {p: {} for p in PARAMS}

    for row in df.itertuples():
        tech, zone, region = row.Technology, row.zone, row.region
        value = row.capacity_mw / MWtoGW
        is_power = row.capacity_type == "pcap"

        if tech in TRANS:
            if not isinstance(region, str) or not region:
                sys.exit(f"transmission row without a destination zone: {row}")
            # The model carries both directions of every link, with the same
            # value -- see par_fix_exist_trans_pcap in a caps2dd .dd. Writing
            # one direction only would leave the reverse at zero and the link
            # half-fixed.
            for a, b in ((zone, region), (region, zone)):
                key = f"{a}.{b}.{tech}"
                out["par_fix_exist_trans_pcap"][key] = (
                    out["par_fix_exist_trans_pcap"].get(key, 0.0) + value
                )
            continue

        if tech in VRE:
            if not is_power:
                sys.exit(f"VRE technology {tech} with capacity_type ecap: not supported")
            # At `region` spatial resolution the model's grid-cell index r is
            # the zone label itself (keys look like AT.Solar.AT), so NUTS2 rows
            # sum into the zone. At finer resolution this is where the real
            # cell mapping would go, and the external code has to supply it.
            key = f"{zone}.{tech}.{zone}"
            out["par_fix_exist_vre_pcap_r"][key] = (
                out["par_fix_exist_vre_pcap_r"].get(key, 0.0) + value
            )
            continue

        if tech in STORAGE:
            par = (
                "par_fix_exist_store_pcap_z" if is_power else "par_fix_exist_store_ecap_z"
            )
            key = f"{zone}.{tech}"
            out[par][key] = out[par].get(key, 0.0) + value
            continue

        if tech in HYDRO_RES:
            if is_power:
                # HydroRes is in the hydro_res set, and the fix_fleet block
                # assigns var_exist_pcap_z.FX only over non_vre, so a HydroRes
                # entry here is loaded and never used. It is written anyway, to
                # match what caps2dd.py produces from a solved run, and the
                # scenario's own FX pin on gen_lim_pcap_z / gen_exist_pcap_z is
                # what actually binds. --scenario-dir checks the two agree.
                key = f"{zone}.{tech}"
                out["par_fix_exist_pcap_z"][key] = (
                    out["par_fix_exist_pcap_z"].get(key, 0.0) + value
                )
            else:
                key = f"{zone}.{tech}"
                out["par_fix_exist_hydro_ecap_z"][key] = (
                    out["par_fix_exist_hydro_ecap_z"].get(key, 0.0) + value
                )
            continue

        # Remaining case: non-VRE generation, zonal
        if not is_power:
            sys.exit(f"non-VRE technology {tech} with capacity_type ecap: not supported")
        key = f"{zone}.{tech}"
        out["par_fix_exist_pcap_z"][key] = out["par_fix_exist_pcap_z"].get(key, 0.0) + value

    return out


def parse_dd_param(path, param):
    """Read one 'param / key value ... /' block from a .dd file."""
    rows = {}
    inside = False
    for line in Path(path).read_text().splitlines():
        s = line.strip()
        if s == f"{param} /":
            inside = True
            continue
        if inside:
            if s == "/":
                break
            if not s or s.startswith("*"):
                continue
            key, val = s.rsplit(maxsplit=1)
            rows[key] = float(val)
    return rows


def carry_out_of_scope_pins(data, scenario_dir, zones):
    """Satisfy the scenario's capacity pins in zones the test does not cover.

    The fix_fleet block assigns over the FULL domain, so every zone absent from
    the ledger is fixed to zero. That contradicts the scenario, which pins the
    TOTAL of some technologies in all 28 zones -- HydroRes energy on
    gen_lim_ecap_z, PumpedHydro power and energy on store_lim_*. tot = exist +
    new, so zero against a nonzero pin is infeasible, and the model is proven
    infeasible in presolve with no indication of which bound did it.

    HydroRes POWER needs nothing: fix_fleet assigns var_exist_pcap_z only over
    non_vre, and HydroRes is in hydro_res, so its scenario pin is never
    overridden. VRE and non-VRE carry UP bounds rather than pins, and zero
    respects an upper bound.

    Out-of-scope zones therefore keep their pinned hydro and pumped hydro. With
    their demand zeroed and no transmission to them, they are inert.
    """
    carried = {
        ("BASE_gen.dd", "gen_lim_ecap_z"): "par_fix_exist_hydro_ecap_z",
        ("BASE_store.dd", "store_lim_pcap_z"): "par_fix_exist_store_pcap_z",
        ("BASE_store.dd", "store_lim_ecap_z"): "par_fix_exist_store_ecap_z",
    }
    n = 0
    for (fname, param), our_param in carried.items():
        path = Path(scenario_dir) / fname
        if not path.is_file():
            sys.exit(f"--scenario-dir given but {fname} not found in it")
        for skey, sval in parse_dd_param(path, param).items():
            parts = skey.split(".")
            if len(parts) != 3:
                continue
            zone, tech, bound = parts
            if bound != "FX" or zone in zones or sval == 0.0:
                continue
            data[our_param][f"{zone}.{tech}"] = sval
            n += 1
    return n


def cross_check(data, scenario_dir, zones):
    """Check the ledger against capacities the scenario pins or caps.

    Only the TOTAL bounds matter here. The scenario's gen_exist_* / store_exist_*
    entries describe its own 2010-era fleet and are overridden by the fix_fleet
    assignments, so they are expected to differ from a 2030 ledger. The
    *_lim_* bounds are different: var_tot_pcap_z is pinned or capped from them,
    and tot = exist + new, so a ledger that contradicts an FX pin or exceeds an
    UP cap makes the model infeasible rather than slightly wrong.

    The ledger wins where it disagrees with an FX pin -- the fix_fleet
    assignments come after the scenario's in highres.gms -- but silently
    winning is how a contradiction becomes a result nobody questions.
    """
    # Ledger totals per zone.tech, pulling VRE back from its cell-level keys.
    totals = {}
    for par in ("par_fix_exist_pcap_z", "par_fix_exist_store_pcap_z"):
        for key, val in data[par].items():
            totals[key] = totals.get(key, 0.0) + val
    for key, val in data["par_fix_exist_vre_pcap_r"].items():
        zone, tech, _r = key.split(".")
        k = f"{zone}.{tech}"
        totals[k] = totals.get(k, 0.0) + val

    ecap_totals = {}
    for par in ("par_fix_exist_hydro_ecap_z", "par_fix_exist_store_ecap_z"):
        for key, val in data[par].items():
            ecap_totals[key] = ecap_totals.get(key, 0.0) + val

    checks = [
        ("BASE_gen.dd", "gen_lim_pcap_z", totals),
        ("BASE_gen.dd", "gen_lim_ecap_z", ecap_totals),
        ("BASE_store.dd", "store_lim_pcap_z", totals),
        ("BASE_store.dd", "store_lim_ecap_z", ecap_totals),
    ]

    pinned_differs, over_cap, checked = [], [], 0
    for fname, param, ours in checks:
        path = Path(scenario_dir) / fname
        if not path.is_file():
            print(f"  {fname:<16} not found, skipped", file=sys.stderr)
            continue
        for skey, sval in parse_dd_param(path, param).items():
            parts = skey.split(".")
            if len(parts) != 3 or parts[0] not in zones:
                continue
            zone, tech, bound = parts
            mine = ours.get(f"{zone}.{tech}", 0.0)
            if bound == "FX":
                checked += 1
                if abs(mine - sval) > 1e-6:
                    pinned_differs.append((param, zone, tech, sval, mine))
            elif bound == "UP" and mine > sval + 1e-6:
                over_cap.append((param, zone, tech, sval, mine))

    print(f"  {checked} pinned (FX) capacities compared")
    for param, zone, tech, sval, mine in pinned_differs:
        print(f"  DISAGREES  {param}: {zone}.{tech} "
              f"scenario {sval:.6f}  ledger {mine:.6f}")
    for param, zone, tech, sval, mine in over_cap:
        print(f"  OVER CAP   {param}: {zone}.{tech} "
              f"ledger {mine:.6f} exceeds UP {sval:.6f}  -- INFEASIBLE")
    if not pinned_differs and not over_cap:
        print("  ledger agrees with every pin and respects every cap")
    elif pinned_differs and not over_cap:
        print(f"  {len(pinned_differs)} pin disagreement(s). The ledger wins; "
              "check this is intended.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", required=True, help="capacity_ledger.csv")
    ap.add_argument("--horizon", required=True, type=int, help="e.g. 2030")
    ap.add_argument("--zones", required=True, help="comma separated, e.g. AT,CZ,HU,PL")
    ap.add_argument("--to", dest="dst", required=True, help="output .dd file")
    ap.add_argument(
        "--scenario-dir",
        help="model directory holding BASE_gen.dd and BASE_store.dd, to "
        "cross-check capacities the scenario pins",
    )
    args = ap.parse_args()

    src, dst = Path(args.ledger), Path(args.dst)
    if not src.is_file():
        sys.exit(f"no ledger at {src}")
    zones = [z.strip() for z in args.zones.split(",") if z.strip()]

    df = pd.read_csv(src)
    check_technologies(df)

    kept = alive(df, args.horizon)
    kept = kept[kept.zone.isin(zones)]
    if kept.empty:
        sys.exit(f"nothing alive at {args.horizon} in zones {zones}")

    dropped_zones = sorted(set(df.zone) - set(zones))
    retired = len(alive(df, args.horizon)) - len(kept)

    data = collect(kept, zones)

    carried = 0
    if args.scenario_dir:
        carried = carry_out_of_scope_pins(data, args.scenario_dir, set(zones))

    print(f"reading {src}")
    print(f"  horizon {args.horizon}, zones {','.join(zones)}")
    print(f"  {len(kept)} rows alive and in scope")
    if dropped_zones:
        print(f"  zones not in scope, left at zero: {','.join(dropped_zones)}")
    if retired:
        print(f"  {retired} rows alive but outside the zone scope")
    if carried:
        print(f"  {carried} scenario pins carried for out-of-scope zones")
    elif not args.scenario_dir:
        print("  WARNING: no --scenario-dir, so capacities the scenario pins in")
        print("  zones outside the ledger are fixed to zero. That contradicts the")
        print("  pins and the model will be proven infeasible in presolve.")

    blocks, counts = [], {}
    for parname in PARAMS:
        blk, n = block(data[parname], parname)
        blocks.append(blk)
        counts[parname] = n

    header = [
        "* Fleet for a Climate-Robust Pathways step, from a capacity ledger.",
        f"* Generated by ledger2dd.py from {src}, horizon {args.horizon},",
        f"* zones {','.join(zones)}.",
        "* Ledger MW/MWh converted to the model's GW/GWh. All capacity enters",
        "* as existing; par_fix_new_* are empty because this project's loop has",
        "* not built anything yet.",
        "",
    ]

    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w") as f:
        f.write("\n".join(header))
        # The zones this fleet speaks for. highres.gms uses it to stop an
        # expansion building outside them -- without it the optimiser puts
        # generation in a zero-demand neighbour and imports, which is cheaper
        # and quietly ends the island.
        f.write("set\nfleet_zones /\n" + "\n".join(sorted(zones)) + "\n/\n\n")
        for blk in blocks:
            np.savetxt(f, blk, delimiter=" ", fmt="%s")

    print(f"\nwrote {dst}")
    for parname, n in counts.items():
        mark = "" if n else "   (empty block)"
        print(f"  {parname:<28} {n:>5} entries{mark}")

    if args.scenario_dir:
        print("\ncross-checking against the scenario's pinned capacities")
        cross_check(data, args.scenario_dir, set(zones))


if __name__ == "__main__":
    main()
