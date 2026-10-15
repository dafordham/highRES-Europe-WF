import calendar
import datetime
import itertools
import re
import warnings

import numpy as np
import pandas as pd
import pathlib


def wrapdd(d, out_par, otype, outfile=""):
    d = np.atleast_2d(d)

    if otype == "set":
        top = np.array([["set"], [out_par + " /"]])
        bottom = np.array([["/"], [""]])

    if otype == "scalar":
        top = np.array([["scalar"], [out_par + " /"]])
        bottom = np.array([["/"], [""]])

    if otype == "parameter":
        top = np.array([["parameter", ""], [out_par + " /", ""]])
        bottom = np.array([["/", ""], ["", ""]])

    d = np.concatenate((top, d, bottom), axis=0)

    if outfile != "":
        np.savetxt(outfile, d, delimiter=" ", fmt="%s")
    else:
        return d


def data2dd(data, sets, outfile="", all_combin=False, rounddp=8):
    # rounddp=5

    # each set in sets needs to be 1D at the moment

    # sets and data must be in correct order -> last set must be column
    # headers, previous sets are rows

    if type(data) != "numpy.ndarray":
        data = np.array(data)
    sets = np.array(sets, dtype="object")

    if len(sets) > 0:
        data = np.round(data.astype(float), rounddp)
        if len(sets) == 1:
            sets_out = sets[0]
            sets_new = np.array(sets_out).astype(str)

            data_out = np.hstack((sets_new.reshape(-1, 1), data.reshape(-1, 1)))
        else:
            if all_combin:
                sets_out = [item for item in list(itertools.product(*sets))]

                sets_out = np.char.array(sets_out)

                sets_new = sets_out[:, 0]

                for i in range(sets_out.shape[1] - 1):
                    sets_new = sets_new + "." + sets_out[:, i + 1]

                data_out = np.hstack((sets_new.reshape(-1, 1), data.reshape(-1, 1)))

            else:
                lens = np.array([item.shape[0] for item in sets])

                sets_new = []
                for i, s in enumerate(sets):
                    if lens[i] != lens.max():
                        sets_new.append(np.repeat(s, lens.max()))
                    else:
                        sets_new.append(s)

                sets_new = np.array(
                    [".".join(item) for item in np.char.array(list(zip(*sets_new)))]
                )

                data_out = np.hstack((sets_new.reshape(-1, 1), data.reshape(-1, 1)))

    else:
        data_out = data.reshape(-1, 1)
    if outfile != "":
        np.savetxt(outfile, data_out, fmt="%s", delimiter=" ")
    else:
        return data_out


def temporal2dd(dstart, dend, opath, temporaloutputpath):
    # nyears=(dend.year-dstart.year)+1
    years = np.arange(dstart.year, dend.year + 1)
    ntime = ((dend - dstart).total_seconds() / 3600) + 1

    hr2yr = []
    for nyr, yr in enumerate(years):
        shour = ((datetime.datetime(yr, 1, 1, 0) - dstart).total_seconds() / 3600) + 1
        if dend.year == yr:
            ehour = ((dend - dstart).total_seconds() / 3600) + 1
        else:
            ehour = (
                (datetime.datetime(yr, 12, 31, 23) - dstart).total_seconds() / 3600
            ) + 1

        hrs = np.arange(shour - 1, ehour).astype(int)
        hr2yr.append(list(zip(np.repeat(nyr, hrs.shape[0]).astype(int), hrs)))

    hr2yr = np.char.array(np.vstack(hr2yr).astype(str))

    hr2yr = (hr2yr[:, 0] + "." + hr2yr[:, 1]).reshape(-1, 1)

    if dstart.year != dend.year:
        yr = str(dstart.year) + "-" + str(dend.year)
    else:
        yr = str(dstart.year)

    out = []

    out.append(wrapdd(np.arange(ntime).reshape(-1, 1).astype(int), "h", "set"))
    out.append(
        wrapdd(np.arange(years.shape[0]).reshape(-1, 1).astype(int), "yr", "set")
    )
    out.append(wrapdd(hr2yr, "hr2yr_map", "set"))

    np.savetxt(temporaloutputpath, np.concatenate(out, axis=0), fmt="%s")


def trans_links(root, f, aggregated_regions, planning_horizon, out="work"):
    tech_type = "trans"

    links_allowed = pd.read_excel(
        f, sheet_name="transmission_allowed", skiprows=1, engine="calamine"
    ).query("Zone1 == @aggregated_regions and Zone2 == @aggregated_regions")

    links_allowed = (
        links_allowed.loc[links_allowed["Installed year"] <= int(planning_horizon), :]
        .sort_values("Installed year")
        .drop_duplicates(subset=["Zone1", "Zone2", "Tech"], keep="last")
    )    

    links_out = np.array(
        links_allowed["Zone1"]
        + "."
        + links_allowed["Zone2"]
        + "."
        + links_allowed["Tech"]
    )

    links_tech = pd.unique(links_allowed["Tech"])

    set_outdd = []
    param_outdd = []

    set_outdd.append(wrapdd(data2dd(links_tech, []), "trans", "set"))
    set_outdd.append(wrapdd(data2dd(links_out, []), "trans_links", "set"))

    # data2dd(links_out,[],outfile=root/out/(tech_type+"_links.dd"))

    out_data = ["links_dist", "links_cap","links_lim_cap"]

    for od in out_data:
        out_par = tech_type + "_" + od

        param_outdd.append(
            wrapdd(data2dd(links_allowed[od].values, [links_out]), out_par, "parameter")
        )

    params = pd.read_excel(f, sheet_name="transmission", skiprows=1, engine="calamine")

    nans = params.isnull()
    params = params.where(~nans, other=0.0)

    out_data = ["loss", "line_capex", "sub_capex", "varom"]

    #    trans_set=params["Technology Name (highRES)"]

    params = params[params["Technology Name (highRES)"].isin(links_tech)]

    for od in out_data:
        out_par = tech_type + "_" + od

        param_outdd.append(
            wrapdd(
                data2dd(params[od], [params["Technology Name (highRES)"]]),
                out_par,
                "parameter",
            )
        )

    param_outdd = np.concatenate(param_outdd, axis=0)
    set_outdd = np.concatenate(set_outdd, axis=0)

    pad = np.repeat(np.array(""), set_outdd.shape[0]).reshape(set_outdd.shape[0], 1)

    outdd = np.concatenate((np.hstack((set_outdd, pad)), param_outdd), axis=0)

    #    outdd=np.concatenate(outdd,axis=0)

    np.savetxt(root / out / (tech_type + ".dd"), outdd, delimiter=" ", fmt="%s")
    
def add_vre_connection_costs(
            root,
            out,
            f_techno,
            psys_scen,
            vre_connection_dists
            ):
    
    dists=pd.read_csv(vre_connection_dists,index_col=0)
    
    capex=pd.read_excel(f_techno, 
                             sheet_name="transmission", 
                             skiprows=1, 
                             engine="calamine")
    
    conn_costs=(dists.div(100)
            .mul(capex.loc[capex["Technology Name (highRES)"]=="HVDC_Windoffshore","line_capex"].values[0]))
    
    conn_costs.loc[(dists["dist"]>0.0),"dist"]+=capex.loc[capex["Technology Name (highRES)"]=="HVDC_Windoffshore","sub_capex"].values[0]
    
    conn_costs=pd.DataFrame(wrapdd(conn_costs.round(1).reset_index().values,
                 "gen_vreconnection","parameter"))
    
    conn_costs["out"]=conn_costs[0]+" "+conn_costs[1].astype(str)
    
    d=pd.read_csv(root / out / (psys_scen+"_gen.dd"),skip_blank_lines=False,header=None)
    
    d_out=np.concatenate(
        (d.values,
        conn_costs.loc[:,["out"]].values),
        axis=0)
    
    np.savetxt(
            root / out / (psys_scen+"_gen.dd"), d_out, delimiter=" ", fmt="%s"
        )

def co2target2dd(co2targets_db, 
                 co2target_out, 
                 esys_scen, 
                 co2_target_type,
                 co2_target_extent,
                 planning_horizon):
    
    d = pd.read_csv(co2targets_db)
    d["year"] = d["year"].astype(str).str.strip()
    dout = (d
            .query("(case == @esys_scen) \
                   and (type == @co2_target_type) \
                   and (extent == @co2_target_extent) \
                   and (year == @planning_horizon)"))

                   
    if co2_target_extent == "all":
        wrapdd(dout["target"].values[0], "co2_target", "scalar", outfile=co2target_out)
    if co2_target_extent == "zonal":
        wrapdd(data2dd(dout["target"].values,
                       [dout["zone"].values])
               ,"co2_target", "parameter", outfile=co2target_out)
        

def getzlims(lim, techs, zones, planning_horizon):
    lim = lim.loc[(lim["Year"] <= int(planning_horizon)) & (lim["Technology"].isin(techs)), :]

    if lim.empty:
        return np.array([])

    lim = (
        lim.sort_values("Year")
        .drop_duplicates(subset=["Technology", "parameter", "limtype"], keep="last")
    ) #removing duplicates and keeping only the latest year limits

    # FX and LO are mutually exclusive (GAMS applies .UP, then .LO, then .FX,
    # unconditionally -- .FX always wins if present, erasing whatever .LO set).
    # Whichever of the two was authored at the more recent Year should take
    # effect, regardless of which one it is. UP is untouched -- it never
    # conflicts with either.
    key_cols = ["Technology", "parameter"]
    fx_lo_mask = lim["limtype"].isin(["FX", "LO"])
    resolved = lim.loc[fx_lo_mask, :].sort_values("Year").drop_duplicates(subset=key_cols, keep="last")
    lim = pd.concat([lim.loc[~fx_lo_mask, :], resolved], ignore_index=True)   

    have_lim = ~lim.loc[:, "limtype"].isnull()

    if (~have_lim).any():
        warnings.warn(
            "Warning, missing zonal new capacity limits for:"
            + ", ".join(lim.loc[~have_lim, "Technology"])
        )

    lim = lim.loc[have_lim, :]
    para_lim = lim["parameter"].drop_duplicates()

    outlims = []

    for p_lim in para_lim:
        nl = []
        for _, row in lim.loc[lim["parameter"] == p_lim, :].iterrows():
            zones = row[zones].index.values
            limval = row[zones].values.astype(float) / 1e3
            limval[np.isnan(limval)] = 0.0

            tech = np.atleast_1d(row["Technology"])
            limtype = np.atleast_1d(row["limtype"])

            nl.append(data2dd(limval.T, [zones, tech, limtype]))

        outlims.append(wrapdd(np.concatenate(nl, axis=0), p_lim, "parameter"))

    return np.concatenate(outlims, axis=0)


def getrlims(lim, techs, zones, exist_agg, planning_horizon):
    # TODO capcity units are fixed to GW here, need to add flexibility

    lim = lim.loc[
        (lim["Year"] <= int(planning_horizon))
        & (lim["Technology"].isin(techs))
        & (lim["zone"].isin(zones)),
        :,
    ]

    if lim.empty:
        return np.array([])

    lim = (
        lim.sort_values("Year")
        .drop_duplicates(subset=["Technology", "zone", "region", "parameter", "limtype"], keep="last")
    ) # removing duplicates and keeping only the latest year limits

    # LO versus FX
    key_cols = ["Technology", "zone", "region", "parameter"]
    fx_lo_mask = lim["limtype"].isin(["FX", "LO"])
    resolved = lim.loc[fx_lo_mask, :].sort_values("Year").drop_duplicates(subset=key_cols, keep="last")
    lim = pd.concat([lim.loc[~fx_lo_mask, :], resolved], ignore_index=True)

    have_lim = ~lim.loc[:, "limtype"].isnull()
    lim = lim.loc[have_lim, :]
    para_lim = lim["parameter"].drop_duplicates()

    outlims = []

    for p_lim in para_lim:
        limval = lim.loc[(lim["parameter"] == p_lim), :]

        if exist_agg == "nuts2":
            nl = data2dd(
                limval["value"] / 1e3,
                [
                    limval["Technology"],
                    limval["zone"],
                    limval["region"],
                    limval["limtype"],
                ],
            )

        if exist_agg == "region":
            #limval = limval.groupby(["zone"], as_index=False).agg(
            # to consider the other technologies
            limval = limval.groupby(["Technology","zone"], as_index=False).agg(
                {
                    #"Technology": "first", # removed because is already in groupby
                    "Year": "first",
                    "parameter": "first",
                    "limtype": "first",
                    "value": "sum",
                }
            )

            nl = data2dd(
                limval["value"] / 1e3,
                [
                    limval["Technology"],
                    limval["zone"],
                    limval["zone"],
                    limval["limtype"],
                ],
            )

        outlims.append(wrapdd(nl, p_lim, "parameter"))

    return np.concatenate(outlims, axis=0)


def read_dd_blocks(f):
    lines = pathlib.Path(f).read_text().splitlines()
    blocks, order = {}, []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].strip()
        if line in ("parameter", "set", "scalar"):
            block_type = line
            i += 1
            name = lines[i].strip().split("/")[0].strip()
            i += 1
            rows = []
            while i < n and lines[i].strip() != "/":
                parts = lines[i].split()
                if len(parts) == 2:
                    rows.append((parts[0], parts[1]))
                elif len(parts) == 1:
                    rows.append((parts[0], None))
                i += 1
            i += 1
            blocks[name] = {"type": block_type, "rows": rows}
            order.append(name)
        else:
            i += 1
    return blocks, order

def write_dd_blocks(blocks, order, outfile):
    lines = []
    for name in order:
        block = blocks[name]
        lines.append(block["type"])
        lines.append(name + " /")
        for key, value in block["rows"]:
            lines.append(key if value is None else f"{key} {value}")
        lines.append("/")
    pathlib.Path(outfile).write_text("\n".join(lines) + "\n")


def patch_exist_block(blocks, block_name, ledger_slice, key_cols,  covered_techs):
    original_rows = blocks[block_name]["rows"]
    tech_pos = key_cols.index("Technology")
    limtype_lookup = {}
    for key, _ in original_rows:
        parts = key.split(".")
        limtype_lookup[parts[tech_pos]] = parts[-1]

    rows = dict(original_rows)
    # gen_database.csv is considered only for covered_techs 
    # Every other psys-scenario tech is left untouched unless the get built in previous horizon.
    for key in list(rows):
        if key.split(".")[tech_pos] in covered_techs:
            del rows[key]

    if not ledger_slice.empty:
        agg = ledger_slice.groupby(key_cols, as_index=False)["capacity_mw"].sum()
        for _, row in agg.iterrows():
            key_parts = [str(row[c]) for c in key_cols]
            limtype = limtype_lookup.get(row["Technology"], "FX")
            prefix = ".".join(key_parts) + "."
            for stale_key in [k for k in rows if k.startswith(prefix)]:
                del rows[stale_key]
            rows[prefix + limtype] = str(row["capacity_mw"] / 1000)

    blocks[block_name]["rows"] = list(rows.items())

def patch_trans_cap_block(blocks, block_name, ledger_slice, key_cols, covered_keys):
    rows = dict(blocks[block_name]["rows"])

    for key in list(rows):
        if tuple(key.split(".")) in covered_keys:
            del rows[key]
    
    if not ledger_slice.empty:
        agg = ledger_slice.groupby(key_cols, as_index=False)["capacity_mw"].sum()
        for _, row in agg.iterrows():
            key = ".".join(str(row[c]) for c in key_cols)
            rows[key] = str(row["capacity_mw"])

    blocks[block_name]["rows"] = list(rows.items())

def patch_growth_rate_ceiling(
    blocks, lim_block_name, growth_block_name, existing_z, seed_lookup,
    delta_t, vre_techs,growth_scen=None,
):

    rate_lookup = {}
    if growth_block_name in blocks:
        for key, value in blocks[growth_block_name]["rows"]:
            # zone, tech, _limtype = key.split(".")
            zone, tech, scen_growth = key.split(".")
            if scen_growth == growth_scen: # only get the correct growth rate scenario
                rate_lookup[(zone, tech)] = float(value)*1000  # undo getzlims() MW->GW /1e3,

    existing_lookup = existing_z.groupby(["zone", "Technology"])["capacity_mw"].sum().to_dict() if not existing_z.empty else {}

    rows = dict(blocks[lim_block_name]["rows"])

    # Absolute capacity UPper limit coming from ODS sheet is an inviolable outer ceiling
    static_up_lookup, fx_keys = {}, set()
    for key, value in rows.items():
        zone, tech, limtype = key.split(".")
        if limtype == "UP":
            static_up_lookup[(zone, tech)] = float(value)
        elif limtype == "FX":
            fx_keys.add((zone, tech))

    curtailment = {}

    for (zone, tech) in (set(static_up_lookup) | set(rate_lookup)):
        static_up = static_up_lookup.get((zone, tech), float("inf"))
        if (zone, tech) in fx_keys:
            continue        #if technology has not build freedom and defined with FX.

        existing_mw = existing_lookup.get((zone, tech), 0.0)
        rate = rate_lookup.get((zone, tech))

        if rate is not None:
            seed_mw = seed_lookup.get(tech, 0.0)
            growth_mw = max(seed_mw, (rate ** delta_t) * existing_mw)
            computed_up = min(growth_mw / 1000, static_up)
        else:
            computed_up = static_up

        if existing_mw / 1000 > computed_up:
            if tech in vre_techs:
                # Region-level techs can't have existing curtailed without
                # breaking eq_exist_vre_pcap_z's z=sum(r) equality.
                rows[f"{zone}.{tech}.UP"] = str(existing_mw / 1000)
            else:
                # existing down to the ceiling instead of relaxing it.
                curtailment[(zone, tech)] = computed_up
        elif rate is not None:
            rows[f"{zone}.{tech}.UP"] = str(computed_up)    

    blocks[lim_block_name]["rows"] = list(rows.items())
    return curtailment

def apply_capacity_curtailment(df, curtailment):
    if not curtailment or df.empty:
        return df
    df = df.copy()
    for (zone, tech), capped_gw in curtailment.items():
        mask = (df["zone"] == zone) & (df["Technology"] == tech)
        total_mw = df.loc[mask, "capacity_mw"].sum()
        to_remove = total_mw - capped_gw * 1000
        if to_remove <= 0:
            continue
        for idx in df.loc[mask, :].sort_values("installed_year").index:
            if to_remove <= 0:
                break
            cut = min(df.loc[idx, "capacity_mw"], to_remove)
            df.loc[idx, "capacity_mw"] -= cut
            to_remove -= cut
    return df


def apply_capacity_retirement(
    gen_ddfile, store_ddfile, ledger_path, f_techno, planning_horizon, spatials,
    gen_database_path, zones, baseline_year, prior_horizon, scen_db, psys_scen, growth_seed,
    growth_scen=None,
):

    scen = pd.read_excel(scen_db, sheet_name="scenario_tech_definition", skiprows=0)
    scen = scen.loc[scen["Psys Scenario"] == psys_scen, :]
    techs = scen["Technology Name (highRES)"]

    gen_sheet = pd.read_excel(f_techno, sheet_name="gen", skiprows=1, engine="calamine")
    gen_techs = set(gen_sheet["Technology Name (highRES)"])
    vre_techs = list(gen_sheet.loc[gen_sheet["set"] == "vre", "Technology Name (highRES)"])
    store_techs = set(pd.read_excel(f_techno, sheet_name="store", skiprows=1, engine="calamine")["Technology Name (highRES)"])

    gen_database_techs = set(pd.read_csv(gen_database_path)["Technology"].unique())

    if ledger_path:
        ledger = pd.read_csv(ledger_path)
        survivors = ledger.loc[ledger["installed_year"] + ledger["lifetime"] > planning_horizon, :]
        patch_store = True
        delta_t = int(planning_horizon) - int(prior_horizon)
    else:
        # gen_database.csv has no storeage data.
        ledger = pd.DataFrame(columns=["Technology"])
        seed_all = read_gen_database(gen_database_path, zones, vre_techs, None, baseline_year)
        patch_store = False
        delta_t = int(planning_horizon) - int(baseline_year)
        survivors = seed_all.loc[seed_all["decommissioning_year"] > int(planning_horizon), :].rename(
            columns={"commissioning_year": "installed_year"}
        )

    survivors = survivors.loc[survivors["Technology"].isin(techs), :]

    covered_techs = gen_database_techs | set(ledger["Technology"].unique())

    gen_pcap = survivors.loc[(survivors["capacity_type"] == "pcap") & survivors["Technology"].isin(gen_techs), :]
    gen_pcap_z_direct = gen_pcap.loc[gen_pcap["region"].isna(), :]
    gen_pcap_r_native = gen_pcap.loc[gen_pcap["region"].notna(), :]
    gen_pcap_z_from_r = gen_pcap_r_native.groupby(["zone", "Technology"], as_index=False)["capacity_mw"].sum()
    gen_pcap_z = pd.concat([gen_pcap_z_direct, gen_pcap_z_from_r], ignore_index=True)

    if spatials == "region":
        gen_pcap_r = gen_pcap_r_native.groupby(["Technology", "zone"], as_index=False)["capacity_mw"].sum()
        gen_pcap_r["region"] = gen_pcap_r["zone"]
    else:
        gen_pcap_r = gen_pcap_r_native
    gen_ecap_z = survivors.loc[
        (survivors["capacity_type"] == "ecap") & survivors["Technology"].isin(gen_techs) & survivors["region"].isna(), :
    ]

    gen_blocks, gen_order = read_dd_blocks(gen_ddfile)

    pcap_curtailment = patch_growth_rate_ceiling(gen_blocks, "gen_lim_pcap_z", "gen_growth_pcap_z", gen_pcap_z,
                                                 growth_seed, delta_t, vre_techs,growth_scen)
    ecap_curtailment = patch_growth_rate_ceiling(gen_blocks, "gen_lim_ecap_z", "gen_growth_ecap_z", gen_ecap_z,
                                                  growth_seed, delta_t, vre_techs,growth_scen)

    gen_pcap_z = apply_capacity_curtailment(gen_pcap_z, pcap_curtailment)
    gen_ecap_z = apply_capacity_curtailment(gen_ecap_z, ecap_curtailment)

    patch_exist_block(gen_blocks, "gen_exist_pcap_z", gen_pcap_z, ["zone", "Technology"], covered_techs)
    patch_exist_block(gen_blocks, "gen_exist_ecap_z", gen_ecap_z, ["zone", "Technology"], covered_techs)
    patch_exist_block(gen_blocks, "gen_exist_pcap_r", gen_pcap_r, ["Technology", "zone", "region"], covered_techs)

    year_lo = int(prior_horizon) if prior_horizon else baseline_year
    mandate = read_gen_database(gen_database_path, zones, vre_techs, year_lo, planning_horizon)
    mandate = mandate.drop(columns=["commissioning_year", "decommissioning_year"])
    mandate = mandate.loc[mandate["Technology"].isin(techs), :]

    mandate_pcap = mandate.loc[mandate["capacity_type"] == "pcap", :]
    mandate_pcap_direct = mandate_pcap.loc[mandate_pcap["region"].isna(), :]
    # region is discarded for any UP/LO gen_lim_pcap_z, as it only operated at zone level
    mandate_pcap_from_r = mandate_pcap.loc[mandate_pcap["region"].notna(), :].groupby(
        ["zone", "Technology"], as_index=False
    )["capacity_mw"].sum()
    mandate_pcap_z = pd.concat([mandate_pcap_direct, mandate_pcap_from_r], ignore_index=True)
    patch_gen_database_mandate(gen_blocks, "gen_lim_pcap_z", gen_pcap_z, mandate_pcap_z)

    # There is no ecap planned capacities in gen database but for future use, if any
    mandate_ecap = mandate.loc[mandate["capacity_type"] == "ecap", :]
    mandate_ecap_direct = mandate_ecap.loc[mandate_ecap["region"].isna(), :]
    mandate_ecap_from_r = mandate_ecap.loc[mandate_ecap["region"].notna(), :].groupby(
        ["zone", "Technology"], as_index=False
    )["capacity_mw"].sum()
    mandate_ecap_z = pd.concat([mandate_ecap_direct, mandate_ecap_from_r], ignore_index=True)
    patch_gen_database_mandate(gen_blocks, "gen_lim_ecap_z", gen_ecap_z, mandate_ecap_z)

    write_dd_blocks(gen_blocks, gen_order, gen_ddfile)

    if patch_store:
        store_pcap_z = survivors.loc[(survivors["capacity_type"] == "pcap") & survivors["Technology"].isin(store_techs), :]
        store_ecap_z = survivors.loc[(survivors["capacity_type"] == "ecap") & survivors["Technology"].isin(store_techs), :]

        store_blocks, store_order = read_dd_blocks(store_ddfile)

        store_pcap_curtailment = patch_growth_rate_ceiling(store_blocks, "store_lim_pcap_z","store_growth_pcap_z",
                                                           store_pcap_z, growth_seed, delta_t, vre_techs)
        store_ecap_curtailment = patch_growth_rate_ceiling(store_blocks, "store_lim_ecap_z", "store_growth_ecap_z",
                                                           store_ecap_z, growth_seed, delta_t, vre_techs)

        store_pcap_z = apply_capacity_curtailment(store_pcap_z, store_pcap_curtailment)
        store_ecap_z = apply_capacity_curtailment(store_ecap_z, store_ecap_curtailment)

        patch_exist_block(store_blocks, "store_exist_pcap_z", store_pcap_z, ["zone", "Technology"], covered_techs)
        patch_exist_block(store_blocks, "store_exist_ecap_z", store_ecap_z, ["zone", "Technology"], covered_techs)
        write_dd_blocks(store_blocks, store_order, store_ddfile)


def apply_transmission_carryforward(trans_ddfile, ledger_path, f_techno, planning_horizon):

    trans_techs = set(
        pd.read_excel(f_techno, sheet_name="transmission", skiprows=1, engine="calamine")["Technology Name (highRES)"]
    )
    trans_blocks, trans_order = read_dd_blocks(trans_ddfile)
    if ledger_path:
        ledger = pd.read_csv(ledger_path)
        survivors = ledger.loc[ledger["installed_year"] + ledger["lifetime"] > planning_horizon, :]

        trans_cap = survivors.loc[
            (survivors["capacity_type"] == "pcap") & survivors["Technology"].isin(trans_techs), :
        ]

        covered_keys = set(
            ledger.loc[ledger["Technology"].isin(trans_techs), ["zone", "region", "Technology"]]
            .astype(str)
            .itertuples(index=False, name=None)
        )

    
        existing_by_key = {
            ".".join(key): value
            for key, value in trans_cap.groupby(["zone", "region", "Technology"])["capacity_mw"].sum().items()
        }
        patch_trans_cap_block(trans_blocks, "trans_links_cap", trans_cap, ["zone", "region", "Technology"], covered_keys)        

    else:
        # Horizon 1: no ledger yet. trans_links() has already seeded trans_links_cap
        # straight from the ODS baseline (links_cap) into trans_blocks
        existing_by_key = {key: float(value) for key, value in trans_blocks["trans_links_cap"]["rows"]}

    absolute_cap = {key: float(value) for key, value in trans_blocks["trans_links_lim_cap"]["rows"]} #absolute maximum buildable trans capacity
    remaining = {key: max(0.0, cap - existing_by_key.get(key, 0.0)) for key, cap in absolute_cap.items()}
    remaining_df = pd.DataFrame(
        [(*key.split("."), value) for key, value in remaining.items()],
        columns=["zone", "region", "Technology", "capacity_mw"],
    )
   
    patch_trans_cap_block(
        trans_blocks, "trans_links_lim_cap", remaining_df, ["zone", "region", "Technology"],
        covered_keys={tuple(key.split(".")) for key in remaining},
    )    

    write_dd_blocks(trans_blocks, trans_order, trans_ddfile)

def read_gen_database(gen_database_path, zones, vre_techs, year_lo, year_hi):
    gdb = pd.read_csv(gen_database_path)
    gdb = gdb[gdb["value"].notna()]               
    gdb = gdb[gdb["limtype"] == "FX"]
    gdb = gdb[gdb["zone"].isin(zones)]

    if year_lo is None:
        gdb = gdb[gdb["commissioning_year"] <= year_hi]
    else:
        gdb = gdb[(gdb["commissioning_year"] > year_lo) & (gdb["commissioning_year"] <= year_hi)]

    gdb = gdb.rename(columns={"value": "capacity_mw", "parameter": "capacity_type"})

    gdb["region"] = gdb["region"].where(gdb["Technology"].isin(vre_techs), other=pd.NA)

    agg = gdb.groupby(
        ["Technology", "zone", "region", "capacity_type", "commissioning_year", "decommissioning_year"], as_index=False, dropna=False
    )["capacity_mw"].sum()

    return agg[["Technology", "zone", "region", "capacity_type", "commissioning_year", "capacity_mw", "decommissioning_year"]]

def patch_gen_database_mandate(blocks, lim_block_name, existing_z, mandate_z):
    if mandate_z.empty:
        return
    existing_lookup = existing_z.groupby(["zone", "Technology"])["capacity_mw"].sum().to_dict()
    mandate_agg = mandate_z.groupby(["zone", "Technology"], as_index=False)["capacity_mw"].sum()
    rows = dict(blocks[lim_block_name]["rows"])
    up_lookup = {}
    for key, value in rows.items():
        zone, tech, limtype = key.split(".")
        if limtype == "UP":
            up_lookup[(zone, tech)] = float(value)

    for _, row in mandate_agg.iterrows():
        zone, tech, mandate_mw = row["zone"], row["Technology"], row["capacity_mw"]
        existing_mw = existing_lookup.get((zone, tech), 0.0)
        lo_final = (existing_mw + mandate_mw) / 1000

        up_value = up_lookup.get((zone, tech))
        if up_value is not None and lo_final > up_value:
            lo_final = up_value     # option to choose: UP=LO or LO=UP

        for lt in ("FX", "LO"):          # need to remove FX to must build planned capacity
            rows.pop(f"{zone}.{tech}.{lt}", None)
        rows[f"{zone}.{tech}.LO"] = str(lo_final)

    blocks[lim_block_name]["rows"] = list(rows.items()) 


def resolve_year_param(params, base_name, planning_horizon):
    """Interpolating the costs for other planning horizon year not in ODS file"""
    if base_name in params.columns:
        return base_name

    exact_col = f"{base_name}{planning_horizon}"
    if exact_col in params.columns:
        return exact_col

    year_re = re.compile(r"^" + re.escape(base_name) + r"(\d{4})$")
    year_cols = {}
    for c in params.columns:
        m = year_re.match(str(c))
        if m:
            year_cols[int(m.group(1))] = c

    if not year_cols:
        return base_name  # error if column name is missplled

    target = int(planning_horizon)
    year_lo = max((y for y in year_cols if y <= target), default=None)
    year_hi = min((y for y in year_cols if y >= target), default=None)

    if year_lo is None or year_hi is None:
        raise ValueError(
            f"planning_horizon={target} is outside the available '{base_name}'"
        )
    if year_lo == year_hi:
        return year_cols[year_lo]

    col_lo, col_hi = year_cols[year_lo], year_cols[year_hi]
    frac = (target - year_lo) / (year_hi - year_lo)
    params[exact_col] = params[col_lo] + (params[col_hi] - params[col_lo]) * frac
    return exact_col

def scen2dd(
    co2budgetddlocation,
    root,
    fin,
    params2write,
    scen_db,
    run,
    esys,
    zones,
    out="work",
    esys_cap=False,
    exist_cap=False,
    exist_agg="region",
    planning_horizon=None,
):
    # co2lim2dd(co2budgetddlocation, root, run, esys, scen_db, out=out)

    scen = pd.read_excel(scen_db, sheet_name="scenario_tech_definition", skiprows=0)

    scen = scen.loc[(scen["Psys Scenario"] == run), :]

    techs = scen["Technology Name (highRES)"]
    tech_class = ["gen", "store"]

    for tech_type in tech_class:
        set_outdd = []
        param_outdd = []

        params = pd.read_excel(fin, sheet_name=None, skiprows=1, engine="calamine")[
            tech_type
        ]

        params = params[params["Technology Name (highRES)"].isin(techs)]

        sets = params2write[tech_type]["set"]
        data_out = params2write[tech_type]["parameter"]

        params = params.merge(scen, on="Technology Name (highRES)")

        # new_lim=pd.read_excel(fin,sheet_name=tech_type+"_lim_z",skiprows=0)

        lims = getzlims(
            pd.read_excel(
                fin, sheet_name=tech_type + "_lim_z", skiprows=0, engine="calamine"
            ),
            techs,
            zones,
            planning_horizon,
        )

        if lims.size != 0:
            param_outdd.append(lims)

        if exist_cap:
            zlims = getzlims(
                pd.read_excel(
                    fin,
                    sheet_name=tech_type + "_exist_z",
                    skiprows=0,
                    engine="calamine",
                ),
                techs,
                zones,
                planning_horizon,
            )

            if zlims.size != 0:
                param_outdd.append(zlims)

            if tech_type == "gen":
                rlims = getrlims(
                    pd.read_excel(
                        fin,
                        sheet_name=tech_type + "_exist_r",
                        skiprows=0,
                        engine="calamine",
                    ),
                    techs,
                    zones,
                    exist_agg,
                    planning_horizon,
                )

                if rlims.size != 0:
                    param_outdd.append(rlims)

        # if exist_cap:
        #     lims = getzlims(
        #         pd.read_excel(fin, sheet_name=tech_type + "_exist_z", skiprows=0),
        #         techs,
        #         zones,
        #     )

        #     if lims.size != 0:
        #         param_outdd.append(
        #             getzlims(
        #                 pd.read_excel(
        #                     fin, sheet_name=tech_type + "_exist_z", skiprows=0
        #                 ),
        #                 techs,
        #                 zones,
        #             )
        #         )

        if esys_cap:
            dout = params.loc[
                (~params["Esys_capacity~2050"].isnull()), "Esys_capacity~2050"
            ]

            if dout.empty:
                continue
            else:
                wrapdd(
                    data2dd(
                        dout.values,
                        [
                            params[
                                (params["Technology Name (highRES)"] != "pgen")
                                & (~params["Esys_capacity~2050"].isnull())
                            ]["Technology Name (highRES)"].values
                        ],
                        rounddp=2,
                    ),
                    tech_type + "_fx_natcap",
                    root / out / (esys + "_" + tech_type + "_fx_natcap.dd"),
                )

        for s in sets:
            if s == "g" or s == "s":
                vals = params["Technology Name (highRES)"]

                set_outdd.append(wrapdd(data2dd(vals, []), s, "set"))
                continue

                # outsets.append(wrapdd(data2dd(vals,[])))
                # set_outdd.append(wrapdd(data2dd(vals,[],s,"set"))
            elif s == "non_vre" or s == "vre" or s == "hydro_res":
                vals = params[params["set"] == s]["Technology Name (highRES)"]

                set_outdd.append(
                    wrapdd(data2dd(vals, []), s + "(" + sets[0] + ")", "set")
                )
                
                
            else:
                vals = params.loc[(~params[s].isnull()), "Technology Name (highRES)"]

                set_outdd.append(
                    wrapdd(
                        data2dd(vals, []),
                        tech_type + "_" + s + "(" + sets[0] + ")",
                        "set",
                    )
                )
                
        for s in data_out:
            for p in data_out[s]:
                p = resolve_year_param(params, p, planning_horizon)
                if "varom" in p or "fuel" in p:
                    # rounddp=5
                    rounddp = 8
                elif "emis" in p:
                    # rounddp=3
                    rounddp = 8
                else:
                    # rounddp=2
                    rounddp = 8

                out_par = tech_type + "_" + str.replace(p, " ", "")

                if "capex" in p or "fuelcost" in p:
                    out_par = re.sub(r"\d+", "", out_par)

                vals = params.loc[~(params[p].isnull()), p].values
                t_out = params.loc[
                    ~(params[p].isnull()), "Technology Name (highRES)"
                ].values

                param_outdd.append(
                    wrapdd(
                        data2dd(vals, [t_out], rounddp=rounddp), out_par, "parameter"
                    )
                )

        param_outdd = np.concatenate(param_outdd, axis=0)
        set_outdd = np.concatenate(set_outdd, axis=0)

        pad = np.repeat(np.array(""), set_outdd.shape[0]).reshape(set_outdd.shape[0], 1)

        outdd = np.concatenate((np.hstack((set_outdd, pad)), param_outdd), axis=0)

        np.savetxt(
            root / out / (run + "_" + tech_type + ".dd"), outdd, delimiter=" ", fmt="%s"
        )

        # param_outdd=np.concatenate(param_outdd,axis=0)
        # set_outdd=np.concatenate(set_outdd,axis=0)

        # np.savetxt(
        #     root/out/(run+"_"+tech_type+"_parameters.dd"), param_outdd,
        #     delimiter=" ",
        #     fmt="%s")
        # np.savetxt(
        #     root/out/(run+"_"+tech_type+"_sets.dd"),
        #     set_outdd, delimiter=" ",
        #     fmt="%s")


def euro_demand2dd(
    europedemandcsvlocation,
    aggregated_regions,
    dpath,
    opath,
    dstart,
    dstop,
    scen_db,
    esys_scen,
    yr,
):
    d = pd.read_csv(europedemandcsvlocation)

    try:
        d["datetime"] = pd.to_datetime(d["datetime"], format="%d/%m/%Y %H:%M")
    except ValueError:
        d["datetime"] = pd.to_datetime(d["datetime"], format="%Y-%m-%d %H:%M:%S")

    d = d.set_index("datetime")

    d = d.loc[:, d.columns.isin(aggregated_regions)]

    d = d[dstart:dstop]

    # TODO a better warning than below

    if d.shape[1] != len(aggregated_regions):
        print("Countries missing...")

    d[(d == 0)] = np.nan

    d.interpolate(limit=2, inplace=True)

    if pd.isnull(d).any().any():
        for c in d.columns[pd.isnull(d).any(axis=0)]:
            print(d.loc[pd.isnull(d[c]), c])

        print("Zero demands for: ", d.columns[pd.isnull(d).any(axis=0)])

    t = np.arange(d.shape[0])
    z = d.columns.values

    wrapdd(
        data2dd(d.values.T, [z, t], all_combin=True),
        "demand",
        "parameter",
        outfile=opath / (esys_scen + "_demand_" + str(yr) + ".dd"),
    )
