# Threads, cores and memory for the milestone job

A one-off test (October 2026). A milestone job runs a rank's three dispatches
side by side, then the expansion alone. With 90 cores and 3 × 30 CPLEX
threads, 2030 at full year took 6 h 12 and about 560 core-hours, and 60 of
the 90 cores sat idle during the 4-hour expansion. This test re-solves the
2030 solves with fewer or more threads to find a cheaper setting.

`run.sh` copies a finished solve's inputs, changes only `threads` in its
`cplex.opt`, and rebuilds the GAMS command from the settings in the solve's
own `highres.log`. A re-solve should give the same model status and
objective as the original.

## Known already (2030, full year, 28 zones)

| solve | threads | wall time | peak memory |
|---|---|---|---|
| second-rank dispatch, alone | 30 | 1 h 08 – 1 h 20 | 50 GB |
| three second-rank dispatches side by side | 3 × 30 | 1 h 54 – 2 h 10 | 145 GiB together |
| expansion, alone | 30 | 3 h 57 | 68 GB |

## Run, on fox

```bash
B=/fp/projects01/ec85/models/davidac/Article_pathways
S=$B/work_fox_robust/BASE_BASE_region/low_low_TYNDP_BASE_UP/2030_2010/robust
R=$B/highRES-Europe-WF/bench_threads/run.sh
O=$B/bench_threads_2026-10
mkdir -p $O
A="--account=ec85 --ntasks=1"
sbatch $A --job-name=exp90  --cpus-per-task=90 --mem=90G  --time=06:00:00 --output=$O/exp90.out  $R 90 $S/iter1/expansion
sbatch $A --job-name=exp60  --cpus-per-task=60 --mem=90G  --time=06:00:00 --output=$O/exp60.out  $R 60 $S/iter1/expansion
sbatch $A --job-name=rank20 --cpus-per-task=60 --mem=170G --time=06:00:00 --output=$O/rank20.out $R 20 $S/iter2/median $S/iter2/stress_AT $S/iter2/stress_PL
sbatch $A --job-name=rank10 --cpus-per-task=30 --mem=170G --time=08:00:00 --output=$O/rank10.out $R 10 $S/iter2/median $S/iter2/stress_AT $S/iter2/stress_PL
```

About 700 core-hours in all, and about 2 GB of results.

## Collect

```bash
column -t $O/results.tsv
sacct -u $USER -S <submission date> --name=exp90,exp60,rank20,rank10 \
  --format=JobName,State,Start,Elapsed,MaxRSS | grep -v "\.ex"
```

Check first that every re-solve has the model status and objective of its
original (`highres.lst` in `$S/iter*/<name>`). Then delete `$O`.

## Decision rule, fixed before the numbers

Per milestone with one expansion (rank, expansion, rank):

| option | cores | rank | expansion |
|---|---|---|---|
| now | 90 | 3 × 30 | 30 threads |
| A | 90 | 3 × 30 | 90 threads |
| B | 60 | 3 × 20 | 60 threads |
| C | 30 | 3 × 10 | 30 threads |

Pick the option with the fewest core-hours whose milestone stays under 10 h,
so two expansions fit in a 24 h job. Memory request: the measured peak plus
10%.
