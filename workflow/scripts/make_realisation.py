"""Build one climate realisation by rescaling a solved model directory's inputs.

Climate-Robust Pathways. TEST SCAFFOLDING: C2E is not available and only
weather year 2010 exists locally and on fox, so realisations are mocked by
scaling that year. Uniform scaling stresses energy and peak together and
uniformly across zones, so a realisation says NOTHING about adequacy -- it
exercises the loop and nothing more.

A realisation is a set of factors. Two ways to use them:

  * One factor on all capacity factors, pulling against demand, which orders
    mild to harsh cleanly:

        mild    cf x 1.05   demand x 0.97
        harsh   cf x 0.90   demand x 1.08

    Those realisations are NESTED: the harsh one has less of every resource and
    more load at every hour, so a fleet adequate for it is automatically
    adequate for the others. The pathway loop can then never need a second
    expansion.

  * PER TECHNOLOGY factors, which break that nesting:

        --cf-tech Windonshore=0.60,Solar=1.20     a becalmed, sunny year
        --cf-tech Windonshore=1.20,Solar=0.40     a dark, windy year

    Neither dominates the other, so a fleet built for one can fail the other and
    the loop has to iterate. This is the only way to exercise the floors, and it
    is also closer to what real realisations look like: weather patterns differ
    in shape, not in intensity.

What gets scaled:

  * demand      BASE_demand_<year>.dd, in scope only; out-of-scope zones go to
                zero, which is what makes the test an island
  * VRE cf      vre_<year>_.parquet -> csv -> gdx, clipped at 1.0 because a
                capacity factor above nameplate is unphysical and the source
                data already reaches exactly 1.0
  * hydro inflow  hydro_res_inflow_<year>.gdx, scaled by the HydroRes factor
                if --cf-tech gives one, otherwise by --cf, so that a dry year is
                dry in every input at once

Rebuilds the gdx files with the same csv2gdx and gdxcopy calls as rules
build_vre_gdx / compress_vre_gdx / build_hydrores_inflow, rather than
reimplementing the conversion.

Usage:
    python3 make_realisation.py --from <model dir> --to <dir> \
        --cf 0.90 --demand 1.08 --zones AT,CZ,HU,PL [--year 2010]
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

GAMS_DEFAULT = "/Library/Frameworks/GAMS.framework/Versions/53/Resources"

# Outputs and logs of whatever solve last ran in the source directory. Copying
# them would leave a realisation holding another run's results, and results.gdx
# is protected() so it cannot even be overwritten in place.
SKIP = (
    "results.gdx",
    "results.db",
    "results.db.compressed",
    "results.parquet",
    "highres.lst",
    "highres.log",
    "results",
    "test_log",
)


def copy_inputs(src, dst):
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for item in sorted(src.iterdir()):
        if item.name in SKIP or item.name.endswith((".log", ".bak")):
            continue
        if item.is_dir():
            continue
        shutil.copy2(item, dst / item.name)
        n += 1
    return n


def scale_demand(path, factor, zones, per_zone=None):
    """Scale demand in scope, zero everything else.

    The file is 'ZONE.hour value' text. Out-of-scope zones are written as zero
    rather than dropped: the model's demand parameter is read over the full
    domain and an absent entry would default to zero anyway, but keeping the
    rows makes the file diffable against the original.

    per_zone overrides the factor for named zones. Stressing one zone at a time
    is the only way to make realisations that firm capacity cannot flatten: a
    gas plant in Poland does not help Austria beyond AT's interconnection, so a
    fleet built for a Polish stress year can still fail an Austrian one. Varying
    wind against sun does not achieve that -- the expansion simply buys firm
    capacity, which is weather-independent, and then every realisation clears.
    """
    per_zone = per_zone or {}
    out, kept, zeroed = [], 0.0, 0.0
    for line in path.read_text().splitlines():
        s = line.strip()
        if "." not in s or " " not in s or s.startswith(("parameter", "*", "/")):
            out.append(line)
            continue
        key, val = s.rsplit(maxsplit=1)
        try:
            v = float(val)
        except ValueError:
            out.append(line)
            continue
        zone = key.split(".")[0]
        if zone in zones:
            v *= per_zone.get(zone, factor)
            kept += v
        else:
            v = 0.0
            zeroed += float(val)
        out.append(f"{key} {v:.8f}")
    path.write_text("\n".join(out) + "\n")
    return kept, zeroed


def scale_vre(dst, year, cf, per_tech, gams):
    """Scale capacity factors per technology, clip at 1.0, rebuild the gdx."""
    pq = dst / f"vre_{year}_.parquet"
    if not pq.is_file():
        sys.exit(f"no {pq.name} in the source directory: cannot rescale VRE")
    df = pd.read_parquet(pq)
    col = df.columns[0]
    before = float(df[col].max())

    techs = df.index.get_level_values("technology")
    unknown = set(per_tech) - set(techs.unique())
    if unknown:
        sys.exit(f"--cf-tech names technologies not in the VRE file: "
                 f"{sorted(unknown)}. It has {sorted(set(techs.unique()))}")

    factors = pd.Series(cf, index=df.index, dtype=float)
    for tech, f in per_tech.items():
        factors[techs == tech] = f
    df[col] = (df[col] * factors).clip(upper=1.0)
    clipped = int(((df[col] >= 1.0) & (factors > 1.0)).sum())
    df.to_parquet(pq, compression="zstd")

    csv = dst / f"vre_{year}_tmp.csv"
    df.to_csv(csv)
    gdx = dst / f"vre_{year}_.gdx"
    run(
        [f"{gams}/csv2gdx", str(csv), f"output={gdx}", "ID=vre_gen",
         "Index=(1,2,3)", "Value=(4)", "UseHeader=True", "StoreZero=True"],
        "csv2gdx (vre)",
    )
    run([f"{gams}/gdxcopy", "-V7C", "-Replace", str(gdx)], "gdxcopy (vre)")
    csv.unlink()
    return before, float(df[col].max()), clipped


def scale_hydro_inflow(dst, year, cf, gams):
    """Scale reservoir inflow with the capacity factors, rebuild the gdx."""
    gdx = dst / f"hydro_res_inflow_{year}.gdx"
    if not gdx.is_file():
        print(f"  no {gdx.name}, hydro inflow left alone")
        return None
    csv = dst / f"hydro_inflow_tmp.csv"
    with open(csv, "w") as f:
        run([f"{gams}/gdxdump", str(gdx), "symb=hydro_inflow", "format=csv"],
            "gdxdump (hydro)", stdout=f)
    df = pd.read_csv(csv)
    vcol = df.columns[-1]
    total_before = float(df[vcol].sum())
    df[vcol] = df[vcol] * cf
    df.to_csv(csv, index=False)
    run(
        [f"{gams}/csv2gdx", str(csv), f"output={gdx}", "ID=hydro_inflow",
         "Index=(1,2,3)", "Value=(4)", "UseHeader=True", "StoreZero=True"],
        "csv2gdx (hydro)",
    )
    csv.unlink()
    return total_before, total_before * cf


def run(cmd, what, stdout=None):
    r = subprocess.run(cmd, stdout=stdout or subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        sys.exit(f"{what} failed ({r.returncode}):\n{r.stderr.decode()[-2000:]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="src", required=True, help="built model directory")
    ap.add_argument("--to", dest="dst", required=True, help="directory to create")
    ap.add_argument("--cf", type=float, required=True,
                    help="capacity factor scale for technologies not named in "
                         "--cf-tech")
    ap.add_argument("--cf-tech", default="",
                    help="per technology overrides, e.g. "
                         "Windonshore=0.60,Solar=1.20. Breaks the nesting that "
                         "a single factor creates")
    ap.add_argument("--demand", type=float, required=True,
                    help="demand scale for zones not named in --demand-zone")
    ap.add_argument("--demand-zone", default="",
                    help="per zone overrides, e.g. PL=1.40,AT=1.00. Stressing "
                         "one zone at a time is what firm capacity cannot "
                         "flatten")
    ap.add_argument("--zones", required=True, help="in scope, comma separated")
    ap.add_argument("--year", default="2010")
    ap.add_argument("--gams", default=GAMS_DEFAULT, help="GAMS Resources directory")
    args = ap.parse_args()

    src, dst = Path(args.src), Path(args.dst)
    if not src.is_dir():
        sys.exit(f"no model directory at {src}")
    zones = {z.strip() for z in args.zones.split(",") if z.strip()}

    print(f"realisation: cf x {args.cf}, demand x {args.demand}"
          + (f", overrides: {args.cf_tech}" if args.cf_tech else ""))
    n = copy_inputs(src, dst)
    print(f"  copied {n} input files to {dst}")

    per_zone = {}
    for item in (x for x in args.demand_zone.split(",") if x.strip()):
        z, _, val = item.partition("=")
        per_zone[z.strip()] = float(val)

    kept, zeroed = scale_demand(dst / f"BASE_demand_{args.year}.dd", args.demand,
                                zones, per_zone)
    print(f"  demand in scope  {kept:>14,.0f}  (x {args.demand})")
    for z, f in sorted(per_zone.items()):
        print(f"    {z:<20} x {f}")
    print(f"  demand zeroed    {zeroed:>14,.0f}  ({len(zones)} zones kept)")

    per_tech = {}
    for item in (x for x in args.cf_tech.split(",") if x.strip()):
        tech, _, val = item.partition("=")
        per_tech[tech.strip()] = float(val)

    before, after, clipped = scale_vre(dst, args.year, args.cf, per_tech, args.gams)
    print(f"  cf max {before:.4f} -> {after:.4f}" + (f", {clipped} cells clipped at 1.0" if clipped else ""))
    for tech, f in sorted(per_tech.items()):
        print(f"    {tech:<20} x {f}")

    hy = scale_hydro_inflow(dst, args.year, per_tech.get("HydroRes", args.cf), args.gams)
    if hy:
        print(f"  hydro inflow {hy[0]:,.0f} -> {hy[1]:,.0f}")

    print("\nThis is a scaled 2010, not a climate realisation. Nothing about")
    print("adequacy can be read off it; it exists to exercise the loop.")


if __name__ == "__main__":
    main()
