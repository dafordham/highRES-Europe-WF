# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**highRES-Europe-WF** is a Snakemake workflow for the highRES (high-resolution electricity system) model for Europe. It prepares inputs from weather/geo data, then runs a GAMS optimization model to plan least-cost electricity systems with high renewable shares.

## Commands

**Run the full workflow:**
```bash
snakemake -c all --configfile config/config_ci.yaml
```

**Run a single rule:**
```bash
snakemake -j1 -R <rule_name>
# e.g.: snakemake -j1 -R build_technoeconomic_inputs
```

**Visualize the DAG:**
```bash
snakemake --dag | dot -Tpng > dag.png
```

**Set up the Conda environment:**
```bash
mamba env create -f workflow/envs/highres_environment.yaml
mamba activate highres
```

**Build documentation:**
```bash
cd docs && make html
```

**Download input data from Zenodo** (DOI `10.5281/zenodo.14223617`):
```bash
zenodo_get 10.5281/zenodo.14223617
unzip resources.zip && unzip weatherdata.zip && unzip geodata.zip
```

**Initialize submodules after cloning:**
```bash
git submodule init && git submodule update
```

## Architecture

### Workflow Phases

The Snakemake workflow (`workflow/Snakefile`) has two phases:

**Phase 1 — Input preparation** (order matters):
1. `build_shapes` → geographic boundaries from geodata
2. `build_vre_cf_grid` → VRE capacity factors from weather data (memory-intensive: 50 GB)
3. `build_vre_land_avail` → available land for renewables (memory-intensive: 50 GB)
4. `build_vre_cf_inputs` → capacity factor processing per zone
5. `build_technoeconomic_inputs` → transforms `.ods` database → GAMS `.dd` format
6. `build_hydro_capfac` → hydropower inflow time series
7. `build_zones_file`, `build_vre_areas_file`, `build_ev_inputs` → auxiliary inputs
8. `build_vre_file` → combine all VRE → Parquet → CSV → GDX
9. `build_cplex_opt`, `build_hydrores_inflow`, `build_inputs` → finalize

**Phase 2 — Optimization & results:**
1. `run_gams` → executes GAMS model via `workflow/scripts/run_gams.py`
2. `convert_results` → GDX → SQLite
3. `convert_results_db_parquet` → SQLite → Parquet

### Key Directories

- `workflow/Snakefile` — all rules and dependencies
- `workflow/scripts/` — Python scripts called by Snakemake rules
- `workflow/notebooks/` — Jupyter notebooks used as rules (run via `papermill`)
- `workflow/envs/` — per-rule Conda environment files (different rules use different envs)
- `config/` — YAML configuration files
- `resources/highRES-Europe-GAMS/` — **git submodule** containing the GAMS model (`.gms` files)
- `resources/` — technoeconomic database (`.ods`), scenario definitions (`.xls`)
- `shared_input/` — large geodata and weather data (downloaded from Zenodo, not in git)
- `work/` — model outputs and results

### Configuration System

Scenarios are defined in `config/config_ci.yaml` (or user-specific configs). Snakemake expands all combinations of:
- `years` × `spatials` × `power_system_scenarios` × `epsilons`

Key config parameters:
| Parameter | Options | Effect |
|-----------|---------|--------|
| `mode` | `normal`, `developer` | `developer` preserves intermediate files |
| `spatials` | `region`, `nuts2`, `grid` | Spatial resolution |
| `years` | e.g. `[2010]` | Weather year(s) |
| `epsilons` | e.g. `[0.0, 0.05]` | Loss-of-load probability values |
| `unit_commitment` | `ON`, `OFF` | UC constraints in GAMS |
| `EV` | `ON`, `OFF` | Electric vehicle modeling |
| `co2_target_type` | `intensity`, `budget` | CO2 constraint formulation |
| `trans_inv` | `OFF`, `TYNDP`, `USER` | Transmission investment |
| `windoff_floating` | `True`, `False` | Floating offshore wind |

### GAMS Submodule

`resources/highRES-Europe-GAMS/` is a git submodule. Core files:
- `highres.gms` — main model
- `highres_data_input.gms` — data loading
- `highres_hydro.gms`, `highres_storage_setup.gms`, `highres_uc_setup.gms` — subsystem equations
- `highres_results.gms` — results export

Changes to GAMS model code require working in the submodule and committing separately.

### Data Flow

```
Weather/Geo data (NetCDF, shapefiles)
        ↓ atlite / rioxarray
VRE capacity factors + land availability
        ↓
Technoeconomic DB (.ods) + Scenario params (.xls)
        ↓ data2dd.py / data2dd_funcs.py
GAMS input files (.dd, .gdx)
        ↓ GAMS + CPLEX
Results (.gdx) → SQLite → Parquet
```

### Important Constraints

- **GAMS license required**: Commercial GAMS + CPLEX license; path specified in config under `gams_sysdir`
- **Memory**: `build_vre_cf_grid` and `build_vre_land_avail` require ~50 GB RAM
- **Multiple Conda envs**: Different rules use different `conda:` directives — check `workflow/envs/` before modifying dependencies
- **Notebooks as rules**: Several rules run Jupyter notebooks via `papermill`; edit the `.ipynb` source in `workflow/notebooks/`, not a generated copy
