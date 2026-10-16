#!/bin/bash
# Submit every benchmark job at once. Run on the login node after prepare.sh.
#
# Plain sbatch, so naming jobs with -J is fine. The --job-name deadlock was
# Snakemake's SLURM executor rejecting a second name, not sbatch.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/common.sh"

common=(--parsable --account="$ACCOUNT" --time="$TIME" -o "$BENCH/slurm-%j.out")
ids=()

# One job holding all three dispatches: three times the cores and memory.
ids+=("$(sbatch "${common[@]}" -J pw-rank-onejob \
          --cpus-per-task=$((3 * THREADS)) --mem=$((3 * MEM_GB))G \
          "$HERE/job_rank_onejob.sh" "$HERE/common.sh")")

# One job per dispatch.
for r in median stress_PL stress_AT; do
  ids+=("$(sbatch "${common[@]}" -J "pw-rank-$r" \
            --cpus-per-task="$THREADS" --mem="${MEM_GB}G" \
            "$HERE/job_one_solve.sh" "$HERE/common.sh" \
            "$BENCH/rank_persolve_$r" --ens_obj=ON --fix_fleet=fixed_caps)")
done

# The other half of an iteration: one expansion, with and without crossover.
for v in barrier crossover; do
  ids+=("$(sbatch "${common[@]}" -J "pw-expand-$v" \
            --cpus-per-task="$THREADS" --mem="${MEM_GB}G" \
            "$HERE/job_one_solve.sh" "$HERE/common.sh" \
            "$BENCH/expand_$v" --fleet_floor=fixed_caps)")
done

printf '%s\n' "${ids[@]}" > "$BENCH/job_ids.txt"
echo "submitted: ${ids[*]}"
squeue -u "$USER"
