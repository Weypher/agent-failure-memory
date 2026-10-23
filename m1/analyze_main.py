"""Predeclared analysis of the main instance table (m1/score_main.py output). No model calls.

Unit: natural failure instance (task, first-attempt repetition); clusters: tasks.
Primary (ITT): retry resolved=1, everything else 0 (failing tests, context_limit,
integrity-flagged). Contrasts vs NONE for HISTORY, REFLECT, SIGNAL, BOTH: mean paired
difference over instances where both arms ran; 95% task-cluster bootstrap CI; two-sided
task-level sign-flip permutation p; Holm over the four. Secondary: REFLECT-HISTORY,
REFLECT-SIGNAL, BOTH-REFLECT; complete-case sensitivity (drop context_limit/integrity);
cost and wall-clock differences. Descriptive: lesson accuracy (lenient/strict), baseline
accuracies and lesson-beyond-baseline, unsupported-claim rates, all with cluster CIs.
Usage: python3 m1/analyze_main.py <instances.json> <out.json> [seed]
"""
import json, random, sys
from collections import defaultdict
from pathlib import Path

B = 10000
PRIMARY = [('HISTORY', 'NONE'), ('REFLECT', 'NONE'), ('SIGNAL', 'NONE'), ('BOTH', 'NONE')]
SECONDARY = [('REFLECT', 'HISTORY'), ('REFLECT', 'SIGNAL'), ('BOTH', 'REFLECT')]


def itt(arm):
    """Resolved=1; agent-caused unresolved (incl. broken builds, timeouts, context limits) and
    integrity-excluded=0; retries cut short by harness failures are missing (None)."""
    if arm.get('outcome') == 'harness':
        return None
    return 1.0 if arm.get('outcome') == 'resolved' else 0.0


def complete_case(arm):
    if arm.get('outcome') in ('harness', 'excluded_integrity') or arm['status'] == 'context_limit':
        return None
    return 1.0 if arm.get('outcome') == 'resolved' else 0.0


def task_weighted(by_task):
    """Each task weighted equally: mean of per-task mean differences (sensitivity)."""
    means = [sum(v) / len(v) for v in by_task.values() if v]
    return sum(means) / len(means) if means else None


def paired(instances, a, b, value):
    """Per-task lists of within-instance differences value(a)-value(b)."""
    by_task = defaultdict(list)
    for inst in instances:
        if a in inst['arms'] and b in inst['arms']:
            x, y = value(inst['arms'][a]), value(inst['arms'][b])
            if x is not None and y is not None:
                by_task[inst['task']].append(x - y)
    return by_task


def estimate(by_task, rng):
    tasks = sorted(by_task); n = sum(len(v) for v in by_task.values())
    if not n:
        return {'n_instances': 0, 'n_tasks': 0}
    mean = sum(sum(v) for v in by_task.values()) / n
    boots = []
    for _ in range(B):
        sample = [by_task[rng.choice(tasks)] for _ in tasks]
        m = sum(len(v) for v in sample)
        boots.append(sum(sum(v) for v in sample) / m)
    boots.sort()
    # Sign-flip of whole task clusters: under H0 (arm labels exchangeable within task).
    totals = [sum(v) for v in (by_task[t] for t in tasks)]
    observed = abs(sum(totals)); extreme = 0
    for _ in range(B):
        if abs(sum(s if rng.random() < .5 else -s for s in totals)) >= observed - 1e-12:
            extreme += 1
    return {'n_instances': n, 'n_tasks': len(tasks), 'difference': mean,
            'ci95': [boots[int(.025 * B)], boots[int(.975 * B) - 1]], 'p_permutation': (extreme + 1) / (B + 1)}


def holm(results):
    order = sorted(results, key=lambda r: r.get('p_permutation', 1))
    running = 0.0
    for i, r in enumerate(order):
        if 'p_permutation' in r:
            running = max(running, min(1.0, (len(order) - i) * r['p_permutation'])); r['p_holm'] = running
    return results


def proportion(instances, getter, rng):
    by_task = defaultdict(list)
    for inst in instances:
        v = getter(inst)
        if v is not None:
            by_task[inst['task']].append(float(v))
    return estimate(by_task, rng) if by_task else {'n_instances': 0}


def main(table, target, seed=20261015):
    rng = random.Random(int(seed)); data = json.loads(Path(table).read_text()); inst = data['instances']
    result = {'kind': 'PREDECLARED_MAIN_ANALYSIS', 'seed': int(seed), 'bootstrap': B,
              'first_attempts': {'n': len(data['first_attempts']),
                                 'by_status': {s: sum(1 for a in data['first_attempts'] if a['status'] == s)
                                               for s in sorted({a['status'] for a in data['first_attempts']})},
                                 'by_outcome': {s: sum(1 for a in data['first_attempts'] if a.get('outcome') == s)
                                                for s in sorted({str(a.get('outcome')) for a in data['first_attempts']})},
                                 'natural_failures': len(inst)},
              'availability': {arm: sum(1 for i in inst if arm in i['arms']) for arm in ('NONE', 'HISTORY', 'REFLECT', 'SIGNAL', 'BOTH')},
              'lesson_censored': sum(1 for i in inst if i['lesson_status'] != 'valid'),
              'lesson_status_counts': {k: sum(1 for i in inst if i['lesson_status'] == k) for k in sorted({i['lesson_status'] for i in inst})},
              'first_attempts_missing_harness': sum(1 for a in data['first_attempts'] if a.get('outcome') == 'harness')}
    result['primary_itt'] = holm([dict(contrast=f'{a}-{b}', task_weighted=task_weighted(paired(inst, a, b, itt)),
                                       **estimate(paired(inst, a, b, itt), rng)) for a, b in PRIMARY])
    result['framing'] = 'Estimation: report differences with 95% CIs; a non-significant result is not evidence of no effect.'
    result['per_project'] = {}
    for i in inst:
        p = result['per_project'].setdefault(i['task'].split('/')[0], {'instances': 0, 'tasks': set()})
        p['instances'] += 1; p['tasks'].add(i['task'])
    result['per_project'] = {k: {'instances': v['instances'], 'tasks': len(v['tasks'])} for k, v in sorted(result['per_project'].items())}
    result['retry_subtypes_by_arm'] = {arm: dict(sorted(__import__('collections').Counter(
        i['arms'][arm].get('subtype') for i in inst if arm in i['arms']).items(), key=lambda kv: str(kv[0])))
        for arm in ('NONE', 'HISTORY', 'REFLECT', 'SIGNAL', 'BOTH')}
    result['lesson_status_by_a1_subtype'] = {}
    for i in inst:
        d = result['lesson_status_by_a1_subtype'].setdefault(str(i.get('a1_subtype')), {})
        d[i['lesson_status']] = d.get(i['lesson_status'], 0) + 1
    result['secondary_itt'] = [dict(contrast=f'{a}-{b}', **estimate(paired(inst, a, b, itt), rng)) for a, b in SECONDARY]
    result['sensitivity_complete_case'] = [dict(contrast=f'{a}-{b}', **estimate(paired(inst, a, b, complete_case), rng)) for a, b in PRIMARY]
    result['cost_usd'] = [dict(contrast=f'{a}-{b}', **estimate(paired(inst, a, b, lambda x: None if x.get('outcome') == 'harness' else x['cost_nusd'] / 1e9), rng)) for a, b in PRIMARY]
    result['seconds'] = [dict(contrast=f'{a}-{b}', **estimate(paired(inst, a, b, lambda x: None if x.get('outcome') == 'harness' else x['seconds']), rng)) for a, b in PRIMARY]
    # Missing-arm bounds (main review r7 MV7-4): harness-missing retry arms imputed as resolved / unresolved.
    missing = sum(1 for i in inst for v in i['arms'].values() if v.get('outcome') == 'harness')
    result['missing_retry_arms'] = {arm: sum(1 for i in inst if i['arms'].get(arm, {}).get('outcome') == 'harness') for arm in ('NONE', 'HISTORY', 'REFLECT', 'SIGNAL', 'BOTH')}
    if missing:
        for label, fill in (('impute_resolved', 1.0), ('impute_unresolved', 0.0)):
            value = lambda x, f=fill: f if x.get('outcome') == 'harness' else itt(x)  # noqa: E731
            result['bounds_' + label] = [dict(contrast=f'{a}-{b}', **estimate(paired(inst, a, b, value), rng)) for a, b in PRIMARY]
    result['resolution_rates'] = {arm: proportion(inst, lambda i, a=arm: itt(i['arms'][a]) if a in i['arms'] else None, rng)
                                  for arm in ('NONE', 'HISTORY', 'REFLECT', 'SIGNAL', 'BOTH')}
    lessons = [i for i in inst if 'lesson' in i]
    desc = {}
    for key in ('top1_file_hit', 'top1_function_hit', 'file_hit', 'file_hit_strict', 'function_hit', 'function_hit_strict'):
        desc['lesson_' + key] = proportion(lessons, lambda i, k=key: i['lesson'][k], rng)
        for base in ('baseline_attempt', 'baseline_traceback'):
            desc[f'{base}_{key}'] = proportion(lessons, lambda i, k=key, b=base: i[b][k], rng)
            desc[f'lesson_beyond_{base}_{key}'] = proportion(
                lessons, lambda i, k=key, b=base: float(i['lesson'][k] and not i[b][k]), rng)
    desc['any_unsupported_claim'] = proportion(lessons, lambda i: i['claims']['any_unsupported'], rng)
    result['descriptive_lessons'] = desc
    Path(target).write_text(json.dumps(result, indent=1) + '\n')
    print(json.dumps({r['contrast']: round(r.get('difference', float('nan')), 3) for r in result['primary_itt']}))


if __name__ == '__main__':
    main(*sys.argv[1:4])
