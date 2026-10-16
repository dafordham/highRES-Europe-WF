# Fox benchmark: how to package a milestone's solves

Decides who runs the milestone sequence (see the briefing note). Measures, at
full year and 28 zones:

- a milestone's **three ranking dispatches in one job**, in parallel, against
  the same three as **three separate jobs**, with the same CPLEX threads per
  solve, so only the packaging differs;
- **one expansion with and without crossover** (`solutiontype 1` vs `2`). At 5
  days crossover removed every sub-MW capacity for one extra second; the
  question is its full-year cost.

## Run, on fox

```bash
module load Mamba/4.14.0-0 && source ${EBROOTMAMBA}/bin/activate && conda activate highres
cd /fp/projects01/ec85/models/davidac/Article_pathways/highRES-Europe-WF
git pull origin pathways && git submodule update --init

sinfo -o "%P %c %m" | sort -u     # then set THREADS and MEM_GB in bench/common.sh
bench/prepare.sh                  # login node, file work only, a few minutes
bench/submit.sh                   # submits 6 jobs
squeue -u $USER                   # come back when they are gone
bench/collect.sh                  # paste the whole output back
```

3 x THREADS cores and 3 x MEM_GB must fit on one node, or the one-job variant
never starts. Check `dusage` shows about 50 GB free before `submit.sh`.

## Decision rule, fixed before the numbers

- Three dispatches fit on one node, **and** the one-job wall time including
  queue wait is within about 20% of the per-solve variant → the loop runs
  **inside one job** per milestone: simplest code.
- Otherwise → the loop is a rule **on the login node submitting one job per
  solve**.
- Snakemake checkpoints only if both turn out unworkable.

Everything lands in `/fp/projects01/ec85/models/davidac/Article_pathways/bench_2026-10`.
Delete it after reading the numbers; nothing in it is needed later.
