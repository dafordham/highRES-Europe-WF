# Archive: the setup before the merge with the pathway code

Kept for reference. Nothing here is used by the current workflow.

On 2026-10-15 Shahzad and Guillermo's `pathway_planning` branch was merged in,
and their inputs step now supplies each milestone's fleet. These files served
the setup before that:

| file | what it was |
|---|---|
| `config/config_pathways_local_5day.yaml` | t0 locally: one stock expansion over 5 days |
| `config/config_pathways_local_5day_dispatch.yaml` | overlay: dispatch the t0 fleet (Test A) |
| `config/config_fox_1year.yaml` | t0 on fox, full year |
| `config/config_fox_1year_dispatch.yaml` | overlay: the full-year dispatch on fox |
| `scripts/ledger2dd.py` | a fleet `.dd` from the test ledger and a horizon |
| `scripts/check_dispatch.py` | Test A: a fixed-fleet dispatch against the run that built the fleet |
| `capacity_ledger.csv` | the test ledger, a 2026 fleet, 548 rows |
| `bench/` | the fox benchmark of 2026-10-14: one job per milestone or one per solve |

They run with the code of the last commit before the merge. Check it out in a
separate directory:

```bash
git worktree add ../highRES-pre-merge 42d2db7
```
