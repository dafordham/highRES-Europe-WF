#!/bin/bash
# One SLURM job running a milestone's three ranking dispatches in parallel.
# Argument 1: path to common.sh. sbatch runs a spool copy of this file, so it
# cannot find common.sh relative to itself.
source "$1"

# A bare `wait` always returns 0, so wait on each solve and fail the job if any
# of them failed.
pids=()
for r in median stress_PL stress_AT; do
  ( solve "$BENCH/rank_onejob_$r" --ens_obj=ON --fix_fleet=fixed_caps ) &
  pids+=($!)
done
rc=0
for p in "${pids[@]}"; do wait "$p" || rc=1; done
exit $rc
