"""Solve one highRES run outside Snakemake, reusing an existing set of inputs.

For the handful of special runs that must not disturb a finished sweep: a
crossover re-solve, a per-zone shedding cap, or a few extra grid points. Going
through Snakemake for these is risky, because cplex.opt and highres.gms are
inputs to every run_gams job. Touching either makes every existing solve look
stale, and a fresh checkout gives every file a new mtime, which cascades into
the 50 GB input rules.

This script makes exactly the GAMS call workflow/scripts/run_gams.py makes,
with cwd = the existing modelpath, so the run reads the same inputs as the
sweep. Then it converts results.gdx to results.db like the convert_results
rule. On top of that call it can:

  --cplex KEY=VALUE   override CPLEX options for this run only. The run's own
                      option file is the modelpath cplex.opt with the
                      overrides applied, written into the run directory and
                      passed via optDir, so the shared cplex.opt is untouched.
  --cap-zones Z,...   cap var_shed in those zones (needs the shed_cap block
                      in highres.gms, on epsilon_ll since 2026-10-19)
  --label NAME        prefix the run directory, NAME_e{eps}_d{delta}. A label
                      keeps the run out of the "e*_d*" glob that builds the
                      runs table, so special runs cannot be pooled into the
                      sweep by accident. Leave it off for ordinary grid points.

It refuses to run if the target results.gdx already exists.

Examples, from the WF checkout:

  # crossover check
  python workflow/scripts/solve_one.py --config config/config_fox_1year_fine.yaml \\
      --epsilon 0.005 --delta 0.2 --label xover --cplex solutiontype=1 --sbatch

  # grid-gap point on the diagonal, joins the sweep
  python workflow/scripts/solve_one.py --config config/config_fox_1year_fine.yaml \\
      --epsilon 0.0005 --delta 0.0005 --sbatch

  # no shedding allowed in the four heaviest relative shedders
  python workflow/scripts/solve_one.py --config config/config_fox_1year_fine.yaml \\
      --epsilon 0.005 --delta 0.2 --label capEESEIEFI \\
      --cap-zones EE,SE,IE,FI --cap-frac 0 --sbatch

--sbatch writes NAME/submit.sh into the run directory instead of solving, so
the solve happens on a compute node. Submit it with sbatch.
"""

import argparse
import pathlib
import shlex
import subprocess
import sys

import yaml


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, type=pathlib.Path)
    ap.add_argument("--epsilon", required=True, type=float)
    ap.add_argument("--delta", required=True, type=float)
    ap.add_argument("--label", default="")
    ap.add_argument("--shed-mode", default=None,
                    help="override the config's shed_mode for this run")
    ap.add_argument("--cplex", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--cap-zones", default=None, metavar="Z1,Z2,...")
    ap.add_argument("--cap-frac", type=float, default=0.0)
    ap.add_argument("--gms", default=None, type=pathlib.Path,
                    help="highres.gms to solve with (default: the config's "
                         "abs_shared_code). Must be a checkout of epsilon_ll: "
                         "the pathways branch has no shed formulation at all.")
    ap.add_argument("--year", default=None,
                    help="weather and demand year (default: the config's only year)")
    ap.add_argument("--dry-run", action="store_true", help="print the GAMS call only")
    ap.add_argument("--sbatch", action="store_true",
                    help="write a SLURM script into the run directory instead of solving")
    ap.add_argument("--account", default="ec85")
    ap.add_argument("--time", default="24:00:00")
    ap.add_argument("--cpus", type=int, default=30)
    ap.add_argument("--mem-per-cpu", type=int, default=4000)
    return ap.parse_args()


def only(cfg_list, name, override=None):
    """The single value of a config list, or the override."""
    if override is not None:
        return str(override)
    vals = [v for v in cfg_list]
    if len(vals) != 1:
        sys.exit(f"config lists {len(vals)} {name}s ({vals}); pass --{name} to pick one")
    return str(vals[0])


def main():
    a = parse_args()
    wf = pathlib.Path.cwd()
    cfg = yaml.safe_load(a.config.read_text())
    paths, p = cfg["paths"], cfg["parameters"]

    year = only(p["years"], "year", a.year)
    spatial = only(p["spatials"], "spatial")
    psys = only(p["power_system_scenario"], "power_system_scenario")
    shed_mode = a.shed_mode or p.get("shed_mode", "off")
    shed_scope = p.get("shed_scope", "system")

    gamspath = str(paths["gams"])
    gms = a.gms or pathlib.Path(paths["abs_shared_code"]) / "highres.gms"
    gms = (wf / gms).resolve()
    if not gms.exists():
        sys.exit(f"no highres.gms at {gms}")
    if "eq_shed_depth" not in gms.read_text():
        sys.exit(f"{gms} has no shed formulation -- is it the pathways branch? "
                 "Point --gms at a checkout of epsilon_ll.")
    if a.cap_zones and "shed_cap_zones" not in gms.read_text():
        sys.exit(f"{gms} predates the shed_cap block; update its epsilon_ll checkout")

    modelpath = (wf / pathlib.Path(paths["results"]) / f"{psys}_{year}_{spatial}").resolve()
    if not (modelpath / "inputs.finished").exists():
        sys.exit(f"{modelpath} has no inputs.finished -- the inputs were never built there")

    # Same naming as the Snakefile's runpath, plus an optional leading label.
    scope_prefix = "" if shed_scope == "system" else shed_scope + "_"
    tag = f"e{float(a.epsilon)}_d{float(a.delta)}"
    name = (a.label + "_" if a.label else "") + scope_prefix + tag
    rundir = modelpath / name
    if (rundir / "results.gdx").exists():
        sys.exit(f"{rundir}/results.gdx already exists; refusing to overwrite a solve")

    if a.sbatch:
        rundir.mkdir(exist_ok=True)
        rest = [x for x in sys.argv[1:] if x != "--sbatch"]
        script = rundir / "submit.sh"
        script.write_text(
            "#!/bin/bash -l\n"
            f"#SBATCH --account={a.account}\n"
            f"#SBATCH --job-name={name}\n"
            f"#SBATCH --time={a.time}\n"
            f"#SBATCH --cpus-per-task={a.cpus}\n"
            f"#SBATCH --mem-per-cpu={a.mem_per_cpu}\n"
            f"#SBATCH --output={rundir}/slurm-%j.out\n"
            "module load Mamba/4.14.0-0\n"
            "source ${EBROOTMAMBA}/bin/activate\n"
            "conda activate highres\n"
            f"cd {shlex.quote(str(wf))}\n"
            f"python workflow/scripts/solve_one.py {shlex.join(rest)}\n"
        )
        print(f"wrote {script}\nsubmit with:  sbatch {script}")
        return

    args = [
        gamspath + "gams",
        str(gms),
        "gdxCompress=1",
        f"o={rundir / 'highres.lst'}",
        "--codefolderpath=" + str(gms.parent) + "/",
        "--psys_scen=" + psys,
        "--co2_target_type=" + str(p["co2_target_type"]),
        "--co2_target_extent=" + str(p["co2_target_extent"]),
        "--weather_yr=" + year,
        "--dem_yr=" + year,
        "--outname=" + f"{name}/results",
        "--store_initial_level=" + str(p["store_initial_level"]),
        "--store_final_level=" + str(p["store_final_level"]),
        "--hydro_res_initial_fill=" + str(p["hydro_res_initial_fill"]),
        "--hydro_res_min=" + str(p["hydro_res_min"]),
        "--pgen=" + str(p["pgen"]),
        "--emis_price=" + str(p["emis_price"]),
        "--trans_inv=" + str(p["trans_inv"]),
        "--trans_cap_lim=" + str(p["trans_cap_lim"]),
        "--UC=" + str(p["unit_commitment"]),
        "--f_res=" + str(p["frequency_response"]),
        "--store_uc=" + str(p["store_uc"]),
        "--EV=" + str(p["EV"]),
        "--EV_flex=" + str(p["EV_flex"]),
        "--V2G=" + str(p["V2G"]),
        "--EV_pcap=" + str(p["EV_pcap"]),
        "--EV_soc_min=" + str(p["EV_soc_min"]),
        "--EV_soc_max=" + str(p["EV_soc_max"]),
        "--epsilon_param=" + str(float(a.epsilon)),
        "--delta_param=" + str(float(a.delta)),
        "--shed_mode=" + shed_mode,
        "--shed_scope=" + shed_scope,
    ]

    if a.cap_zones:
        args += ["--shed_cap_zones=" + a.cap_zones, "--shed_cap_frac=" + str(a.cap_frac)]

    if a.cplex:
        # This run's own option file: the shared cplex.opt, overrides applied.
        opts = {}
        for line in (modelpath / "cplex.opt").read_text().splitlines():
            if line.strip():
                k, v = line.split(maxsplit=1)
                opts[k] = v
        for kv in a.cplex:
            k, v = kv.split("=", 1)
            opts[k] = v
        optdir = rundir / "opt"
        if not a.dry_run:
            optdir.mkdir(parents=True, exist_ok=True)
            (optdir / "cplex.opt").write_text("".join(f"{k} {v}\n" for k, v in opts.items()))
        args.append(f"optDir={optdir}/")

    print(f"run directory : {rundir}")
    print(f"model         : {gms}")
    print("call          : " + shlex.join(args))
    if a.dry_run:
        return

    rundir.mkdir(exist_ok=True)
    log = rundir / "highres.log"
    with open(log, "w") as f:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, cwd=modelpath)
        for line in proc.stdout:
            text = line.decode(errors="replace")
            print(text, end="")
            f.write(text)
        rc = proc.wait()
    if rc != 0 or not (rundir / "results.gdx").exists():
        sys.exit(f"GAMS exited with {rc}; see {log} and {rundir / 'highres.lst'}")

    # Same as the convert_results rule.
    subprocess.run([gamspath + "gamstool", "sqlitewrite",
                    f"gdxIn={rundir / 'results.gdx'}", f"o={rundir / 'results.db'}"],
                   check=True)
    print(f"done: {rundir / 'results.db'}")


if __name__ == "__main__":
    main()
