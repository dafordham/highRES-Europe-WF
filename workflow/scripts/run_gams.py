import subprocess

from gams_args import gams_settings

# TODO - test on linux

# [cl] The settings live in gams_args.py, shared with run_robust_loop.py, so a
# milestone solved by the robust loop uses exactly the settings of this rule.
args = [
    snakemake.params.gamspath + "gams",
    snakemake.params.sharedcodepath / "highres.gms",
    *gams_settings(snakemake.params, snakemake.wildcards),
]

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
        f.write(line.decode())  # [cl] keep newlines, or the log is one run-on line

# [cl] GAMS errors (licensing, compilation, execution) do not raise on their own:
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
