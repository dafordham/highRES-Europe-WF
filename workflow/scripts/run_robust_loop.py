"""Snakemake wrapper for rule run_robust_loop: one milestone through the loop.

Climate-Robust Pathways. Takes the place of run_gams when robust.enabled is
set in the config. Instead of one expansion it runs pathway_step.py: dispatch
the milestone's fleet against every realisation, expand on the worst, repeat
until all clear. The final results.gdx goes where run_gams would have put it,
so the ledger rule and everything after it are unchanged.

The rule is local. With robust.submit = slurm this process only coordinates,
on the login node, and every GAMS solve is its own SLURM job. Snakemake does
not see those jobs, so follow a milestone in robust/loop.log (this rule's log)
or with squeue, where the jobs are named rob_<iteration>_<realisation>.
"""

import json
import pathlib
import subprocess
import sys

from gams_args import gams_settings

p = snakemake.params
w = snakemake.wildcards
r = p.robust

horizon = pathlib.Path(p.horizonpath)
work = horizon / "robust"
work.mkdir(parents=True, exist_ok=True)

settings_file = work / "gams_settings.json"
settings_file.write_text(json.dumps(gams_settings(p, w), indent=1))

cmd = [
    sys.executable, str(pathlib.Path(p.scriptsdir) / "pathway_step.py"),
    "--from-inputs",
    "--model-dir", str(horizon),
    "--work", str(work),
    "--zones", ",".join(p.zones),
    "--realisations-file", str(snakemake.input.realisations),
    "--vre-parquet", str(snakemake.input.vre_parquet),
    "--tolerance", str(r.get("tolerance", 0.05)),
    "--max-iter", str(r.get("max_iter", 5)),
    "--jobs", str(r.get("jobs", 3)),
    "--year", str(w.year),
    "--esys", str(p.energy_system_scenario),
    "--gams", str(p.gamspath),
    "--code", str(p.sharedcodepath),
    "--gams-settings", str(settings_file),
    "--results-to", str(snakemake.output.modelresults),
    "--submit", r.get("submit", "local"),
    "--account", str(r.get("account", "ec85")),
    "--partition", str(r.get("partition", "")),
    "--cpus", str(p.threads),
    "--mem-dispatch", str(r.get("mem_dispatch", "64G")),
    "--mem-expansion", str(r.get("mem_expansion", "80G")),
    "--time-dispatch", str(r.get("time_dispatch", "12:00:00")),
    "--time-expansion", str(r.get("time_expansion", "12:00:00")),
]

with open(snakemake.log[0], "a") as log:
    log.write("\n$ " + " ".join(cmd) + "\n")
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        print(line, end="", flush=True)
        log.write(line)
        log.flush()
    returncode = process.wait()

if returncode != 0:
    raise RuntimeError(
        f"the robust loop stopped with code {returncode}. The reason is at the "
        f"end of {snakemake.log[0]}. Rerunning the same command resumes it."
    )
