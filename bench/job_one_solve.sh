#!/bin/bash
# One SLURM job, one solve.
# Argument 1: path to common.sh. Argument 2: the solve directory. The rest are
# the mode switches, passed straight to GAMS.
source "$1"
dir=$2
shift 2
solve "$dir" "$@"
