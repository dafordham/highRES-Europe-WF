# Settings for the fox benchmark, sourced by every script in bench/.
#
# Set THREADS and MEM_GB from the node sizes before submitting:
#   sinfo -o "%P %c %m" | sort -u      # partition, cores per node, MB per node
# 3 x THREADS cores and 3 x MEM_GB must fit on ONE node, or the one-job variant
# can never start.

REPO=/fp/projects01/ec85/models/davidac/Article_pathways/highRES-Europe-WF
BENCH=/fp/projects01/ec85/models/davidac/Article_pathways/bench_2026-10
GAMS=/fp/projects01/ec85/.bin/gams/gams54.1_linux_x64_64_sfx
CODE=$REPO/resources/highRES-Europe-GAMS
ACCOUNT=ec85
TIME=24:00:00     # the partition maximum; full-year solves need it

THREADS=16        # CPLEX threads per solve, identical in every variant
MEM_GB=60         # per solve. run_gams asks 50 GB for a full-year expansion

# Full-year inputs and the t0 fleet, already on fox from 2026-10-01/02.
SOURCE_DIR=/fp/projects01/ec85/models/davidac/Article_pathways/work_fox_1year_dispatch/BASE_2010_region
T0_DB=/fp/projects01/ec85/models/davidac/Article_pathways/work_fox_1year_pathways/BASE_2010_region/results.db

# All 28, so nothing is zeroed: the timing should be the production model's.
ZONES=AT,BE,BG,CH,CZ,DE,DK,EE,ES,FI,FR,UK,GR,HR,HU,IE,IT,LT,LU,LV,NL,NO,PL,PT,RO,SE,SI,SK

# The arguments pathway_step.py passes, minus the mode switches. None may be
# empty: GAMS lets an empty --key= swallow the next argument.
GAMS_ARGS=(
  --codefolderpath="$CODE" --psys_scen=BASE --co2_target_type=intensity
  --co2_target_extent=all --weather_yr=2010 --dem_yr=2010 --outname=results
  --store_initial_level=0.5 --store_final_level=0.5 --hydro_res_initial_fill=0.8
  --hydro_res_min=0.5 --pgen=20.0 --emis_price=0 --trans_inv=TYNDP
  --trans_cap_lim=20 --UC=OFF --f_res=OFF --store_uc=OFF --EV=OFF --EV_flex=0
  --V2G=OFF --EV_pcap=0.011 --EV_soc_min=0.2 --EV_soc_max=0.8
)

# solve <dir> <mode switches...>
# One GAMS solve in its own directory, with wall time and peak memory recorded
# per solve. Per-solve memory matters for the one-job variant, where SLURM
# only reports the job's total.
solve() {
  local dir=$1; shift
  cd "$dir" || return 1
  local start; start=$(date +%s)
  if [ -x /usr/bin/time ]; then
    /usr/bin/time -v "$GAMS/gams" "$CODE/highres.gms" "${GAMS_ARGS[@]}" "$@" lo=2 \
      > solve.log 2> time.log
  else
    "$GAMS/gams" "$CODE/highres.gms" "${GAMS_ARGS[@]}" "$@" lo=2 > solve.log 2>&1
  fi
  echo "wall_s $(( $(date +%s) - start ))" > wall.txt
}
