import calendar
import datetime
import pathlib

import pandas as pd

from data2dd_funcs import (euro_demand2dd, scen2dd, temporal2dd, trans_links,
                           co2target2dd, add_vre_connection_costs, apply_capacity_retirement,
                           apply_transmission_carryforward)
                           

root = pathlib.Path(snakemake.output[1]).parent
data_root = root
out = pathlib.Path(".")


co2target2dd(
    snakemake.input.co2_targets_db,
    snakemake.output.co2_target,
    # snakemake.params.energy_system_scenario,
    snakemake.wildcards.emission_scenario,
    snakemake.params.co2_target_type,
    snakemake.params.co2_target_extent,
    snakemake.params.planning_horizon)

psys_scen = snakemake.wildcards.psys_scenario
esys_scen = snakemake.params.energy_system_scenario
planning_horizon = snakemake.params.planning_horizon
growth_seed = snakemake.params.growth_seed

scen_db = snakemake.input[1]
f_techno = snakemake.input[2]

params_to_write = {}

params_to_write["gen"] = {}
params_to_write["gen"]["set"] = [
    "g",
    "non_vre",
    "vre",
    "hydro_res",
    "uc_int",
    "uc_lin",
    "quick"
]
params_to_write["gen"]["parameter"] = {}
params_to_write["gen"]["parameter"]["all"] = [
    "emis fac",
    "max ramp",
    "min down",
    "min up",
    "startup cost",
    "inertia",
    "unit size",
    "capex",
    "varom",
    "fom",
    "fuelcost",
    "cap2area",
    "af",
    "min gen",
    "peak af",
    "lifetime",
]

params_to_write["store"] = {}
params_to_write["store"]["set"] = ["s", "uc_lin","quick"]
params_to_write["store"]["parameter"] = {}
params_to_write["store"]["parameter"]["all"] = [
    "max_freq",
    "max_res",
    "eff_in",
    "eff_out",
    "loss_per_hr",
    "p_to_e",
    "varom",
    "e capex",
    "p capex",
    "fom",
    "af",
    "min down",
    "min up",
    "startup cost",
    "inertia",
    "unit size",
    "min gen",
    "max ramp",
    "e lifetime",
    "p lifetime",
]

zones = pd.read_csv(snakemake.input[0]).loc[:, "zone"]

scen2dd(
    snakemake.output[1],
    root,
    f_techno,
    params_to_write,
    scen_db,
    psys_scen,
    esys_scen,
    zones,
    out=out,
    esys_cap=False,
    exist_cap=True,
    exist_agg=snakemake.wildcards.spatial,
    planning_horizon=planning_horizon,
)

trans_links(
    root,
    f_techno,
    aggregated_regions=snakemake.params.aggregated_regions,
    planning_horizon=planning_horizon,
    out=out,
)

add_vre_connection_costs(
    root,
    out,
    f_techno,
    psys_scen,
    snakemake.input.vre_connection_dists
    )
    

years = [snakemake.wildcards.year]
date_range = snakemake.params.date_range

for yr in years:
    date_range = [yr + "-" + date for date in date_range]
    yr = int(yr)

    dstart = datetime.datetime.fromisoformat(date_range[0])
    dstop = datetime.datetime.fromisoformat(date_range[1]) + datetime.timedelta(
        hours=23
    )

    if calendar.isleap(yr):
        rleap = False
    else:
        rleap = True

    print(rleap)
    temporal2dd(dstart, dstop, root / out, snakemake.output[0])

    euro_demand2dd(
        snakemake.input["europedemandcsvlocation"],
        snakemake.params.aggregated_regions,
        root,
        root / out,
        dstart,
        dstop,
        scen_db,
        esys_scen,
        yr,
    )

if snakemake.input.prior_ledger:
    prior_horizon = pathlib.Path(str(snakemake.input.prior_ledger)).parent.name.split("_")[0]
else:
    prior_horizon = None

apply_transmission_carryforward(
    snakemake.output[3], snakemake.input.prior_ledger, f_techno, int(planning_horizon),
)

apply_capacity_retirement(
    snakemake.output[1], snakemake.output[2], snakemake.input.prior_ledger, f_techno,
    int(planning_horizon), snakemake.wildcards.spatial,
    snakemake.input.gen_database, zones, snakemake.params.baseline_year, prior_horizon,
    scen_db, psys_scen, growth_seed,growth_scen=snakemake.wildcards.growth_scenario,
)