"""One pathway milestone through the robust loop.

Climate-Robust Pathways. Run by the run_robust_loop rule, once per milestone:

  1. The starting fleet: the milestone's inputs, existing capacity after
     retirement plus plant under construction (fleet_from_inputs in GAMS).
  2. Rank: dispatch the fleet against every realisation, minimising ENS.
  3. If every realisation's ENS share is within the tolerance, stop.
  4. Otherwise a least-cost expansion on the worst realisation, new capacity
     floored at what earlier expansions built, and back to 2.

Results are only used from optimal solves. One exception: a dispatch at status
7 (feasible, not proven optimal) whose ENS share is within the tolerance. Its
ENS is an upper bound on the minimum, so the realisation provably clears.

With --submit slurm every GAMS solve is its own SLURM job. Each finished solve
leaves a done.json, so rerunning the same command after a crash reuses what is
done and waits for jobs still in the queue rather than submitting them again.

Usage:
    python3 pathway_step.py --model-dir <milestone dir> --work <output dir> \
        --zones AT,CZ,HU,PL --realisations-file <yaml> [--tolerance 0.05]
"""

import argparse
import csv
import hashlib
import json
import shlex
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

# Model status a result may be used from: optimal, locally optimal, integer
# solution (unit commitment). 7 only for a dispatch that clears, see judge().
OK_MODELSTAT = {1, 2, 8}
FEASIBLE_MODELSTAT = 7

# Files written by a solve, or by later workflow steps (the ledger). Never
# copied into a new solve directory, and left out of the inputs fingerprint.
RESULT_FILES = ("results.gdx", "results.db", "results.db.tmp",
                "results.db.compressed", "highres.lst", "highres.log",
                "solve.log", "done.json", "job.txt", "job.sh", "slurm.out",
                "capacity_ledger.csv", "realisation.json")

# GAMS settings for a run outside the workflow. The rule passes its own.
SOLVE_ARGS = [
    "--psys_scen=BASE", "--esys_scen=BASE", "--weather_yr=2010", "--dem_yr=2010",
    "--co2_target_type=intensity", "--co2_target_extent=all",
    "--outname=results", "--store_initial_level=0.5", "--store_final_level=0.5",
    "--hydro_res_initial_fill=0.8", "--hydro_res_min=0.5", "--pgen=20.0",
    "--emis_price=0", "--trans_inv=TYNDP", "--trans_cap_lim=20", "--UC=OFF",
    "--f_res=OFF", "--store_uc=OFF", "--EV=OFF", "--EV_flex=0", "--V2G=OFF",
    "--EV_soc_max=0.8",
]

SCALARS = ["o_modelstat", "o_solvestat", "o_ens_total", "o_ens_share"]


def say(*a):
    print(*a, flush=True)


def script(name, *args):
    """Run one of the sibling scripts and fail loudly."""
    cmd = [sys.executable, str(HERE / name), *map(str, args)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"{name} failed:\n{r.stdout.decode()}\n{r.stderr.decode()}")


def sha(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode())
        h.update(b"\0")
    return h.hexdigest()[:16]


def inputs_key(d):
    """Fingerprint of a directory's inputs: names, sizes, modification times.
    Rebuilt inputs change it, so a resumed loop never reuses a stale solve."""
    parts = []
    for f in sorted(Path(d).iterdir()):
        if f.is_file() and f.name not in RESULT_FILES:
            st = f.stat()
            parts += [f.name, st.st_size, int(st.st_mtime)]
    return sha(*parts)


def setting(settings, name, default):
    """The value of one --name=value GAMS setting."""
    for s in settings:
        if s.startswith(f"--{name}="):
            return s.split("=", 1)[1]
    return default


def copy_inputs(src, dst):
    dst.mkdir(parents=True, exist_ok=True)
    for f in Path(src).iterdir():
        if f.is_file() and f.name not in RESULT_FILES:
            shutil.copy2(f, dst / f.name)


def query(db, sql):
    """Rows of one query on a results.db, or [] if the table is missing."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def scalars(db):
    """0-dimensional symbols. gamstool puts them in a table called scalars."""
    if not db.is_file():
        return {}
    names = ",".join(f"'{n}'" for n in SCALARS)
    return dict(query(db, f"select name, value from scalars where name in ({names})"))


class Runner:
    """Runs GAMS solves, here or as SLURM jobs, and remembers finished ones."""

    def __init__(self, args, settings):
        self.args = args
        self.settings = settings

    def gams_cmd(self, extra):
        a = self.args
        return [f"{a.gams}/gams", f"{a.code}/highres.gms", *self.settings, *extra, "lo=2"]

    def run(self, d, extra, kind, key, label):
        """One solve in directory d. Returns its record and whether it was reused.

        kind (dispatch, expansion, start) sets the job's size. key says what the
        solve was run with, so a done.json with the same key is its result.
        """
        done = d / "done.json"
        if done.is_file():
            rec = json.loads(done.read_text())
            if rec["key"] == key:
                return rec, True

        # A job an earlier run submitted counts as reused: not solved now.
        adopted = self.args.submit == "slurm" and self.adopt_queued(d, key, label)
        wall = None
        if not adopted:
            for f in RESULT_FILES:
                (d / f).unlink(missing_ok=True)
            t0 = time.time()
            if self.args.submit == "slurm":
                self.run_slurm(d, extra, kind, key, label)
            else:
                self.run_local(d, extra)
            wall = time.time() - t0

        rec = {"key": key, "wall_s": wall, "scalars": scalars(d / "results.db"),
               "has_results": (d / "results.db").is_file()}
        done.write_text(json.dumps(rec, indent=2))
        return rec, adopted

    def run_local(self, d, extra):
        with open(d / "solve.log", "w") as log:
            subprocess.run(self.gams_cmd(extra), cwd=d, stdout=log,
                           stderr=subprocess.STDOUT)
        if (d / "results.gdx").is_file():
            subprocess.run([f"{self.args.gams}/gamstool", "sqlitewrite",
                            "gdxIn=results.gdx", "o=results.db"],
                           cwd=d, capture_output=True)

    def run_slurm(self, d, extra, kind, key, label):
        """Submit one solve and wait for it.

        The job converts results.gdx to results.db itself, so the login node
        only reads a few numbers. The database is moved into place when
        complete, so a job killed while writing it leaves nothing to trust.
        """
        a = self.args
        gams_line = " ".join(shlex.quote(str(x)) for x in self.gams_cmd(extra))
        tool = shlex.quote(f"{a.gams}/gamstool")
        (d / "job.sh").write_text(
            "#!/bin/bash\n"
            f"cd {shlex.quote(str(d))}\n"
            f"{gams_line} > solve.log 2>&1\n"
            "rc=$?\n"
            "if [ -f results.gdx ]; then\n"
            f"  {tool} sqlitewrite gdxIn=results.gdx o=results.db.tmp > /dev/null"
            " && mv results.db.tmp results.db\n"
            "fi\n"
            "exit $rc\n"
        )
        mem, limit, cpus = {
            "dispatch": (a.mem_dispatch, a.time_dispatch, a.cpus),
            "expansion": (a.mem_expansion, a.time_expansion, a.cpus),
            "start": ("16G", "01:00:00", 1),
        }[kind]
        cmd = ["sbatch", "--wait", "--parsable", f"--account={a.account}",
               f"--time={limit}", f"--mem={mem}", f"--cpus-per-task={cpus}",
               "--ntasks=1", f"--job-name={label}", f"--output={d / 'slurm.out'}"]
        if a.partition:
            cmd.append(f"--partition={a.partition}")
        cmd.append(str(d / "job.sh"))
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        job = p.stdout.readline().strip().split(";")[0]
        (d / "job.txt").write_text(json.dumps({"job": job, "key": key}))
        say(f"    submitted {label} as job {job}")
        _, err = p.communicate()
        if p.returncode != 0:
            say(f"    job {job} ({label}) ended with code {p.returncode}. {err.strip()[-300:]}")

    def adopt_queued(self, d, key, label):
        """Use a job an earlier run of the loop submitted, rather than resubmit:
        wait for it if it is still queued, then take its results if it left any."""
        jf = d / "job.txt"
        if not jf.is_file():
            return False
        rec = json.loads(jf.read_text())
        if rec["key"] != key:
            return False
        if job_alive(rec["job"]):
            say(f"    {label}: job {rec['job']} from an earlier run is still queued, waiting")
            while job_alive(rec["job"]):
                time.sleep(60)
        if (d / "results.db").is_file():
            say(f"    {label}: using the result of job {rec['job']}")
            return True
        return False


def job_alive(job):
    r = subprocess.run(["squeue", "-h", "-j", str(job), "-o", "%T"],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def judge(s, what, path, tolerance=None):
    """Whether a result may be used, and on what proof. Dispatches pass their
    tolerance, expansions none, so an expansion must be optimal."""
    stat = s.get("o_modelstat")
    if stat is None:
        sys.exit(f"{what}: no model status in the results. Look at "
                 f"{path / 'solve.log'} and, on fox, {path / 'slurm.out'}")
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
    """One input directory per realisation in the yaml file. Rebuilt only when
    the entry or the inputs change: at full year a rescale takes minutes."""
    entries = yaml.safe_load(Path(path).read_text())
    year = setting(settings, "weather_yr", "2010")
    esys = setting(settings, "esys_scen", "BASE")
    out = {}
    for e in entries:
        d = work / "realisations" / e["name"]
        key = sha(json.dumps(e, sort_keys=True), inputs_key(source), ",".join(zones), year, esys)
        marker = d / "realisation.json"
        if not (marker.is_file() and json.loads(marker.read_text())["key"] == key):
            shutil.rmtree(d, ignore_errors=True)
            args = ["--from", source, "--to", d, "--cf", e.get("cf", 1.0),
                    "--demand", e.get("demand", 1.0), "--zones", ",".join(zones),
                    "--year", year, "--esys", esys, "--gams", gams]
            if vre_parquet:
                args += ["--vre-parquet", vre_parquet]
            for flag, k in (("--cf-tech", "cf_tech"), ("--demand-zone", "demand_zone")):
                if e.get(k):
                    args += [flag, ",".join(f"{a}={b}" for a, b in e[k].items())]
            script("make_realisation.py", *args)
            marker.write_text(json.dumps({"key": key, "entry": e}, indent=2))
        out[e["name"]] = {"dir": d, "key": key}
    return out


def warn_vre_gaps(db):
    """Warn where a zone's existing VRE, as the data bounds it, exceeds what its
    grid cells hold. The fleet takes the cells, so that capacity would be lost."""
    zonal = query(db, "select z, g, upper from var_exist_pcap_z "
                      "where g in (select * from vre)")
    cells = dict(((z, v), s) for z, v, s in query(
        db, "select z, vre, sum(level) from var_exist_vre_pcap_r group by z, vre"))
    for z, g, upper in zonal:
        gap = upper - cells.get((z, g), 0.0)
        if upper != float("inf") and gap > 1e-3:
            say(f"  WARNING existing {g} in {z}: up to {upper:.4f} GW per zone, "
                f"{cells.get((z, g), 0.0):.4f} GW in its cells. {gap:.4f} GW left out.")


def start_fleet(args, runner, work, zones):
    """The fleet of the milestone's inputs, as a fleet .dd."""
    d = work / "start"
    fleet_dd = work / "fleet_start.dd"
    copy_inputs(args.model_dir, d)
    key = sha(inputs_key(args.model_dir), *runner.settings, "fleet_from_inputs")
    rec, reused = runner.run(d, ["--fleet_from_inputs=ON"], "start", key, "rob_start")
    if not rec["has_results"]:
        sys.exit(f"fleet_from_inputs wrote no results. Look at {d / 'solve.log'}")
    if not (reused and fleet_dd.is_file()):
        warn_vre_gaps(d / "results.db")
        script("caps2dd.py", "--from", d / "results.db", "--to", fleet_dd,
               "--zones", ",".join(zones))
    return fleet_dd


def rank(realisations, fleet_dd, work, runner, iteration, tolerance):
    """One fixed-fleet min-ENS dispatch per realisation, in parallel."""
    fleet_key = sha(fleet_dd.read_bytes())

    def one(item):
        name, r = item
        d = work / f"iter{iteration}" / f"dispatch_{name}"
        copy_inputs(r["dir"], d)
        shutil.copy2(fleet_dd, d / "fixed_caps.dd")
        key = sha(fleet_key, r["key"], *runner.settings, "dispatch")
        rec, reused = runner.run(d, ["--ens_obj=ON", "--fix_fleet=fixed_caps"],
                                 "dispatch", key, f"rob_i{iteration}_{name}")
        s = rec["scalars"]
        proof = judge(s, f"iteration {iteration}, {name}", d, tolerance)
        return name, {"dir": d, "key": r["key"], "wall": rec["wall_s"],
                      "reused": reused, "share": s["o_ens_share"],
                      "ens": s["o_ens_total"], "modelstat": int(s["o_modelstat"]),
                      "proof": proof}

    with ThreadPoolExecutor(max_workers=runner.args.jobs) as pool:
        return dict(pool.map(one, realisations.items()))


def expand(worst, fleet_dd, work, runner, iteration, zones):
    """Least-cost expansion on the worst realisation, new capacity floored."""
    d = work / f"iter{iteration}" / "expansion"
    copy_inputs(worst["dir"], d)
    shutil.copy2(fleet_dd, d / "fixed_caps.dd")
    key = sha(fleet_dd.read_bytes(), worst["key"], *runner.settings, "expansion")
    rec, reused = runner.run(d, ["--fleet_floor=fixed_caps"], "expansion", key,
                             f"rob_i{iteration}_expand")
    judge(rec["scalars"], f"iteration {iteration}, expansion", d)
    new_dd = work / f"iter{iteration}" / "fleet_after.dd"
    if not (reused and new_dd.is_file()):
        script("caps2dd.py", "--from", d / "results.db", "--to", new_dd,
               "--zones", ",".join(zones))
    return d, new_dd, rec["wall_s"], reused


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-dir", required=True, type=Path,
                    help="the milestone's built model directory")
    ap.add_argument("--work", required=True, type=Path, help="output directory")
    ap.add_argument("--zones", required=True, help="comma separated")
    ap.add_argument("--realisations-file", required=True, help="yaml list")
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="ENS share of each realisation's own demand. 0.05 is a "
                         "placeholder, nowhere near a reliability standard")
    ap.add_argument("--max-iter", type=int, default=5)
    ap.add_argument("--jobs", type=int, default=3, help="dispatches at once")
    ap.add_argument("--gams-settings",
                    help="json list of GAMS settings from the rule. Default SOLVE_ARGS")
    ap.add_argument("--vre-parquet", help="capacity factors, if not in --model-dir")
    ap.add_argument("--results-to", help="copy the final results.gdx here")
    ap.add_argument("--gams", default=GAMS_DEFAULT)
    ap.add_argument("--code", help="GAMS model directory")
    ap.add_argument("--submit", choices=["local", "slurm"], default="local")
    ap.add_argument("--account", default="ec85")
    ap.add_argument("--partition", default="")
    ap.add_argument("--cpus", type=int, default=30, help="per job, = threads in cplex.opt")
    ap.add_argument("--mem-dispatch", default="64G")
    ap.add_argument("--mem-expansion", default="80G")
    ap.add_argument("--time-dispatch", default="12:00:00")
    ap.add_argument("--time-expansion", default="12:00:00")
    args = ap.parse_args()

    args.code = args.code or str((HERE / ".." / ".." / "resources" / "highRES-Europe-GAMS").resolve())
    args.work = args.work.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    zones = [z.strip() for z in args.zones.split(",") if z.strip()]
    if args.gams_settings:
        settings = json.loads(Path(args.gams_settings).read_text())
    else:
        settings = [f"--codefolderpath={args.code}", *SOLVE_ARGS]
    runner = Runner(args, settings)
    work = args.work

    say(f"milestone {args.model_dir}, {len(zones)} zones, solves {args.submit}, "
        f"tolerance {args.tolerance:.1%}")
    fleet_dd = start_fleet(args, runner, work, zones)
    realisations = build_realisations(args.realisations_file, args.model_dir, work,
                                      zones, settings, args.gams, args.vre_parquet)
    say(f"realisations: {', '.join(realisations)}\n")

    log, previous_worst, final_dir = [], None, None
    for iteration in range(1, args.max_iter + 1):
        res = rank(realisations, fleet_dd, work, runner, iteration, args.tolerance)
        ranked = sorted(res.items(), key=lambda kv: kv[1]["share"], reverse=True)

        say(f"iteration {iteration}: rank")
        for name, r in ranked:
            flag = "FAIL" if r["share"] > args.tolerance else "ok"
            if r["proof"] != "optimal":
                flag += ", " + r["proof"]
            wall = "reused" if r["reused"] else f"{r['wall']:.1f}s"
            say(f"  {name:<10} share {r['share']:>9.4%}  ENS {r['ens']:>12,.1f} GWh"
                f"  {wall:>8}  [{flag}]")
            log.append({"iteration": iteration, "realisation": name,
                        "share": r["share"], "ens_gwh": r["ens"],
                        "modelstat": r["modelstat"], "proof": r["proof"],
                        "wall_s": None if r["wall"] is None else round(r["wall"], 1)})

        worst_name, worst = ranked[0]
        if worst["share"] <= args.tolerance:
            say(f"\nall realisations within tolerance at iteration {iteration}")
            # Nothing built beyond plant under construction: any dispatch of
            # this rank carries exactly that fleet.
            final_dir = final_dir or worst["dir"]
            break

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
        final_dir, fleet_dd, wall, reused = expand(worst, fleet_dd, work, runner,
                                                   iteration, zones)
        say(f"  expansion {'reused' if reused else f'solved in {wall:.1f}s'}\n")
        if args.submit == "slurm":
            # Superseded by the expansion. done.json keeps what resuming needs.
            for r in res.values():
                for f in ("results.gdx", "results.db"):
                    (r["dir"] / f).unlink(missing_ok=True)

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

    if not converged:
        sys.exit("not converged: nothing delivered, so the pathway stops here")
    if args.results_to:
        Path(args.results_to).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(final_dir / "results.gdx", args.results_to)
        say(f"delivered {final_dir / 'results.gdx'}")


if __name__ == "__main__":
    main()
