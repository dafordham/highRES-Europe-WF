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

# Set 2026-10-14. sinfo: the default partition, normal, has 128 cores and
# 494592 MB per node. sacct on the full-year solves of 2026-09-30 to 10-02, 30
# threads each: peaks of 66 GiB (4 h 13) and 103 GiB (6 h 25). So 120 GB per
# solve, what those jobs requested, and 3 x 30 cores / 3 x 120 GB still fits
# one node for the one-job variant.
THREADS=30        # CPLEX threads per solve, identical in every variant; = production
MEM_GB=120        # per solve

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
#
# GAMS keeps its temporary files in the run directory, inside the project
# folder. Returns GAMS's own exit code, so a SLURM FAILED means GAMS failed.
solve() {
  local dir=$1; shift
  cd "$dir" || return 1

  # A failed solve must not leave an earlier run's listing to be read as its
  # result: collect.sh takes the model status from highres.lst.
  rm -f highres.lst highres.log results.gdx solve.log time.log wall.txt

  # GNU time takes -v; the BSD one on a Mac does not and fails before GAMS
  # even starts. Use it only if it works here.
  local timer=()
  if /usr/bin/time -v true >/dev/null 2>&1; then timer=(/usr/bin/time -v); fi

  local start rc; start=$(date +%s)
  "${timer[@]}" "$GAMS/gams" "$CODE/highres.gms" "${GAMS_ARGS[@]}" "$@" \
    lo=2 > solve.log 2> time.log
  rc=$?
  echo "wall_s $(( $(date +%s) - start ))" > wall.txt
  return $rc
}
