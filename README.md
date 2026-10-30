# Failure memory for repository-repair retries

Code and data for the study *What Does a Repair Agent Gain from Remembering Its Failure? An Exploratory Study on
Real Python Bugs*. The main study (M1) is described first; the replication (M2), the notice-only control (M3) and
the post hoc analyses are listed under "Revision material" below. A repair agent (`gpt-5.4-mini-2026-03-17`) attempts 75 BugsInPy bugs six times each; every failed first attempt
is retried once under five conditions (NONE, HISTORY, REFLECT, SIGNAL, BOTH). The repository contains the harness and
experiment code as they were run, the frozen task inputs, the analysis plan, and the per-failure tables and analysis
outputs behind every number in the paper.

## Layout

| Path | Contents |
| --- | --- |
| `harness/` | Model clients (strict JSON operations and lessons), call accounting, pytest event scorer |
| `runner/` | Sandboxed multi-turn repair loop, workspace snapshots, pytest event recorder |
| `envs/` | Environment builders for BugsInPy tasks, sandbox controls, issue-statement collection |
| `m1/` | Experiment: retry conditions (`memory.py`), runner (`run_main.py`), scoring (`score_main.py`), lesson localisation (`accuracy.py`), analysis (`analyze_main.py`, `posthoc_final.py`) and helpers |
| `scripts/build_assets.py` | Regenerates the paper's tables and figures from `data/results/full` |
| `protocol/` | Analysis plan (`protocol.json`), task eligibility and statement rules, selection funnel |
| `data/inputs/` | Task statements and initial prompts (`tasks/`), reference-fix locations (`reference/`), run manifests and retained-task lists |
| `data/results/full/` | Per-failure tables (`C.json`: all 75 tasks; `T2`–`T4`: batches 1–3) and analysis outputs |
| `data/results/analysis-46/` | The interim analysis of the first 46 tasks |

Some files refer to their original locations under `planning/`; these correspond to `protocol/` and `data/results/`
here (`planning/m1-main-r1/analysis-final` → `data/results/full`, `analysis-46` → `data/results/analysis-46`,
`main-eligibility-rules-r1.json` → `protocol/eligibility-rules.json`, `statement-rule-draft-r1.json` →
`protocol/statement-rule.json`, `funnel-r2.json` → `protocol/funnel.json`).

## Reproducing the analysis

The analysis needs only Python 3.12 (standard library); the figures also need matplotlib.

```bash
python3 m1/analyze_main.py data/results/full/C.json /tmp/analysis-C.json
python3 scripts/build_assets.py data/results/full protocol/funnel.json /tmp/generated
```

`analysis-C.json` is the primary analysis (task-cluster bootstrap with 10,000 resamples, seed 20261015; sign-flip
permutation tests; Holm correction). `posthoc-r1.json` holds the post hoc analyses marked as such in the paper.

## Re-running the experiment

Re-running requires Linux with bubblewrap, BugsInPy at revision `11c5f1e`, the per-task environments built by
`envs/trial_build.py` and `envs/batch_build.py` (CPython built from source for older projects), and an OpenAI API
key. Entry points: `m1/prepare_main.py` builds the inputs, `m1/run_main.py` runs first attempts and retries,
`m1/score_main.py` scores them, and `m1/combine_instances.py` merges batches. The runner refuses to start without a
frozen scope file and an approval record (`m1/stage.py`); adapt or remove that check for your own runs. Model
outputs are stochastic (temperature 1, no seed), so a rerun will not reproduce individual attempts.

Local file-system paths in this copy have been replaced for anonymous review. Workspace snapshots (several GB) are not included.



## Revision material (added for review)

| Path | Contents |
| --- | --- |
| `m1/posthoc_revision.py` | Post hoc analyses of how memory changes the retry (edits, fix files, variance by failure, project-cluster bootstrap) -> `data/results/full/posthoc-revision-r1.json` |
| `m1/fullsuite*.py`, `protocol/fullsuite-rule.json` | Full-test-suite check of every resolved attempt (rule and amendments, runner, summary, contrasts) -> `data/results/full/fullsuite-*.json` |
| `m2/`, `protocol/m2-protocol.json`, `data/results/m2/` | Exploratory replication with `gpt-5.4-2026-03-05` (NONE, HISTORY, REFLECT): runner, frozen protocol, predeclared analysis and per-task rows |
| `m3/`, `protocol/m3-*.json`, `data/results/m3/` | Notice-only control (NONE, NOTICE, HISTORY, REFLECT on all M1/M2 failures): runners incl. the two resumptions, frozen protocol and addenda, merge and predeclared analyses |
| `annotation/` | Blind annotation protocol and a stratified sample of 60 lessons with their reference fixes (not yet annotated) |
| `transcripts/` | Raw model requests, responses, tool outputs and test logs of every retained M1, M2 and M3 attempt (M3 rows of interrupted tasks are included but not analysed) (`cat transcripts.tar.xz.part* > t.tar.xz; tar xJf t.tar.xz`) |

Workspace snapshots are not included (several GB).
