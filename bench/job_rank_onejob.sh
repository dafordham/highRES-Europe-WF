#!/bin/bash
# One SLURM job running a milestone's three ranking dispatches in parallel.
# Argument 1: path to common.sh. sbatch runs a spool copy of this file, so it
# cannot find common.sh relative to itself.
source "$1"

for r in median stress_PL stress_AT; do
  ( solve "$BENCH/rank_onejob_$r" --ens_obj=ON --fix_fleet=fixed_caps ) &
done
wait
