import subprocess

# TODO - test on linux

args = [
    snakemake.params.gamspath + "gams",
    snakemake.params.sharedcodepath / "highres.gms",
    "gdxCompress=1",
    "--codefolderpath=" + str(snakemake.params.sharedcodepath),
    "--psys_scen=" + str(snakemake.wildcards.psys_scenario),
    # May bring this back at some stage but commented for now
    #"--co2intensity=" + str(snakemake.params.co2intensity),
    "--co2_target_type=" + str(snakemake.params.co2_target_type),
    "--co2_target_extent=" + str(snakemake.params.co2_target_extent),
    "--weather_yr=" + str(snakemake.wildcards.year),
    "--dem_yr=" + str(snakemake.wildcards.year),
    "--outname=" + snakemake.params.outname,
    "--store_initial_level=" + str(snakemake.params.store_initial_level),
    "--store_final_level=" + str(snakemake.params.store_final_level),
    "--hydro_res_initial_fill=" + str(snakemake.params.hydro_res_initial_fill),
    "--hydro_res_min=" + str(snakemake.params.hydro_res_min),
    "--pgen=" + str(snakemake.params.pgen),
    "--emis_price=" + str(snakemake.params.emis_price),
    "--trans_inv=" + str(snakemake.params.trans_inv),
    "--trans_cap_lim=" + str(snakemake.params.trans_cap_lim),
    # Climate-Robust Pathways
    "--ens_obj=" + str(snakemake.params.ens_obj),
    "--delta=" + str(snakemake.params.delta),
    "--UC=" + str(snakemake.params.unit_commitment),
    "--f_res=" + str(snakemake.params.frequency_response),
    "--store_uc=" + str(snakemake.params.store_uc),
    "--EV=" + snakemake.params.EV,
    "--EV_flex=" + str(snakemake.params.EV_flex),
    "--V2G=" + snakemake.params.V2G,
    "--EV_pcap=" + str(snakemake.params.EV_pcap),
    "--EV_soc_min="  + str(snakemake.params.EV_soc_min),
    "--EV_soc_max="  + str(snakemake.params.EV_soc_max),
]

# fix_fleet names a .dd holding a solved fleet; empty means capacity stays free,
# as in a stock expansion run. It is appended only when set, because GAMS parses
# "--key=" with an empty value by swallowing the NEXT argument as the value --
# "--fix_fleet=" followed by "--ens_obj=OFF" silently set fix_fleet to the string
# "--ens_obj=OFF", after which the model tried to $INCLUDE a file by that name
# and failed with 19 errors whose first line was "Unable to open include file".
fix_fleet = str(snakemake.params.fix_fleet).strip()
if fix_fleet:
    args.append("--fix_fleet=" + fix_fleet)

process = subprocess.Popen(
    args,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    cwd=snakemake.params.modelpath,
)

# Below writes gams output to terminal in realtime and log

with open(snakemake.log[0], "w") as f:
    while True:
        line = process.stdout.readline()

        if not line and process.poll() is not None:
            break
        print(line.decode(), end="")
        f.write(line.decode())  # keep newlines, or the log is one run-on line

# GAMS errors (licensing, compilation, execution) do not raise on their own:
# they just leave results.gdx missing, which snakemake reports as an opaque
# "message: None". Fail here with the end of the listing so the real cause is
# in the first log anyone reads. Note an infeasible model still exits 0 --
# model status is separate from the process exit code -- so this only trips
# on genuine failures.
returncode = process.wait()
if returncode != 0:
    with open(snakemake.log[0]) as f:
        tail = f.read()[-3000:]
    raise RuntimeError(
        f"GAMS exited with code {returncode}.\n"
        f"--- end of {snakemake.log[0]} ---\n{tail}"
    )
