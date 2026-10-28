"""GAMS settings for a highRES solve, from the rule's params

used by both run_gams.py and run_robust_loop.py so they solve with the same
scenario settings. ens_obj, fix_fleet and fleet_floor are added by each script
"""


def gams_settings(params, wildcards):
    return [
        "gdxCompress=1",
        "--codefolderpath=" + str(params.sharedcodepath),
        "--psys_scen=" + str(wildcards.psys_scenario),
        "--esys_scen=" + str(params.energy_system_scenario),
        # May bring this back at some stage but commented for now
        #"--co2intensity=" + str(params.co2intensity),
        "--co2_target_type=" + str(params.co2_target_type),
        "--co2_target_extent=" + str(params.co2_target_extent),
        "--weather_yr=" + str(wildcards.year),
        "--dem_yr=" + str(wildcards.year),
        "--outname=" + params.outname,
        "--store_initial_level=" + str(params.store_initial_level),
        "--store_final_level=" + str(params.store_final_level),
        "--hydro_res_initial_fill=" + str(params.hydro_res_initial_fill),
        "--hydro_res_min=" + str(params.hydro_res_min),
        "--pgen=" + str(params.pgen),
        "--emis_price=" + str(params.emis_price),
        "--trans_inv=" + str(params.trans_inv),
        "--trans_cap_lim=" + str(params.trans_cap_lim),
        "--UC=" + str(params.unit_commitment),
        "--f_res=" + str(params.frequency_response),
        "--store_uc=" + str(params.store_uc),
        "--EV=" + params.EV,
        "--EV_flex=" + str(params.EV_flex),
        "--V2G=" + params.V2G,
        "--EV_soc_max=" + str(params.EV_soc_max),
    ]
