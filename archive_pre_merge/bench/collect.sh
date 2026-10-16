#!/bin/bash
# Gather the numbers once every job has finished. Paste the whole output back.
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/common.sh"
ids=$(paste -sd, "$BENCH/job_ids.txt")

echo "== jobs: queue wait is Start minus Submit; MaxRSS is on the .batch line"
sacct -j "$ids" \
  --format=JobID%14,JobName%18,Submit,Start,End,Elapsed,ReqMem,MaxRSS,AllocCPUS,State

echo
echo "== seff"
for id in $(cat "$BENCH/job_ids.txt"); do
  seff "$id" | grep -E "Job ID|Job Name|State|Cores per node|CPU Efficiency|Job Wall-clock|Memory Utilized|Memory Efficiency"
  echo
done

echo "== per solve: model status, wall time, peak memory, disk"
for d in "$BENCH"/rank_* "$BENCH"/expand_*; do
  st=$(grep -m1 "MODEL STATUS" "$d/highres.lst" 2>/dev/null | tr -s ' ' | cut -d' ' -f4-)
  wall=$(cut -d' ' -f2 "$d/wall.txt" 2>/dev/null)
  rss=$(grep "Maximum resident" "$d/time.log" 2>/dev/null | awk '{printf "%.1f GiB", $NF/1048576}')
  size=$(du -sh "$d" 2>/dev/null | cut -f1)
  printf "%-26s %-22s wall %7ss  peak %-10s  disk %s\n" \
    "$(basename "$d")" "${st:-no listing}" "${wall:--}" "${rss:--}" "$size"
done

echo
echo "== crossover: new-capacity entries by size (5-day test: 155 under 1 MW and 28 negative without crossover, none with)"
# Counts var_new_pcap_z levels, the last csv column ("Val"). Model units are
# GW, so 0.001 is 1 MW. gdxdump leaves out records at exactly zero, which is
# why there is no zero count here.
for v in barrier crossover; do
  gdx="$BENCH/expand_$v/results.gdx"
  if [ ! -f "$gdx" ]; then printf "%-10s no results.gdx\n" "$v"; continue; fi
  "$GAMS/gdxdump" "$gdx" symb=var_new_pcap_z format=csv 2>/dev/null | awk -F, -v name="$v" '
    NR == 1 { next }
    { x = $NF + 0
      if (x < 0) neg++; else if (x > 0 && x < 0.001) tiny++; else if (x >= 0.001) real++ }
    END { printf "%-10s 0 < x < 1 MW: %d   negative: %d   >= 1 MW: %d\n",
                 name, tiny + 0, neg + 0, real + 0 }'
done

echo
dusage -p ec85 | grep ec85 || true
