"""wrapper for the run_robust_loop rule, runs pathway_step.py on one milestone

used instead of run_gams when robust.enabled is on in the config.
writes the final results.gdx to the same place run_gams would, so the ledger
rule doesn't need to change.
the whole milestone runs in one SLURM job, progress is in <milestone>/robust.log
"""

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

# pathway_step.py reads the GAMS settings from this file, one per line
settings_file = work / "gams_settings.txt"
settings_file.write_text("\n".join(gams_settings(p, w)))

cmd = [
    sys.executable, str(pathlib.Path(p.scriptsdir) / "pathway_step.py"),
    "--model-dir", str(horizon),
    "--work", str(work),
    "--zones", ",".join(p.zones),
    "--realisations-file", str(snakemake.input.realisations),
    "--vre-parquet", str(snakemake.input.vre_parquet),
    "--tolerance", str(p.tolerance),
    "--max-iter", str(r.get("max_iter", 5)),
    "--gams", str(p.gamspath),
    "--code", str(p.sharedcodepath),
    "--gams-settings", str(settings_file),
    "--results-to", str(snakemake.output.modelresults),
]

# print pathway_step's output and write it to the log as it comes
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
        f"end of {snakemake.log[0]}. Rerunning starts the milestone again."
    )
