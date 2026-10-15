# Configs from before the merge with the pathway code

Kept for reference. These four configs were written for the Snakefile as it
was before Shahzad and Guillermo's `pathway_planning` branch was merged on
2026-10-15. The merged Snakefile needs keys they do not have (planning
horizons, growth, emission, wind and solar scenarios), so they no longer run
with it.

| file | what it ran |
|---|---|
| `config_pathways_local_5day.yaml` | t0 locally: one stock expansion over 5 days |
| `config_pathways_local_5day_dispatch.yaml` | overlay on the one above: dispatch the t0 fleet with `fix_fleet` and `ens_obj` (Test A) |
| `config_fox_1year.yaml` | t0 on fox, full year |
| `config_fox_1year_dispatch.yaml` | overlay: the full-year dispatch on fox |

Their replacements are `config/config_robust_local_5day.yaml` and
`config/config_robust_fox.yaml`.

To run one of these again, check out the last commit before the merge in a
separate directory and point Snakemake at it:

```bash
git worktree add ../highRES-pre-merge 42d2db7
```

The configs still give the scenario settings of every run made before the
merge, including the fox benchmark of 2026-10-14.
