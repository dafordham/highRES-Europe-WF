#!/bin/bash
# Build the benchmark's inputs. Run once, on the login node: file work only,
# nothing is solved here.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/common.sh"

# The environment is activated in your own shell first (see README.md):
# `module` is a function of the login shell and is often missing in scripts.
if [ "${CONDA_DEFAULT_ENV:-}" != "highres" ]; then
  echo "activate the highres environment first:" >&2
  echo "  module load Mamba/4.14.0-0 && source \${EBROOTMAMBA}/bin/activate && conda activate highres" >&2
  exit 1
fi

echo "== disk: the benchmark needs about 50 GB free"
dusage -p ec85 | grep ec85 || true
mkdir -p "$BENCH"

echo "== fleet from the full-year t0. No --zones, so fleet_zones is empty: whole model"
python3 "$REPO/workflow/scripts/caps2dd.py" --from "$T0_DB" --to "$BENCH/fixed_caps.dd"

echo "== three realisations, full year, all 28 zones in scope"
for spec in "median:" "stress_PL:PL=1.45" "stress_AT:AT=1.45"; do
  name=${spec%%:*}
  dz=${spec#*:}
  args=(--from "$SOURCE_DIR" --to "$BENCH/real_$name" --cf 1.0 --demand 1.0
        --zones "$ZONES" --gams "$GAMS")
  [ -n "$dz" ] && args+=(--demand-zone "$dz")
  python3 "$REPO/workflow/scripts/make_realisation.py" "${args[@]}"
done

# One directory per solve, so no two solves share a listing or a scratch dir.
# setup <realisation> <solve dir> <solutiontype>
setup() {
  rm -rf "${2:?}"
  cp -r "$BENCH/real_$1" "$2"
  cp "$BENCH/fixed_caps.dd" "$2/fixed_caps.dd"
  sed -i "s/^threads .*/threads $THREADS/; s/^solutiontype .*/solutiontype $3/" "$2/cplex.opt"
}

for r in median stress_PL stress_AT; do
  setup "$r" "$BENCH/rank_onejob_$r" 2
  setup "$r" "$BENCH/rank_persolve_$r" 2
done
setup stress_PL "$BENCH/expand_barrier" 2
setup stress_PL "$BENCH/expand_crossover" 1

echo "== ready"
ls "$BENCH"
grep -H "threads\|solutiontype" "$BENCH"/rank_onejob_median/cplex.opt "$BENCH"/expand_crossover/cplex.opt

echo "== size of the benchmark so far, and what is free. Each solve adds its"
echo "   results.gdx (~0.3 GB) and a few GB of GAMS temporary files."
du -sh "$BENCH"
dusage -p ec85 | grep ec85 || true
