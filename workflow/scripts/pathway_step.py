"""Run one pathway step: rank realisations by ENS, expand on the worst, repeat.

Climate-Robust Pathways. This is the loop itself, and it lives outside
Snakemake deliberately: the number of iterations depends on the results, which
no DAG can express.

One step, given a fleet and a set of realisations:

  1. Rank. Dispatch the fleet against every realisation (fix_fleet, ens_obj=ON,
     minimise ENS). Realisations are independent, so these run in parallel.
  2. If every ENS share is within tolerance, the step is done and nothing is
     built.
  3. Otherwise expand on the worst realisation: least-cost capacity expansion
     with shedding switched off, existing capacity fixed and new capacity
     floored at whatever the previous expansion in this step built
     (fleet_floor).
  4. Re-rank against the enlarged fleet and repeat from 2.

Termination rests on monotonicity: every expansion only adds capacity, and
added capacity can only weakly reduce ENS for every realisation, so no
iteration undoes an earlier one. The price is overbuild. If a realisation's
share rises after an expansion, something is wrong with the floors or the
carried fleet, and the loop stops and says so rather than continuing.

Nothing is ranked off a non-optimal solve. GAMS exits 0 on an infeasible model
and still writes the result parameters, where an infeasible fixed-fleet
dispatch reports ENS = 0 -- indistinguishable from a realisation that clears
perfectly unless the status is checked.

Usage:
    python3 pathway_step.py \
        --ledger ../../capacity_ledger.csv --horizon 2030 --zones AT,CZ,HU,PL \
        --model-dir <a built model directory> --work <output root> \
        [--tolerance 0.05] [--max-iter 5] [--jobs 3] \
        [--realisation harsh:0.90:1.08 ...]
"""

import argparse
import csv
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAMS_DEFAULT = "/Library/Frameworks/GAMS.framework/Versions/53/Resources"

# Default realisations: SHAPES, not intensities. One factor on all capacity
# factors makes the realisations nested -- the harsh one has less of every
# resource and more load at every hour, so a fleet adequate for it is adequate
# for the rest and the loop can never need a second expansion. Per technology
# factors break that: a becalmed sunny year against a dark windy one, neither
# dominating the other.
#
# Spec is name:cf:demand[:Tech=f;Tech=f], the fourth field optional.
DEFAULT_REALISATIONS = [
    "median:1.00:1.00",
    "becalmed:1.00:1.05:Windonshore=0.60;Windoffshore=0.60;WindoffshoreFloat=0.60;Solar=1.20",
    "dark:1.00:1.05:Windonshore=1.20;Windoffshore=1.20;WindoffshoreFloat=1.20;Solar=0.40",
]

# modelstat values a result may be read from. 1 optimal, 2 locally optimal,
# 8 integer solution (unit commitment). Everything else -- 4 and 5 infeasible,
# 7 intermediate non-optimal, 19 infeasible no solution -- is refused.
OK_MODELSTAT = {1, 2, 8}

# Files that belong to whatever solve last ran in a directory.
RESULT_FILES = ("results.gdx", "results.db", "results.db.compressed",
                "highres.lst", "highres.log", "solve.log")

SOLVE_ARGS = [
    "--psys_scen=BASE", "--co2_target_type=intensity", "--co2_target_extent=all",
    "--outname=results", "--store_initial_level=0.5", "--store_final_level=0.5",
    "--hydro_res_initial_fill=0.8", "--hydro_res_min=0.5", "--pgen=20.0",
    "--emis_price=0", "--trans_inv=TYNDP", "--trans_cap_lim=20", "--UC=OFF",
    "--f_res=OFF", "--store_uc=OFF", "--EV=OFF", "--EV_flex=0", "--V2G=OFF",
    "--EV_pcap=0.011", "--EV_soc_min=0.2", "--EV_soc_max=0.8",
]


def script(name, *args):
    """Run one of the sibling scripts and fail loudly."""
    cmd = [sys.executable, str(HERE / name), *map(str, args)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"{name} failed:\n{r.stdout.decode()}\n{r.stderr.decode()}")
    return r.stdout.decode()


def solve(model_dir, code, gams, year, extra, log_name="solve.log"):
    """One GAMS solve in its own directory. Returns wall seconds."""
    for f in RESULT_FILES:
        (model_dir / f).unlink(missing_ok=True)
    cmd = [f"{gams}/gams", f"{code}/highres.gms", f"--codefolderpath={code}",
           f"--weather_yr={year}", f"--dem_yr={year}", *SOLVE_ARGS, *extra, "lo=2"]
    t0 = time.time()
    with open(model_dir / log_name, "w") as log:
        subprocess.run(cmd, cwd=model_dir, stdout=log, stderr=subprocess.STDOUT)
    wall = time.time() - t0
    # The model's own gdx2sqlite call is behind a switch nothing sets, and that
    # binary is not shipped on macOS anyway. convert_results uses this instead.
    subprocess.run([f"{gams}/gamstool", "sqlitewrite", "gdxIn=results.gdx",
                    "o=results.db"], cwd=model_dir, capture_output=True)
    return wall


def scalars(db, names):
    """Read 0-dimensional symbols. gamstool puts them in `scalars`, not tables."""
    if not db.is_file():
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        qs = ",".join("?" * len(names))
        rows = con.execute(
            f"select name, value from scalars where name in ({qs})", tuple(names)
        ).fetchall()
    finally:
        con.close()
    return {n: v for n, v in rows}


def read_outcome(model_dir, what):
    """ENS share and solve status, refusing anything non-optimal."""
    s = scalars(model_dir / "results.db", ["o_modelstat", "o_solvestat",
                                           "o_ens_total", "o_ens_share"])
    stat = s.get("o_modelstat")
    if stat is None:
        sys.exit(f"{what}: no o_modelstat in results. Did the solve write a gdx?")
    if int(stat) not in OK_MODELSTAT:
        sys.exit(
            f"{what}: refusing to use a solve with modelstat {int(stat)}.\n"
            f"  ENS reported as {s.get('o_ens_total')}, which on an infeasible "
            f"model means nothing.\n"
            f"  Look at {model_dir / 'highres.lst'}; `iis 1` in cplex.opt names "
            f"the conflict."
        )
    return s


def zone_shares(db, zones):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("select * from o_ens_share_z").fetchall()
        cols = [d[0].lower() for d in con.execute("select * from o_ens_share_z").description]
    finally:
        con.close()
    zi, vi = 0, cols.index("value") if "value" in cols else len(cols) - 1
    return {r[zi]: r[vi] for r in rows if r[zi] in zones}


def parse_spec(spec):
    """name:cf:demand[:Tech=f;Tech=f] -> the same dict a yaml entry gives."""
    parts = spec.split(":")
    if len(parts) not in (3, 4):
        sys.exit(f"realisation spec {spec!r} is not "
                 "name:cf:demand[:Tech=f;Tech=f]")
    out = {"name": parts[0], "cf": float(parts[1]), "demand": float(parts[2])}
    if len(parts) == 4:
        out["cf_tech"] = dict(
            (k, float(v)) for k, v in
            (item.split("=") for item in parts[3].split(";") if item.strip()))
    return out


def load_realisations_file(path):
    """A yaml list of realisations. Needed for per-zone factors, which the
    colon-delimited spec cannot carry:

        - name: median
        - name: stress_PL
          demand_zone: {PL: 1.40}
        - name: stress_AT
          demand_zone: {AT: 1.40}
          cf_tech: {Windonshore: 0.80}
    """
    import yaml
    entries = yaml.safe_load(Path(path).read_text())
    if not isinstance(entries, list):
        sys.exit(f"{path} must hold a yaml LIST of realisations")
    for e in entries:
        if "name" not in e:
            sys.exit(f"realisation without a name in {path}: {e}")
    return entries


def build_realisations(entries, source, work, zones, year, gams):
    out = {}
    for e in entries:
        name = e["name"]
        d = work / "realisations" / name
        args = ["--from", source, "--to", d,
                "--cf", e.get("cf", 1.0), "--demand", e.get("demand", 1.0),
                "--zones", ",".join(zones), "--year", year, "--gams", gams]
        for flag, key in (("--cf-tech", "cf_tech"), ("--demand-zone", "demand_zone")):
            if e.get(key):
                args += [flag, ",".join(f"{k}={v}" for k, v in e[key].items())]
        script("make_realisation.py", *args)
        out[name] = {"dir": d, **{k: v for k, v in e.items() if k != "name"}}
    return out


def dispatch_all(realisations, fleet_dd, work, code, gams, year, jobs, iteration):
    """Rank step: one fixed-fleet min-ENS solve per realisation, in parallel."""
    def one(item):
        name, meta = item
        d = work / f"iter{iteration}" / f"dispatch_{name}"
        d.mkdir(parents=True, exist_ok=True)
        for f in meta["dir"].iterdir():
            if f.is_file() and f.name not in RESULT_FILES:
                shutil.copy2(f, d / f.name)
        shutil.copy2(fleet_dd, d / "fixed_caps.dd")
        wall = solve(d, code, gams, year,
                     ["--ens_obj=ON", "--fix_fleet=fixed_caps"])
        s = read_outcome(d, f"iteration {iteration}, realisation {name}")
        return name, {"dir": d, "wall": wall, "share": s["o_ens_share"],
                      "ens": s["o_ens_total"], "modelstat": int(s["o_modelstat"])}

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return dict(pool.map(one, realisations.items()))


def expand(worst_dir, fleet_dd, work, code, gams, year, iteration, zones):
    """Least-cost expansion on the worst realisation, new capacity floored."""
    d = work / f"iter{iteration}" / "expansion"
    d.mkdir(parents=True, exist_ok=True)
    for f in worst_dir.iterdir():
        if f.is_file() and f.name not in RESULT_FILES:
            shutil.copy2(f, d / f.name)
    shutil.copy2(fleet_dd, d / "fixed_caps.dd")
    wall = solve(d, code, gams, year, ["--fleet_floor=fixed_caps"])
    read_outcome(d, f"iteration {iteration}, expansion")
    new_dd = work / f"iter{iteration}" / "fleet_after.dd"
    script("caps2dd.py", "--from", d / "results.db", "--to", new_dd,
           "--zones", ",".join(zones))
    return d, new_dd, wall


# What a step hands back: the capacity built during it. The external code that
# owns decommissioning and the next step's parameters consumes this, so it is
# written as a flat table rather than left inside a gdx.
NEW_CAPACITY_TABLES = {
    "var_new_pcap_z": ("zone", "technology", "GW"),
    "var_new_vre_pcap_r": ("zone", "technology", "GW"),
    "var_new_store_pcap_z": ("zone", "technology", "GW"),
    "var_new_store_ecap_z": ("zone", "technology", "GWh"),
    "var_new_hydro_ecap_z": ("zone", "technology", "GWh"),
    "var_new_trans_pcap": ("zone", "technology", "GW"),
}


def write_new_capacity(db, out, zones, floor=1e-4):
    """The step output: what was built, from the last expansion's results."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = []
    try:
        have = {r[0] for r in con.execute(
            "select name from sqlite_master where type='table'")}
        for table, (_z, _t, unit) in NEW_CAPACITY_TABLES.items():
            if table not in have:
                continue
            cur = con.execute(f"select * from [{table}] where level > ?", (floor,))
            cols = [d[0].lower() for d in cur.description]
            li = cols.index("level")
            for r in cur.fetchall():
                dims = [r[i] for i in range(len(cols)) if i != li
                        and cols[i] not in {"marginal", "lower", "upper", "scale"}]
                if dims[0] not in zones:
                    continue
                rows.append({"table": table, "zone": dims[0],
                             "key": ".".join(str(d) for d in dims[1:]),
                             "value": r[li], "unit": unit})
    finally:
        con.close()
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["table", "zone", "key", "value", "unit"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["table"], r["zone"], r["key"])))
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--horizon", required=True)
    ap.add_argument("--zones", required=True)
    ap.add_argument("--model-dir", required=True,
                    help="a built model directory to take inputs from")
    ap.add_argument("--work", required=True, help="output root for this step")
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="ENS share a realisation must clear (default 0.05, a "
                         "placeholder, nowhere near a reliability standard)")
    ap.add_argument("--max-iter", type=int, default=5)
    ap.add_argument("--jobs", type=int, default=3,
                    help="parallel dispatch solves; bound this by memory and "
                         "disk, not by cores")
    ap.add_argument("--realisation", action="append", dest="realisations",
                    help="name:cf:demand[:Tech=f;Tech=f], repeatable")
    ap.add_argument("--realisations-file",
                    help="yaml list of realisations, which is the only form "
                         "that carries per-zone factors. Overrides "
                         "--realisation")
    ap.add_argument("--year", default="2010")
    ap.add_argument("--gams", default=GAMS_DEFAULT)
    ap.add_argument("--code", default=None, help="GAMS submodule directory")
    args = ap.parse_args()

    code = args.code or str((HERE / ".." / ".." / "resources" / "highRES-Europe-GAMS").resolve())
    zones = [z.strip() for z in args.zones.split(",") if z.strip()]
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    if args.realisations_file:
        entries = load_realisations_file(args.realisations_file)
    else:
        entries = [parse_spec(x) for x in (args.realisations or DEFAULT_REALISATIONS)]
    specs = [e["name"] for e in entries]

    print(f"pathway step, horizon {args.horizon}, zones {','.join(zones)}")
    print(f"tolerance {args.tolerance:.3%} of each realisation's own demand")
    print(f"{len(entries)} realisations: {', '.join(specs)}\n")

    fleet_dd = work / "fleet_start.dd"
    script("ledger2dd.py", "--ledger", args.ledger, "--horizon", args.horizon,
           "--zones", ",".join(zones), "--to", fleet_dd,
           "--scenario-dir", args.model_dir)
    print(f"starting fleet: {fleet_dd.name}")

    realisations = build_realisations(entries, args.model_dir, work, zones,
                                      args.year, args.gams)
    print(f"built {len(realisations)} realisation input sets\n")

    log, previous_worst = [], None
    for iteration in range(1, args.max_iter + 1):
        res = dispatch_all(realisations, fleet_dd, work, code, args.gams,
                           args.year, args.jobs, iteration)
        ranked = sorted(res.items(), key=lambda kv: kv[1]["share"], reverse=True)

        print(f"iteration {iteration}: rank")
        for name, r in ranked:
            flag = "FAIL" if r["share"] > args.tolerance else "ok"
            print(f"  {name:<8} share {r['share']:>9.4%}  ENS {r['ens']:>12,.1f} GWh"
                  f"  {r['wall']:>6.1f}s  [{flag}]")

        worst_name, worst = ranked[0]
        for name, r in res.items():
            log.append({"iteration": iteration, "realisation": name,
                        "share": r["share"], "ens_gwh": r["ens"],
                        "modelstat": r["modelstat"], "wall_s": round(r["wall"], 1)})

        if worst["share"] <= args.tolerance:
            print(f"\nall realisations within tolerance at iteration {iteration}")
            break

        if previous_worst is not None and worst["share"] > previous_worst + 1e-9:
            sys.exit(
                f"\nworst share rose from {previous_worst:.4%} to "
                f"{worst['share']:.4%} after an expansion. Capacity only ever "
                f"increases, so ENS cannot rise: the floors or the carried "
                f"fleet are wrong. Stopping rather than iterating."
            )
        previous_worst = worst["share"]

        if iteration == args.max_iter:
            print(f"\nhit --max-iter {args.max_iter} with {worst_name} still at "
                  f"{worst['share']:.4%}. Not converged.")
            break

        print(f"  expanding on {worst_name}")
        exp_dir, fleet_dd, wall = expand(worst["dir"], fleet_dd, work, code,
                                         args.gams, args.year, iteration, zones)
        print(f"  expansion solved in {wall:.1f}s -> {fleet_dd.name}\n")

    with open(work / "iterations.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(log[0].keys()))
        w.writeheader()
        w.writerows(log)

    last_expansion = sorted(work.glob("iter*/expansion/results.db"))
    built = 0
    if last_expansion:
        built = write_new_capacity(last_expansion[-1], work / "new_capacity.csv", set(zones))
        print(f"wrote {work / 'new_capacity.csv'} ({built} entries built this step)")
    else:
        (work / "new_capacity.csv").write_text("table,zone,key,value,unit\n")
        print("nothing was built this step: the starting fleet already cleared")

    summary = {"horizon": args.horizon, "zones": zones,
               "tolerance": args.tolerance, "realisations": specs,
               "iterations": len(set(r["iteration"] for r in log)),
               "final_fleet_dd": str(fleet_dd),
               "new_capacity_entries": built,
               "note": "Mock realisations from a scaled 2010. Machinery only; "
                       "nothing here is an adequacy result."}
    (work / "step_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"wrote {work / 'iterations.csv'} and {work / 'step_summary.json'}")


if __name__ == "__main__":
    main()
