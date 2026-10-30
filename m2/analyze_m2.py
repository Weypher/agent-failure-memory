"""Predeclared analysis of M2 (planning/m2-gpt54/review-r2/protocol.json). No model calls.

  python3 m2/analyze_m2.py <output_root>/main <manifest.json> <out.json> [ledger.sqlite]

Reads each task's row.json (only rows with status 'complete' if the main part stopped: labelled partial),
classifies first attempts and retries as in M1 (outcome written by the runner), scores whether each retry
edited any source file and a source file changed by the reference fix, and reports H1-H4 with task-cluster
bootstrap CIs, sign-flip permutation p (two-sided) and Holm within each family, plus the declared descriptive
outputs (conditional pass rates and their HISTORY-NONE difference, endings, cost and wall-clock per condition,
complete-case three-arm sensitivity, side-by-side M1 values).
"""
import json, random, sqlite3, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'm1'))
from analyze_main import estimate, holm, paired, proportion  # noqa: E402
import accuracy  # noqa: E402

SEED = 20261029
ARMS = ['NONE', 'HISTORY', 'REFLECT']
ROUTE = 'afm-m2-gpt54/main'
ALPHA = 0.05


def value(arm):
    """ITT: resolved 1; agent-caused unresolved and integrity-flagged 0; harness missing."""
    if arm['outcome'] == 'harness':
        return None
    return 1.0 if arm['outcome'] == 'resolved' else 0.0


def load(run_root, manifest_path, costs):
    manifest = json.loads(Path(manifest_path).read_text())
    ref_root = Path(manifest_path).parent / 'reference'
    instances, firsts, statuses = [], [], Counter()
    for t in manifest['tasks']:
        name = t['task_id']; key = name.replace('/', '-')
        row_path = Path(run_root) / 'tasks' / key / 'row.json'
        if not row_path.exists():
            statuses['missing_row'] += 1; continue
        row = json.loads(row_path.read_text()); statuses[row.get('status')] += 1
        if row.get('status') != 'complete':
            continue  # partial rule: only completed tasks are analysed
        protected = json.loads(Path(t['sandbox_spec']).read_text())['protected_paths']
        ref = accuracy.reference_locations(ref_root / key / 'buggy', ref_root / key / 'fixed', t['reference_changed'])['files']
        ref = [f for f in ref if not any(f == p or f.startswith(p.rstrip('/') + '/') for p in protected)]
        for entry in row['attempts']:
            if 'A1' not in entry:
                continue
            a1 = entry['A1']; rep = entry['rep']
            firsts.append({'task': name, 'rep': rep, 'outcome': a1['m1_outcome'], 'subtype': a1['m1_subtype']})
            if a1['m1_outcome'] != 'unresolved':
                continue
            lesson = entry.get('lesson') or {}
            arms = {}
            for arm in ARMS:
                r = (entry.get('retries') or {}).get(arm)
                if r is None:
                    continue
                missing = r['m1_outcome'] == 'harness'
                edits = [f for f in r.get('modified_files') or [] if f.endswith('.py')]
                hit = any(f == x or x.endswith('/' + f) for f in edits for x in ref)
                cost = costs.get(f'{ROUTE}/{name}/R{rep}-{arm}/')
                secs = r.get('elapsed_seconds')
                if arm == 'REFLECT':  # the lesson call belongs to the REFLECT condition's cost
                    cost = None if cost is None else cost + costs.get(f'{ROUTE}/{name}/LESSON-{rep}/', 0.0)
                    secs = None if secs is None else secs + (lesson.get('seconds') or 0.0)
                arms[arm] = {'outcome': r['m1_outcome'], 'subtype': r['m1_subtype'],
                             'edited': None if missing else (1.0 if edits else 0.0),
                             'edit_hit': None if missing else (1.0 if hit else 0.0),
                             'cost_usd': None if missing else cost, 'seconds': None if missing else secs}
            instances.append({'task': name, 'rep': rep, 'lesson': lesson.get('status'), 'arms': arms, 'reference_files': ref})
    return instances, firsts, statuses


def ledger_costs(ledger):
    """Charged USD per call-sequence prefix (route/task/label/), read-only."""
    if ledger is None:
        return {}
    db = sqlite3.connect(Path(ledger).as_uri() + '?mode=ro', uri=True)
    out = defaultdict(float)
    for cid, charged in db.execute('SELECT id, charged FROM calls WHERE id LIKE ?', (ROUTE + '/%',)):
        out[cid.rsplit('/', 1)[0] + '/'] += (charged or 0) / 1e9
    db.close()
    return dict(out)


def cond_diff(instances, a, b, rng, B=10000):
    """HISTORY-NONE difference of pass rates among retries that edited a fix file (unpaired subsets,
    task-cluster bootstrap over tasks). Descriptive: conditions on a post-treatment variable."""
    by_task = defaultdict(lambda: {a: [], b: []})
    for i in instances:
        for arm in (a, b):
            x = i['arms'].get(arm)
            if x and x['edit_hit'] == 1.0 and value(x) is not None:
                by_task[i['task']][arm].append(value(x))
    tasks = sorted(by_task)

    def rate(sample, arm):
        v = [y for t in sample for y in by_task[t][arm]]
        return sum(v) / len(v) if v else None
    point = (rate(tasks, a) or 0) - (rate(tasks, b) or 0) if rate(tasks, a) is not None and rate(tasks, b) is not None else None
    boots = []
    for _ in range(B):
        s = [rng.choice(tasks) for _ in tasks]
        ra, rb = rate(s, a), rate(s, b)
        if ra is not None and rb is not None:
            boots.append(ra - rb)
    boots.sort()
    return {'contrast': f'{a}-{b} resolved | edited fix file', 'difference': point, 'n_tasks': len(tasks),
            'ci95': [boots[int(.025 * len(boots))], boots[int(.975 * len(boots)) - 1]] if boots else None}


def _rate(instances, getter, rng):
    r = proportion(instances, getter, rng); r.pop('p_permutation', None)
    return r


def m1_reference():
    """M1 values for side-by-side display only (no pooling, no test of difference)."""
    final = ROOT / 'planning/m1-main-r1/analysis-final'
    a = json.loads((final / 'analysis-C.json').read_text()); p = json.loads((final / 'posthoc-revision-r1.json').read_text())['A2']
    pick = lambda rows, c: next(({k: r[k] for k in ('difference', 'ci95', 'n_instances')} for r in rows if r['contrast'] == c), None)  # noqa: E731
    return {'resolved': {c: pick(a['primary_itt'], c) for c in ('HISTORY-NONE', 'REFLECT-NONE')},
            'reflect_minus_history': pick(a['secondary_itt'], 'REFLECT-HISTORY'),
            'edited_any': {k: {kk: v[kk] for kk in ('difference', 'ci95', 'n_instances')} for k, v in p['contrast_edited_any_vs_none'].items() if k in ('HISTORY', 'REFLECT')},
            'fix_file_given_edited': {k: p['fix_file_given_edited'][k] for k in ARMS},
            'source': ['planning/m1-main-r1/analysis-final/analysis-C.json', 'planning/m1-main-r1/analysis-final/posthoc-revision-r1.json']}


def family(instances, getter, label, rng):
    rows = holm([dict(estimate(paired(instances, a, 'NONE', getter), rng), contrast=f'{a}-NONE {label}') for a in ('HISTORY', 'REFLECT')])
    return rows


def main(run_root, manifest_path, out_path, ledger=None):
    costs = ledger_costs(ledger)
    instances, firsts, statuses = load(run_root, manifest_path, costs)
    rng = random.Random(SEED)
    partial = any(k != 'complete' for k in statuses)
    n_fail = len(instances); n_tasks = len({i['task'] for i in instances})
    small = n_fail < 30 or n_tasks < 12
    itt = lambda a: value(a)  # noqa: E731
    edited = lambda a: a['edited']  # noqa: E731
    h1 = family(instances, edited, 'edited_any_source', rng)
    h1_supported = (not small) and all(r.get('p_holm', 1) < ALPHA and r.get('difference', 0) > 0 for r in h1)
    h1_fix = family(instances, lambda a: a['edit_hit'], 'edited_fix_file', rng)  # secondary, as in M1 post hoc
    h4 = family(instances, itt, 'resolved', rng)
    h3 = dict(estimate(paired(instances, 'REFLECT', 'HISTORY', itt), rng), contrast='REFLECT-HISTORY resolved')
    h2 = cond_diff(instances, 'HISTORY', 'NONE', rng)
    rates = {}
    for arm in ARMS:
        has = lambda i, a=arm: a in i['arms']  # noqa: E731
        rates[arm] = {
            'resolved': _rate(instances, lambda i, a=arm: value(i['arms'][a]) if a in i['arms'] else None, rng),
            'edited_any': _rate(instances, lambda i, a=arm: i['arms'][a]['edited'] if a in i['arms'] else None, rng),
            'edit_fix_file': _rate(instances, lambda i, a=arm: i['arms'][a]['edit_hit'] if a in i['arms'] else None, rng),
            'fix_file_given_edited': _rate([i for i in instances if has(i) and i['arms'][arm]['edited'] == 1.0],
                                           lambda i, a=arm: i['arms'][a]['edit_hit'], rng),
            'resolved_given_fix_file': _rate([i for i in instances if has(i) and i['arms'][arm]['edit_hit'] == 1.0],
                                             lambda i, a=arm: value(i['arms'][a]), rng),
            'resolved_given_no_fix_file': _rate([i for i in instances if has(i) and i['arms'][arm]['edit_hit'] == 0.0],
                                                lambda i, a=arm: value(i['arms'][a]), rng)}
    cost = {arm: _mean([i['arms'][arm]['cost_usd'] for i in instances if arm in i['arms']]) for arm in ARMS}
    secs = {arm: _mean([i['arms'][arm]['seconds'] for i in instances if arm in i['arms']]) for arm in ARMS}
    full3 = [i for i in instances if all(a in i['arms'] and value(i['arms'][a]) is not None for a in ARMS)]
    sens = {'n_instances': len(full3),
            'edited_any': [dict(estimate(paired(full3, a, 'NONE', edited), rng), contrast=f'{a}-NONE') for a in ('HISTORY', 'REFLECT')],
            'resolved': [dict(estimate(paired(full3, a, 'NONE', itt), rng), contrast=f'{a}-NONE') for a in ('HISTORY', 'REFLECT')]}
    endings = {arm: dict(Counter(i['arms'][arm]['subtype'] for i in instances if arm in i['arms'])) for arm in ARMS}
    out = {'kind': 'M2_PREDECLARED_ANALYSIS', 'seed': SEED, 'protocol': 'planning/m2-gpt54/review-r2/protocol.json',
           'partial': partial, 'row_status': dict(statuses),
           'first_attempts': {'n': len(firsts), 'outcomes': dict(Counter(f['outcome'] for f in firsts)),
                              'endings': dict(Counter(f['subtype'] for f in firsts))},
           'failures': n_fail, 'tasks_with_failures': n_tasks, 'small_sample_rule_applies': small,
           'lesson_status': dict(Counter(i['lesson'] for i in instances)),
           'H1_edited_any_source': h1, 'H1_supported': h1_supported, 'H1_secondary_edited_fix_file': h1_fix,
           'H2_resolved_given_fix_file_HISTORY_minus_NONE': h2, 'H3_reflect_minus_history': h3, 'H4_resolved': h4,
           'rates': rates, 'mean_cost_usd': cost, 'mean_seconds': secs, 'complete_three_arm_sensitivity': sens,
           'retry_endings': endings, 'm1_side_by_side': m1_reference()}
    if small:
        for fam in (h1, h1_fix, h4, [h3], sens['edited_any'], sens['resolved']):
            for r in fam:
                r.pop('p_permutation', None); r.pop('p_holm', None)
    Path(out_path).write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps({k: out[k] for k in ('partial', 'first_attempts', 'failures', 'tasks_with_failures', 'H1_edited_any_source',
                                          'H1_supported', 'H4_resolved', 'H3_reflect_minus_history')}, indent=1))


def _mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


if __name__ == '__main__':
    main(*sys.argv[1:5])
