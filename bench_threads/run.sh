#!/bin/bash
# Re-solve finished robust-loop solves with a different number of CPLEX
# threads, and record wall time and peak memory. One SLURM job per test; all
# solves given to one job run at the same time, as in a milestone's rank.
#
#   run.sh <threads> <solve dir> [<solve dir> ...]
#
# Each solve dir is a finished solve of the loop (robust/iter*/<name>). Its
# inputs are copied to $BENCH/<run>/<name>, its GAMS command is rebuilt from
# the settings its own highres.log records, so the re-solve differs from the
# original only in the threads. See README.md.

THREADS=$1; shift
BENCH=${BENCH:-/fp/projects01/ec85/models/davidac/Article_pathways/bench_threads_2026-10}
RUN=${SLURM_JOB_NAME:-local}

# GNU time reports peak memory with -v; the BSD one on a Mac does not.
TIMER=()
if /usr/bin/time -v true >/dev/null 2>&1; then TIMER=(/usr/bin/time -v); fi

solve() {
  local src=$1 d=$BENCH/$RUN/$(basename "$1")
  rm -rf "$d"; mkdir -p "$d"
  for f in "$src"/*; do
    case $(basename "$f") in
      results.*|highres.*|solve.log|time.log|wall.txt) ;;
      *) [ -f "$f" ] && cp "$f" "$d/" ;;
    esac
  done
  sed -i.bak "s/^threads .*/threads $THREADS/" "$d/cplex.opt" && rm -f "$d/cplex.opt.bak"

  # The original's settings, from the block GAMS writes at the top of its log
  local sysdir input args
  sysdir=$(awk '$1 == "SysDir" {print $2; exit}' "$src/highres.log")
  input=$(awk '$1 == "Input" {print $2; exit}' "$src/highres.log")
  args=$(awk '/^    --/ {print $1 "=" $2}' "$src/highres.log")

  cd "$d" || return
  local start; start=$(date +%s)
  # shellcheck disable=SC2086
  "${TIMER[@]}" "$sysdir/gams" "$input" $args lo=2 > solve.log 2> time.log
  echo $(( $(date +%s) - start )) > wall.txt
}

for src in "$@"; do solve "$src" & done
wait

# One line per solve: run, solve, threads, wall seconds, peak memory in GB,
# model status, objective value
OUT=$BENCH/results.tsv
[ -f "$OUT" ] || printf "run\tsolve\tthreads\twall_s\tpeak_GB\tmodelstat\tobjective\n" > "$OUT"
for src in "$@"; do
  d=$BENCH/$RUN/$(basename "$src")
  kb=$(awk -F: '/Maximum resident set size/ {print $2}' "$d/time.log")
  stat=$(awk '/MODEL STATUS/ {print $4; exit}' "$d/highres.lst")
  obj=$(awk '/OBJECTIVE VALUE/ {print $4; exit}' "$d/highres.lst")
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$RUN" "$(basename "$src")" "$THREADS" \
    "$(cat "$d/wall.txt")" "$(awk -v k="${kb:-0}" 'BEGIN {printf "%.1f", k / 1048576}')" \
    "${stat:-none}" "${obj:-none}" >> "$OUT"
done
