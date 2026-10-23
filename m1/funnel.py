"""Eligibility funnel per project for the main frame (no model outcomes). Persists every stage.
Usage: python3 m1/funnel.py <env-batch-all.json> <sandbox summaries comma-separated> <statements summary> <main manifest> <out.json>
"""
import json, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'envs'))
import trial_build  # noqa: E402

NATIVE = {'pandas', 'matplotlib', 'spacy'}


def main(env_batch, sandboxes, statements, manifest_path, target):
    env = {r['task']: r['outcome'] for r in json.loads(Path(env_batch).read_text())['rows']}
    sb = {}
    for p in sandboxes.split(','):
        for r in json.loads(Path(p).read_text())['rows']:
            sb[r['task']] = r
    st = {r['task']: r for r in json.loads(Path(statements).read_text())['rows']}
    m = json.loads(Path(manifest_path).read_text()); final = {t['task_id'] for t in m['tasks']}
    skipped = {s['task'] for s in m['skipped_over_bound']}; excluded = set(m['excluded'])
    table = defaultdict(lambda: defaultdict(int)); reasons = {}
    for pdir in sorted((trial_build.BUGSINPY / 'projects').iterdir()):
        for b in (pdir / 'bugs').iterdir():
            if not b.name.isdigit():
                continue
            t = f'{pdir.name}/{b.name}'; row = table[pdir.name]; row['bugsinpy'] += 1
            if pdir.name in NATIVE:
                reasons[t] = 'native_extension_project'; continue
            if t in ('fastapi/1', 'tqdm/1'):
                reasons[t] = 'old_project_exposed_not_built'; continue
            if t not in env:
                reasons[t] = 'not_built'; continue
            row['built'] += 1
            if env[t] != 'controls_as_expected':
                reasons[t] = 'environment_' + env[t]; continue
            row['environment_ok'] += 1
            if not sb.get(t, {}).get('eligible'):
                reasons[t] = 'sandbox_controls_failed' if t in sb else 'sandbox_not_run'; continue
            row['sandbox_ok'] += 1
            if not st.get(t, {}).get('eligible'):
                reasons[t] = 'statement_' + (st.get(t, {}).get('reason') or 'not_fetched'); continue
            row['statement_ok'] += 1
            if t in excluded:
                reasons[t] = 'excluded_dev_or_exposed'; continue
            row['not_excluded'] += 1
            if t in skipped:
                reasons[t] = 'initial_prompt_over_32000'; continue
            row['final'] += int(t in final)
            if t not in final:
                reasons[t] = 'unexplained'
    out = {'kind': 'ELIGIBILITY_FUNNEL_NO_MODEL_OUTCOMES', 'projects': {k: dict(v) for k, v in sorted(table.items())},
           'totals': {k: sum(v.get(k, 0) for v in table.values()) for k in
                      ['bugsinpy', 'built', 'environment_ok', 'sandbox_ok', 'statement_ok', 'not_excluded', 'final']},
           'reason_counts': {r: sum(1 for x in reasons.values() if x == r) for r in sorted(set(reasons.values()))},
           'reasons': reasons}
    assert out['totals']['final'] == len(final) and 'unexplained' not in out['reason_counts'], out['reason_counts']
    Path(target).write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps(out['totals'])); print(json.dumps(out['reason_counts']))


if __name__ == '__main__':
    main(*sys.argv[1:6])
