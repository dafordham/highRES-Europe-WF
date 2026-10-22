"""[cl] runs one milestone of the pathway through the robust loop

called by the run_robust_loop rule, once per milestone, all in one SLURM job.

1. starting fleet = the milestone's inputs: existing capacity after
   retirement + plant under construction (fleet_from_inputs in GAMS)
2. rank: dispatch that fleet against every realisation at the same time,
   minimising ENS
3. if every realisation's ENS share is under the tolerance, stop
4. if not, least-cost expansion on the worst one (new capacity can't go below
   what earlier expansions built), then back to 2

only optimal results are used, except a dispatch at status 7 (feasible but not
proven optimal) with ENS under the tolerance: the true minimum can only be
lower, so it clears anyway.

the work dir is emptied at the start, so a rerun redoes the whole milestone.

Usage:
    python3 pathway_step.py --model-dir <milestone dir> --work <output dir> \
        --zones AT,CZ,HU,PL --realisations-file <yaml> [--tolerance 0.05]
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

import yaml

HERE = Path(__file__).resolve().parent
GAMS_DEFAULT = "/Library/Frameworks/GAMS.framework/Versions/53/Resources"

# [cl] modelstats we accept: 1 optimal, 2 locally optimal, 8 integer (UC)
# 7 is ok for a dispatch if ENS is under the tolerance, see judge()
OK_MODELSTAT = {1, 2, 8}
FEASIBLE_MODELSTAT = 7

# [cl] files in a milestone dir that aren't inputs, so they're not copied
# into the solve dirs
RESULT_FILES = ("results.gdx", "results.db", "results.db.tmp",
                "results.db.compressed", "highres.lst", "highres.log",
                "solve.log", "robust.log", "capacity_ledger.csv")

# [cl] GAMS settings used when running this script by hand, the rule passes its own
SOLVE_ARGS = [
    "--psys_scen=BASE", "--esys_scen=BASE", "--weather_yr=2010", "--dem_yr=2010",
    "--co2_target_type=intensity", "--co2_target_extent=all",
    "--outname=results", "--store_initial_level=0.5", "--store_final_level=0.5",
    "--hydro_res_initial_fill=0.8", "--hydro_res_min=0.5", "--pgen=20.0",
    "--emis_price=0", "--trans_inv=TYNDP", "--trans_cap_lim=20", "--UC=OFF",
    "--f_res=OFF", "--store_uc=OFF", "--EV=OFF", "--EV_flex=0", "--V2G=OFF",
    "--EV_soc_max=0.8",
]

# [cl] the numbers we read back from each solve
SCALARS = ["o_modelstat", "o_solvestat", "o_ens_total", "o_ens_share"]

# [cl] GAMS can't load CPLEX if the solve dir path is longer than ~212 chars
# ("Could not find algorithm CPL", status 13), and can't compile past ~228
# (tested on GAMS 53, 16 Oct 2026)
MAX_PATH = 210


# [cl] print straight away, so the log is up to date while the job runs
def say(*a):
    print(*a, flush=True)


def script(name, *args):
    """[cl] run another script in this folder, stop with its output if it fails"""
    cmd = [sys.executable, str(HERE / name), *map(str, args)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"{name} failed:\n{r.stdout.decode()}\n{r.stderr.decode()}")


def setting(settings, name, default):
    """[cl] value of one --name=value GAMS setting"""
    for s in settings:
        if s.startswith(f"--{name}="):
            return s.split("=", 1)[1]
    return default


def copy_inputs(src, dst):
    """[cl] copy a milestone's input files into a solve dir"""
    dst.mkdir(parents=True, exist_ok=True)
    for f in Path(src).iterdir():
        if f.is_file() and f.name not in RESULT_FILES:
            shutil.copy2(f, dst / f.name)


def query(db, sql):
    """[cl] run a query on results.db, [] if the table isn't there"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def solve(d, extra, settings, args):
    """[cl] one GAMS solve in dir d, then convert results.gdx to results.db
    returns the scalars and how long it took"""
    t0 = time.time()
    cmd = [f"{args.gams}/gams", f"{args.code}/highres.gms", *settings, *extra, "lo=2"]
    with open(d / "solve.log", "w") as log:
        subprocess.run(cmd, cwd=d, stdout=log, stderr=subprocess.STDOUT)
    if (d / "results.gdx").is_file():
        subprocess.run([f"{args.gams}/gamstool", "sqlitewrite",
                        "gdxIn=results.gdx", "o=results.db"], cwd=d, capture_output=True)
    names = ",".join(f"'{n}'" for n in SCALARS)
    s = dict(query(d / "results.db", f"select name, value from scalars where name in ({names})"))
    return s, time.time() - t0


def judge(s, what, path, tolerance=None):
    """[cl] can we use this result, and why ("optimal" or "upper bound")
    stops the run if not. dispatches pass the tolerance, expansions don't,
    so an expansion has to be optimal"""
    stat = s.get("o_modelstat")
    if stat is None:
        sys.exit(f"{what}: no model status in the results. Look at {path / 'highres.log'}")
    stat, share = int(stat), s.get("o_ens_share")
    if stat in OK_MODELSTAT:
        return "optimal"
    if stat == FEASIBLE_MODELSTAT and tolerance is not None:
        if share <= tolerance:
            return "upper bound"
        sys.exit(f"{what}: status 7 (feasible, not proven optimal) with ENS share "
                 f"{share:.4%}, above the tolerance. The true minimum could be "
                 f"lower, so it cannot be ranked. Look at {path / 'highres.lst'}")
    hint = ""
    if tolerance is None:
        hint = ("\n  An infeasible expansion means this realisation cannot be "
                "served within the limits of the inputs\n  (growth ceilings, "
                "plant under construction, transmission limits, land).")
    sys.exit(f"{what}: status {stat}, not usable.{hint}\n  Look at "
             f"{path / 'highres.lst'}. `iis 1` in cplex.opt names the conflict.")


def build_realisations(path, source, work, zones, settings, gams, vre_parquet):
    """[cl] make an input dir for each realisation in the yaml, with make_realisation.py"""
    entries = yaml.safe_load(Path(path).read_text())
    out = {}
    for e in entries:
        d = work / "realisations" / e["name"]
        args = ["--from", source, "--to", d, "--cf", e.get("cf", 1.0),
                "--demand", e.get("demand", 1.0), "--zones", ",".join(zones),
                "--year", setting(settings, "weather_yr", "2010"),
                "--esys", setting(settings, "esys_scen", "BASE"), "--gams", gams]
        if vre_parquet:
            args += ["--vre-parquet", vre_parquet]
        for flag, k in (("--cf-tech", "cf_tech"), ("--demand-zone", "demand_zone")):
            if e.get(k):
                args += [flag, ",".join(f"{a}={b}" for a, b in e[k].items())]
        script("make_realisation.py", *args)
        out[e["name"]] = d
    return out


def warn_vre_gaps(db):
    """[cl] warn if a zone has more existing VRE in the data than its grid cells
    hold. the fleet is fixed per cell, so the difference would be lost"""
    zonal = query(db, "select z, g, upper from var_exist_pcap_z "
                      "where g in (select * from vre)")
    cells = dict(((z, v), s) for z, v, s in query(
        db, "select z, vre, sum(level) from var_exist_vre_pcap_r group by z, vre"))
    for z, g, upper in zonal:
        gap = upper - cells.get((z, g), 0.0)
        if upper != float("inf") and gap > 1e-3:
            say(f"  WARNING existing {g} in {z}: up to {upper:.4f} GW per zone, "
                f"{cells.get((z, g), 0.0):.4f} GW in its cells. {gap:.4f} GW left out.")


def start_fleet(args, settings, work, zones):
    """[cl] starting fleet from the milestone's inputs, written to a .dd"""
    d = work / "start"
    copy_inputs(args.model_dir, d)
    solve(d, ["--fleet_from_inputs=ON"], settings, args)
    if not (d / "results.db").is_file():
        sys.exit(f"fleet_from_inputs wrote no results. Look at {d / 'highres.log'}")
    warn_vre_gaps(d / "results.db")
    fleet_dd = work / "fleet_start.dd"
    script("caps2dd.py", "--from", d / "results.db", "--to", fleet_dd,
           "--zones", ",".join(zones))
    return fleet_dd


def rank(realisations, fleet_dd, work, settings, args, iteration):
    """[cl] dispatch the fixed fleet against each realisation, minimising ENS,
    all at the same time"""
    def one(item):
        name, src = item
        d = work / f"iter{iteration}" / name
        copy_inputs(src, d)
        shutil.copy2(fleet_dd, d / "fixed_caps.dd")
        s, wall = solve(d, ["--ens_obj=ON", "--fix_fleet=fixed_caps"], settings, args)
        proof = judge(s, f"iteration {iteration}, {name}", d, args.tolerance)
        return name, {"dir": d, "src": src, "wall": wall, "share": s["o_ens_share"],
                      "ens": s["o_ens_total"], "modelstat": int(s["o_modelstat"]),
                      "proof": proof}

    with ThreadPoolExecutor(max_workers=len(realisations)) as pool:
        return dict(pool.map(one, realisations.items()))


def expand(worst, fleet_dd, work, settings, args, iteration, zones):
    """[cl] least-cost expansion on the worst realisation, new capacity can only
    go up from what's already built"""
    d = work / f"iter{iteration}" / "expansion"
    copy_inputs(worst["src"], d)
    shutil.copy2(fleet_dd, d / "fixed_caps.dd")
    s, wall = solve(d, ["--fleet_floor=fixed_caps"], settings, args)
    judge(s, f"iteration {iteration}, expansion", d)
    new_dd = work / f"iter{iteration}" / "fleet_after.dd"
    script("caps2dd.py", "--from", d / "results.db", "--to", new_dd,
           "--zones", ",".join(zones))
    return d, new_dd, wall


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-dir", required=True, type=Path,
                    help="the milestone's built model directory")
    ap.add_argument("--work", required=True, type=Path,
                    help="output directory, emptied at the start")
    ap.add_argument("--zones", required=True, help="comma separated")
    ap.add_argument("--realisations-file", required=True, help="yaml list")
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="ENS share of each realisation's own demand. 0.05 is a "
                         "placeholder, nowhere near a reliability standard")
    ap.add_argument("--max-iter", type=int, default=5)
    ap.add_argument("--gams-settings",
                    help="json list of GAMS settings from the rule. Default SOLVE_ARGS")
    ap.add_argument("--vre-parquet", help="capacity factors, if not in --model-dir")
    ap.add_argument("--results-to", help="copy the final results.gdx here")
    ap.add_argument("--gams", default=GAMS_DEFAULT)
    ap.add_argument("--code", help="GAMS model directory")
    args = ap.parse_args()

    args.code = args.code or str((HERE / ".." / ".." / "resources" / "highRES-Europe-GAMS").resolve())
    zones = [z.strip() for z in args.zones.split(",") if z.strip()]
    # [cl] read this before emptying the work dir, the rule puts it there
    if args.gams_settings:
        settings = json.loads(Path(args.gams_settings).read_text())
    else:
        settings = [f"--codefolderpath={args.code}", *SOLVE_ARGS]
    work = args.work.resolve()
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    say(f"milestone {args.model_dir}, {len(zones)} zones, tolerance {args.tolerance:.1%}")
    fleet_dd = start_fleet(args, settings, work, zones)
    realisations = build_realisations(args.realisations_file, args.model_dir, work,
                                      zones, settings, args.gams, args.vre_parquet)
    say(f"realisations: {', '.join(realisations)}\n")

    # [cl] check the solve dirs won't be too long for GAMS (see MAX_PATH)
    longest = max(len(str(work / f"iter{args.max_iter}" / n)) for n in [*realisations, "expansion"])
    if longest > MAX_PATH:
        sys.exit(f"solve directories would be up to {longest} characters long, over "
                 f"{MAX_PATH}, and GAMS then fails to load CPLEX. Use a shorter "
                 f"results path or shorter realisation names.")

    # [cl] the loop: rank, stop if all clear, otherwise expand on the worst
    log, previous_worst, final_dir = [], None, None
    for iteration in range(1, args.max_iter + 1):
        res = rank(realisations, fleet_dd, work, settings, args, iteration)
        ranked = sorted(res.items(), key=lambda kv: kv[1]["share"], reverse=True)

        say(f"iteration {iteration}: rank")
        for name, r in ranked:
            flag = "FAIL" if r["share"] > args.tolerance else "ok"
            if r["proof"] != "optimal":
                flag += ", " + r["proof"]
            say(f"  {name:<10} share {r['share']:>9.4%}  ENS {r['ens']:>12,.1f} GWh"
                f"  {r['wall']:>8.0f}s  [{flag}]")
            log.append({"iteration": iteration, "realisation": name,
                        "share": r["share"], "ens_gwh": r["ens"],
                        "modelstat": r["modelstat"], "proof": r["proof"],
                        "wall_s": round(r["wall"], 1)})

        worst_name, worst = ranked[0]
        if worst["share"] <= args.tolerance:
            say(f"\nall realisations within tolerance at iteration {iteration}")
            # [cl] if nothing was expanded, any dispatch of this rank has the
            # fleet we want, so use its results
            final_dir = final_dir or worst["dir"]
            break

        # [cl] sanity check: more capacity can't make ENS go up
        if previous_worst is not None and worst["share"] > previous_worst + 1e-9:
            sys.exit(f"\nworst share rose from {previous_worst:.4%} to "
                     f"{worst['share']:.4%} after an expansion. Capacity only "
                     f"increases, so ENS cannot rise: the floors or the fleet are wrong.")
        previous_worst = worst["share"]

        if iteration == args.max_iter:
            final_dir = None
            say(f"\nhit --max-iter {args.max_iter}, {worst_name} still at {worst['share']:.4%}")
            break

        say(f"  expanding on {worst_name}")
        final_dir, fleet_dd, wall = expand(worst, fleet_dd, work, settings, args,
                                           iteration, zones)
        say(f"  expansion solved in {wall:.0f}s\n")

    # [cl] save every rank's results, and a summary of the run
    with open(work / "iterations.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(log[0].keys()))
        w.writeheader()
        w.writerows(log)
    converged = final_dir is not None
    (work / "step_summary.json").write_text(json.dumps(
        {"model_dir": str(args.model_dir), "tolerance": args.tolerance,
         "realisations": list(realisations), "iterations": iteration,
         "converged": converged, "final_fleet_dd": str(fleet_dd),
         "final_results": str(final_dir / "results.gdx") if converged else None},
        indent=2))

    # [cl] hand the final results.gdx to the workflow (the ledger reads it)
    if not converged:
        sys.exit("not converged: nothing delivered, so the pathway stops here")
    if args.results_to:
        Path(args.results_to).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(final_dir / "results.gdx", args.results_to)
        say(f"delivered {final_dir / 'results.gdx'}")


if __name__ == "__main__":
    main()
