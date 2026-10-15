"""Run one pathway step: rank realisations by ENS, expand on the worst, repeat.

Climate-Robust Pathways. This is the loop itself. It lives outside Snakemake's
DAG deliberately: the number of iterations depends on the results, which no
DAG can express. In production it runs inside the run_robust_loop rule, once
per milestone.

One step, given a fleet and a set of realisations:

  1. Rank. Dispatch the fleet against every realisation (fix_fleet, ens_obj=ON,
     minimise ENS). Realisations are independent, so these run in parallel.
  2. If every ENS share is within tolerance, the step is done and nothing more
     is built.
  3. Otherwise expand on the worst realisation: least-cost capacity expansion
     with shedding switched off, existing capacity fixed and new capacity
     floored at whatever the previous expansion in this step built
     (fleet_floor).
  4. Re-rank against the enlarged fleet and repeat from 2.

The starting fleet comes from one of two places:

  --from-inputs   production. The milestone's own input files, as the inputs
                  step wrote them: existing capacity after retirement, plus
                  plant under construction. GAMS reads it off its own bounds
                  (fleet_from_inputs in highres.gms), so nothing is parsed here.
  --ledger        tests. A capacity ledger and a horizon, through ledger2dd.py.

Termination rests on monotonicity: every expansion only adds capacity, and
added capacity can only weakly reduce ENS for every realisation, so no
iteration undoes an earlier one. The price is overbuild. If a realisation's
share rises after an expansion, something is wrong with the floors or the
carried fleet, and the loop stops and says so rather than continuing.

Nothing is ranked off a non-optimal solve. GAMS exits 0 on an infeasible model
and still writes the result parameters, where an infeasible fixed-fleet
dispatch reports ENS = 0 -- indistinguishable from a realisation that clears
perfectly unless the status is checked.

One exception, for dispatches only: status 7 (feasible, not proven optimal)
when its ENS share is within the tolerance. A feasible solution's ENS is an
upper bound on the minimum, and ENS cannot go below zero, so the minimum is
within the tolerance too. At full year this is the usual outcome for a
realisation that clears, because with ENS near zero the barrier stalls on a
flat objective (fox, 2026-10-15: 0.016 GWh/yr after 1.5 h). iterations.csv
records which proof each result rests on.

Running on fox (--submit slurm). This process only coordinates: each GAMS
solve is its own SLURM job, submitted with `sbatch --wait`, and the rank's
dispatches are submitted together. Measured on fox, three dispatches as
separate jobs were done 3.6 h after submission against 9.2 h as one job.

Resuming. A milestone can take a day or more, so every finished solve leaves a
done.json in its directory, holding its result and a key built from the fleet
and settings it ran with. Rerunning the same command replays the loop: a solve
whose done.json matches is not run again, and a job an earlier run left in the
queue is waited for rather than submitted twice.

Usage, a local test:
    python3 pathway_step.py \
        --ledger ../../capacity_ledger.csv --horizon 2030 --zones AT,CZ,HU,PL \
        --model-dir <a built model directory> --work <output root> \
        [--tolerance 0.05] [--max-iter 5] [--jobs 3] \
        [--realisations-file <yaml> | --realisation harsh:0.90:1.08 ...]
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
# 8 integer solution (unit commitment). 7, feasible but not proven optimal, is
# accepted only for a dispatch that clears (see judge). Everything else
# -- 4 and 5 infeasible, 19 infeasible no solution -- is refused.
OK_MODELSTAT = {1, 2, 8}
FEASIBLE_MODELSTAT = 7

# Files that belong to whatever solve last ran in a directory, or that later
# workflow steps add to a milestone directory (the ledger). Never copied into a
# new solve directory, and not part of the inputs fingerprint.
RESULT_FILES = ("results.gdx", "results.db", "results.db.tmp",
                "results.db.compressed", "highres.lst", "highres.log",
                "solve.log", "done.json", "job.txt", "job.sh", "slurm.out",
                "capacity_ledger.csv")

# GAMS settings for a local test, when the rule does not pass its own. The
# year and the code folder are added in main().
SOLVE_ARGS = [
    "--psys_scen=BASE", "--co2_target_type=intensity", "--co2_target_extent=all",
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
    return r.stdout.decode()


def sha(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode())
        h.update(b"\0")
    return h.hexdigest()[:16]


def inputs_key(d):
    """Fingerprint of a model directory's inputs: names, sizes, modification
    times. Rebuilding the inputs changes it, so a resumed loop never reuses a
    solve made from older inputs. Cheap, unlike hashing gigabytes of gdx."""
    parts = []
    for f in sorted(Path(d).iterdir()):
        if f.is_file() and f.name not in RESULT_FILES:
            st = f.stat()
            parts += [f.name, st.st_size, int(st.st_mtime)]
    return sha(*parts)


def copy_inputs(src, dst):
    """A solve directory: the inputs of src, without any earlier results."""
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file() and f.name not in RESULT_FILES:
            shutil.copy2(f, dst / f.name)


def scalars(db, names=SCALARS):
    """Read 0-dimensional symbols. gamstool puts them in `scalars`, not tables."""
    if not db.is_file():
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        # fleet_from_inputs writes capacities only, no scalars at all
        if not con.execute("select 1 from sqlite_master where type='table' "
                           "and name='scalars'").fetchone():
            return {}
        qs = ",".join("?" * len(names))
        rows = con.execute(
            f"select name, value from scalars where name in ({qs})", tuple(names)
        ).fetchall()
    finally:
        con.close()
    return {n: v for n, v in rows}


class Runner:
    """Runs GAMS solves, here or as SLURM jobs, and remembers finished ones."""

    def __init__(self, args, settings):
        self.gams = args.gams
        self.code = args.code
        self.settings = settings
        self.submit = args.submit
        self.args = args

    def gams_cmd(self, extra):
        return [f"{self.gams}/gams", f"{self.code}/highres.gms",
                *self.settings, *extra, "lo=2"]

    def run(self, d, extra, kind, key, label):
        """One solve in directory d. Returns its record and whether it was reused.

        kind is "dispatch", "expansion" or "start", and only sets the job's
        size. key identifies what the solve was run with: a done.json with the
        same key is a finished result for exactly this solve.
        """
        done = d / "done.json"
        if done.is_file():
            rec = json.loads(done.read_text())
            if rec.get("key") == key:
                return rec, True

        adopted = self.submit == "slurm" and self.adopt_queued(d, key, label)
        if not adopted:
            for f in RESULT_FILES:
                (d / f).unlink(missing_ok=True)
            t0 = time.time()
            if self.submit == "slurm":
                self.run_slurm(d, extra, kind, key, label)
            else:
                self.run_local(d, extra)
            wall = time.time() - t0
        else:
            wall = None

        rec = {"key": key, "kind": kind, "wall_s": wall,
               "scalars": scalars(d / "results.db"),
               "has_gdx": (d / "results.gdx").is_file()}
        done.write_text(json.dumps(rec, indent=2))
        return rec, False

    def run_local(self, d, extra):
        with open(d / "solve.log", "w") as log:
            subprocess.run(self.gams_cmd(extra), cwd=d, stdout=log,
                           stderr=subprocess.STDOUT)
        # The model's own gdx2sqlite call is behind a switch nothing sets, and
        # that binary is not shipped on macOS anyway. convert_results uses this.
        if (d / "results.gdx").is_file():
            subprocess.run([f"{self.gams}/gamstool", "sqlitewrite",
                            "gdxIn=results.gdx", "o=results.db"],
                           cwd=d, capture_output=True)

    def job_size(self, kind):
        a = self.args
        return {
            "dispatch": (a.mem_dispatch, a.time_dispatch, a.cpus),
            "expansion": (a.mem_expansion, a.time_expansion, a.cpus),
            "start": (a.mem_start, a.time_start, 1),
        }[kind]

    def run_slurm(self, d, extra, kind, key, label):
        """Submit one solve and wait for it.

        The job also converts results.gdx to results.db, so the login node
        never does more than read a few scalars. The database is written under
        a temporary name and moved into place, so a job killed while writing it
        leaves no half-written results.db for a later run to trust.
        """
        gams_line = " ".join(shlex.quote(str(x)) for x in self.gams_cmd(extra))
        tool = shlex.quote(f"{self.gams}/gamstool")
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
        mem, limit, cpus = self.job_size(kind)
        cmd = ["sbatch", "--wait", "--parsable",
               f"--account={self.args.account}", f"--time={limit}",
               f"--mem={mem}", f"--cpus-per-task={cpus}", "--ntasks=1",
               f"--job-name={label}", f"--output={d / 'slurm.out'}"]
        if self.args.partition:
            cmd.append(f"--partition={self.args.partition}")
        cmd.append(str(d / "job.sh"))
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
        job = p.stdout.readline().strip().split(";")[0]
        (d / "job.txt").write_text(json.dumps({"job": job, "key": key}))
        say(f"    submitted {label} as job {job}")
        _, err = p.communicate()
        if p.returncode != 0:
            say(f"    job {job} ({label}) ended with code {p.returncode}. "
                f"{err.strip()[-300:]}")

    def adopt_queued(self, d, key, label):
        """Pick up a job an earlier run of this loop submitted.

        If it is still queued or running, wait for it. If it has finished and
        left a results.db, use that. Either way it is not submitted again.
        """
        jf = d / "job.txt"
        if not jf.is_file():
            return False
        rec = json.loads(jf.read_text())
        if rec.get("key") != key:
            return False
        job = rec["job"]
        if job_alive(job):
            say(f"    {label}: job {job} from an earlier run is still in the "
                f"queue, waiting for it")
            while job_alive(job):
                time.sleep(60)
        if (d / "results.db").is_file():
            say(f"    {label}: using the result of job {job}")
            return True
        return False


def job_alive(job):
    r = subprocess.run(["squeue", "-h", "-j", str(job), "-o", "%T"],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def judge(s, what, path, tolerance=None):
    """Decide whether a result may be used, and on what proof.

    Optimal solves are always accepted. A dispatch passes its tolerance, and
    then status 7 is accepted too if its ENS share is within it: that ENS is an
    upper bound on the minimum, so the realisation provably clears. Above the
    tolerance the minimum could be lower, so it cannot be ranked. Expansions
    pass no tolerance and must be optimal.
    """
    s = dict(s)
    stat = s.get("o_modelstat")
    if stat is None:
        sys.exit(f"{what}: no o_modelstat in results. Did the solve write a "
                 f"gdx? Look at {path / 'solve.log'}"
                 + (f" and {path / 'slurm.out'}" if (path / "slurm.out").is_file() else ""))
    stat = int(stat)
    if stat in OK_MODELSTAT:
        s["proof"] = "optimal"
        return s
    share = s.get("o_ens_share")
    if stat == FEASIBLE_MODELSTAT and tolerance is not None and share is not None:
        if share <= tolerance:
            s["proof"] = "upper bound"
            return s
        sys.exit(
            f"{what}: modelstat 7 (feasible, not proven optimal) with ENS share "
            f"{share:.4%}, above the tolerance {tolerance:.4%}.\n"
            f"  The true minimum could be lower, so this realisation cannot be "
            f"ranked.\n  Look at {path / 'highres.lst'}."
        )
    hint = ""
    if tolerance is None and stat in (4, 5, 19):
        hint = ("\n  An infeasible EXPANSION means this realisation cannot be "
                "served at all within the\n  limits of the inputs: growth "
                "ceilings, plant under construction, TYNDP lines, land.")
    sys.exit(
        f"{what}: refusing to use a solve with modelstat {stat}.\n"
        f"  ENS reported as {s.get('o_ens_total')}, which on an infeasible "
        f"model means nothing.{hint}\n"
        f"  Look at {path / 'highres.lst'}; `iis 1` in cplex.opt names "
        f"the conflict."
    )


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


def build_realisations(entries, source, work, zones, year, gams, esys, vre_parquet):
    """One input directory per realisation. Rebuilt only if missing, since a
    full-year rescale takes minutes and the result depends only on the entry."""
    out = {}
    for e in entries:
        name = e["name"]
        d = work / "realisations" / name
        key = sha(json.dumps(e, sort_keys=True), inputs_key(source),
                  ",".join(zones), year, esys)
        marker = d / "realisation.json"
        if not (marker.is_file() and json.loads(marker.read_text()).get("key") == key):
            if d.exists():
                shutil.rmtree(d)
            args = ["--from", source, "--to", d,
                    "--cf", e.get("cf", 1.0), "--demand", e.get("demand", 1.0),
                    "--zones", ",".join(zones), "--year", year, "--gams", gams,
                    "--esys", esys]
            if vre_parquet:
                args += ["--vre-parquet", vre_parquet]
            for flag, k in (("--cf-tech", "cf_tech"), ("--demand-zone", "demand_zone")):
                if e.get(k):
                    args += [flag, ",".join(f"{a}={b}" for a, b in e[k].items())]
            script("make_realisation.py", *args)
            marker.write_text(json.dumps({"key": key, "entry": e}, indent=2))
        out[name] = {"dir": d, "key": key,
                     **{k: v for k, v in e.items() if k != "name"}}
    return out


def start_fleet(args, runner, work, zones):
    """The fleet the first rank dispatches."""
    fleet_dd = work / "fleet_start.dd"
    if args.ledger:
        script("ledger2dd.py", "--ledger", args.ledger, "--horizon", args.horizon,
               "--zones", ",".join(zones), "--to", fleet_dd,
               "--scenario-dir", args.model_dir)
        say(f"starting fleet from the ledger: {fleet_dd.name}")
        return fleet_dd

    d = work / "start"
    copy_inputs(Path(args.model_dir), d)
    key = sha(inputs_key(args.model_dir), *runner.settings, "fleet_from_inputs")
    rec, reused = runner.run(d, ["--fleet_from_inputs=ON"], "start", key, "rob_start")
    if not rec["has_gdx"] or not (d / "results.db").is_file():
        sys.exit(f"fleet_from_inputs wrote no results. Look at {d / 'solve.log'}")
    if reused and fleet_dd.is_file():
        return fleet_dd
    script("caps2dd.py", "--from", d / "results.db", "--to", fleet_dd,
           "--zones", ",".join(zones))
    say(f"starting fleet from the milestone's inputs: {fleet_dd.name}")
    return fleet_dd


def dispatch_all(realisations, fleet_dd, work, runner, jobs, iteration, tolerance):
    """Rank step: one fixed-fleet min-ENS solve per realisation, in parallel."""
    fleet_key = sha(fleet_dd.read_bytes())

    def one(item):
        name, meta = item
        d = work / f"iter{iteration}" / f"dispatch_{name}"
        copy_inputs(meta["dir"], d)
        shutil.copy2(fleet_dd, d / "fixed_caps.dd")
        key = sha(fleet_key, meta["key"], *runner.settings, "dispatch")
        rec, reused = runner.run(d, ["--ens_obj=ON", "--fix_fleet=fixed_caps"],
                                 "dispatch", key, f"rob_i{iteration}_{name}")
        s = judge(rec["scalars"], f"iteration {iteration}, realisation {name}",
                  d, tolerance)
        return name, {"dir": d, "key": meta["key"], "wall": rec["wall_s"],
                      "reused": reused,
                      "share": s["o_ens_share"], "ens": s["o_ens_total"],
                      "modelstat": int(s["o_modelstat"]), "proof": s["proof"]}

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return dict(pool.map(one, realisations.items()))


def expand(worst_dir, worst_key, fleet_dd, work, runner, iteration, zones):
    """Least-cost expansion on the worst realisation, new capacity floored."""
    d = work / f"iter{iteration}" / "expansion"
    copy_inputs(worst_dir, d)
    shutil.copy2(fleet_dd, d / "fixed_caps.dd")
    key = sha(fleet_dd.read_bytes(), worst_key, *runner.settings, "expansion")
    rec, reused = runner.run(d, ["--fleet_floor=fixed_caps"], "expansion", key,
                             f"rob_i{iteration}_expand")
    judge(rec["scalars"], f"iteration {iteration}, expansion", d)
    new_dd = work / f"iter{iteration}" / "fleet_after.dd"
    if not (reused and new_dd.is_file()):
        script("caps2dd.py", "--from", d / "results.db", "--to", new_dd,
               "--zones", ",".join(zones))
    return d, new_dd, rec["wall_s"], reused


def prune(res):
    """Drop a rank's large result files once an expansion has superseded it.

    done.json keeps everything the loop reads, so resuming still works. Only
    the final rank, and every expansion, keeps its results.gdx: one of them is
    the milestone's output.
    """
    for r in res.values():
        for f in ("results.gdx", "results.db"):
            (r["dir"] / f).unlink(missing_ok=True)


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
    """The step output: what was built, from the results that are delivered."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = []
    try:
        have = {r[0] for r in con.execute(
            "select name from sqlite_master where type='table'")}
        for table, (_z, _t, unit) in NEW_CAPACITY_TABLES.items():
            if table not in have:
                continue
            cols = [d[0].lower() for d in
                    con.execute(f"select * from [{table}] limit 0").description]
            if "level" not in cols:
                continue
            cur = con.execute(f"select * from [{table}] where level > ?", (floor,))
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
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    start = ap.add_mutually_exclusive_group(required=True)
    start.add_argument("--from-inputs", action="store_true",
                       help="start from the milestone's own inputs (production)")
    start.add_argument("--ledger", help="start from a capacity ledger (tests)")
    ap.add_argument("--horizon", help="milestone year, needed with --ledger")
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
    ap.add_argument("--year", default="2010", help="weather and demand year")
    ap.add_argument("--esys", default="BASE",
                    help="energy system scenario, which names the demand file")
    ap.add_argument("--vre-parquet",
                    help="capacity factors to rescale, if not in --model-dir")
    ap.add_argument("--gams", default=GAMS_DEFAULT)
    ap.add_argument("--code", default=None, help="GAMS submodule directory")
    ap.add_argument("--gams-settings",
                    help="json list of GAMS settings, written by the "
                         "run_robust_loop rule so the loop solves with the "
                         "workflow's own scenario settings. Without it, "
                         "SOLVE_ARGS are used")
    ap.add_argument("--results-to",
                    help="copy the milestone's final results.gdx here")
    ap.add_argument("--submit", choices=["local", "slurm"], default="local")
    ap.add_argument("--account", default="ec85")
    ap.add_argument("--partition", default="")
    ap.add_argument("--cpus", type=int, default=30,
                    help="CPUs per solve job; must equal threads in cplex.opt")
    ap.add_argument("--mem-dispatch", default="64G")
    ap.add_argument("--mem-expansion", default="80G")
    ap.add_argument("--mem-start", default="16G")
    ap.add_argument("--time-dispatch", default="12:00:00")
    ap.add_argument("--time-expansion", default="12:00:00")
    ap.add_argument("--time-start", default="01:00:00")
    ap.add_argument("--keep-all", action="store_true",
                    help="keep every rank's results.gdx and results.db. By "
                         "default, with --submit slurm, a rank an expansion "
                         "has superseded keeps only its logs and done.json")
    args = ap.parse_args()
    if args.ledger and not args.horizon:
        ap.error("--ledger needs --horizon")

    args.code = args.code or str((HERE / ".." / ".." / "resources" / "highRES-Europe-GAMS").resolve())
    zones = [z.strip() for z in args.zones.split(",") if z.strip()]
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    if args.gams_settings:
        settings = json.loads(Path(args.gams_settings).read_text())
    else:
        settings = [f"--codefolderpath={args.code}", f"--weather_yr={args.year}",
                    f"--dem_yr={args.year}", *SOLVE_ARGS]
    runner = Runner(args, settings)
    prune_ranks = args.submit == "slurm" and not args.keep_all

    if args.realisations_file:
        entries = load_realisations_file(args.realisations_file)
    else:
        entries = [parse_spec(x) for x in (args.realisations or DEFAULT_REALISATIONS)]
    specs = [e["name"] for e in entries]

    say(f"pathway step, model directory {args.model_dir}")
    say(f"zones {','.join(zones)}, solves {args.submit}")
    say(f"tolerance {args.tolerance:.3%} of each realisation's own demand")
    say(f"{len(entries)} realisations: {', '.join(specs)}\n")

    fleet_dd = start_fleet(args, runner, work, zones)

    realisations = build_realisations(entries, args.model_dir, work, zones,
                                      args.year, args.gams, args.esys,
                                      args.vre_parquet)
    say(f"{len(realisations)} realisation input sets ready\n")

    log, previous_worst, converged = [], None, False
    final_dir = None
    for iteration in range(1, args.max_iter + 1):
        res = dispatch_all(realisations, fleet_dd, work, runner, args.jobs,
                           iteration, args.tolerance)
        ranked = sorted(res.items(), key=lambda kv: kv[1]["share"], reverse=True)

        say(f"iteration {iteration}: rank")
        for name, r in ranked:
            flag = "FAIL" if r["share"] > args.tolerance else "ok"
            if r["proof"] != "optimal":
                flag += ", " + r["proof"]
            wall = "reused" if r["reused"] else f"{r['wall']:.1f}s"
            say(f"  {name:<10} share {r['share']:>9.4%}  ENS {r['ens']:>12,.1f} GWh"
                f"  {wall:>8}  [{flag}]")

        worst_name, worst = ranked[0]
        for name, r in res.items():
            log.append({"iteration": iteration, "realisation": name,
                        "share": r["share"], "ens_gwh": r["ens"],
                        "modelstat": r["modelstat"], "proof": r["proof"],
                        "wall_s": None if r["wall"] is None else round(r["wall"], 1)})

        if worst["share"] <= args.tolerance:
            say(f"\nall realisations within tolerance at iteration {iteration}")
            converged = True
            if final_dir is None:
                # Nothing was built beyond plant under construction. Any of this
                # rank's dispatches carries exactly that fleet.
                final_dir = worst["dir"]
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
            say(f"\nhit --max-iter {args.max_iter} with {worst_name} still at "
                f"{worst['share']:.4%}. Not converged.")
            break

        say(f"  expanding on {worst_name}")
        exp_dir, fleet_dd, wall, reused = expand(worst["dir"], worst["key"],
                                                 fleet_dd, work, runner,
                                                 iteration, zones)
        final_dir = exp_dir
        say(f"  expansion {'reused' if reused else f'solved in {wall:.1f}s'}"
            f" -> {fleet_dd.name}\n")
        if prune_ranks:
            prune(res)

    with open(work / "iterations.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(log[0].keys()))
        w.writeheader()
        w.writerows(log)

    summary = {"model_dir": str(args.model_dir), "zones": zones,
               "tolerance": args.tolerance, "realisations": specs,
               "iterations": len(set(r["iteration"] for r in log)),
               "converged": converged,
               "final_fleet_dd": str(fleet_dd),
               "final_results": str(final_dir / "results.gdx") if final_dir else None,
               "note": "Mock realisations until C2E. Machinery only; nothing "
                       "here is an adequacy result."}

    if converged:
        built = write_new_capacity(final_dir / "results.db",
                                   work / "new_capacity.csv", set(zones))
        summary["new_capacity_entries"] = built
        say(f"wrote {work / 'new_capacity.csv'} ({built} new entries, plant "
            f"under construction included)")
        if args.results_to:
            dst = Path(args.results_to)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final_dir / "results.gdx", dst)
            say(f"delivered {final_dir / 'results.gdx'} -> {dst}")

    (work / "step_summary.json").write_text(json.dumps(summary, indent=2))
    say(f"wrote {work / 'iterations.csv'} and {work / 'step_summary.json'}")
    if not converged:
        sys.exit("not converged: nothing delivered, so the pathway stops here")


if __name__ == "__main__":
    main()
