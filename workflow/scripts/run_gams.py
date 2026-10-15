import subprocess

from gams_args import gams_settings

# TODO - test on linux

# The settings live in gams_args.py, shared with run_robust_loop.py, so a
# milestone solved by the robust loop uses exactly the settings of this rule.
args = [
    snakemake.params.gamspath + "gams",
    snakemake.params.sharedcodepath / "highres.gms",
    *gams_settings(snakemake.params, snakemake.wildcards),
    # Climate-Robust Pathways
    "--ens_obj=" + str(snakemake.params.ens_obj),
]

# fix_fleet names a .dd holding a solved fleet. Empty means capacity stays free,
# as in a stock expansion run. It is appended only when set, so the default is a stock expansion run and configs that predate this switch keep working unchanged.
fix_fleet = str(snakemake.params.fix_fleet).strip()
if fix_fleet:
    args.append("--fix_fleet=" + fix_fleet)

process = subprocess.Popen(
    args,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    cwd=snakemake.params.horizonpath,
)

# Below writes gams output to terminal in realtime and log

with open(snakemake.log[1], "w") as f:
    while True:
        line = process.stdout.readline()

        if not line and process.poll() is not None:
            break
        print(line.decode(), end="")
        f.write(line.decode())  # keep newlines, or the log is one run-on line

# GAMS errors (licensing, compilation, execution) do not raise on their own:
# they just leave results.gdx missing, which snakemake reports as an opaque
# "message: None". Fail here with the end of the log so the real cause is in
# the first log anyone reads.
returncode = process.wait()
if returncode != 0:
    with open(snakemake.log[1]) as f:
        tail = f.read()[-3000:]
    raise RuntimeError(
        f"GAMS exited with code {returncode}.\n"
        f"--- end of {snakemake.log[1]} ---\n{tail}"
    )
