"""Post hoc analyses added after the internal pre-submission review (2026-10-28). No model calls.

A2  Retry-level localisation: did each retry edit a file changed by the reference fix, by condition;
    paired differences vs NONE; retry success by whether the lesson's first-listed file was a hit.
A3  Where the variation is: number of conditions resolving each failure, one-way ICC of retry
    outcome within failures and within tasks, and project-cluster bootstrap of the primary contrasts.
A4  Effect sizes excluded by the primary and secondary intervals (read from analysis-C.json).

Usage: python3 m1/posthoc_revision.py  -> planning/m1-main-r1/analysis-final/posthoc-revision-r1.json
"""
import json, random, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_main import estimate, holm, itt, paired, PRIMARY, SECONDARY
from fullsuite import BATCH_RUN, FINAL, ROOT, RUNTIME

ARMS = ['NONE', 'HISTORY', 'REFLECT', 'SIGNAL', 'BOTH']
SEED = 20261028


def load():
    combined = json.loads((FINAL / 'C.json').read_text())
    run_of = {}
    for source in combined['sources']:
        for a in json.loads((ROOT / source['path']).read_text())['first_attempts']:
            run_of[a['task']] = BATCH_RUN[Path(source['path']).stem]
    rows = {}
    for task in {i['task'] for i in combined['instances']}:
        row = json.loads((RUNTIME / f"m1-main-paid-{run_of[task]}/tasks/{task.replace('/', '-')}/row.json").read_text())
        for att in row['attempts']:
            rows[task, att['rep']] = att
    return combined['instances'], rows


def _hit(files, ref):
    return any(f == r or r.endswith('/' + f) for f in files for r in ref)


def a2(instances, rows, rng):
    for inst in instances:
        ref = inst['lesson']['reference_files']
        att = rows[inst['task'], inst['rep']]
        inst['_a1_edits'] = [f for f in att['A1'].get('modified_files') or [] if f.endswith('.py')]
        for arm in ARMS:
            r = att['retries'].get(arm)
            if r is None or inst['arms'][arm].get('outcome') == 'harness':
                inst['arms'][arm]['_edit_hit'] = None; inst['arms'][arm]['_edited'] = None
                continue
            edits = [f for f in r.get('modified_files') or [] if f.endswith('.py')]
            inst['arms'][arm]['_edited'] = 1.0 if edits else 0.0
            inst['arms'][arm]['_edit_hit'] = 1.0 if _hit(edits, ref) else 0.0
    out = {'retry_edit_hit': {}, 'retry_edited_any': {}, 'contrast_edit_hit_vs_none': {},
           'resolved_given_edit_hit': {}}
    for arm in ARMS:
        vals = [i['arms'][arm]['_edit_hit'] for i in instances if i['arms'][arm]['_edit_hit'] is not None]
        out['retry_edit_hit'][arm] = {'rate': sum(vals) / len(vals), 'n': len(vals)}
        ed = [i['arms'][arm]['_edited'] for i in instances if i['arms'][arm]['_edited'] is not None]
        out['retry_edited_any'][arm] = sum(ed) / len(ed)
        res = [(i['arms'][arm]['_edit_hit'], itt(i['arms'][arm])) for i in instances if i['arms'][arm]['_edit_hit'] is not None]
        out['resolved_given_edit_hit'][arm] = {
            'hit': _rate([y for x, y in res if x == 1.0]), 'miss': _rate([y for x, y in res if x == 0.0])}
    for a, b in PRIMARY:
        out['contrast_edit_hit_vs_none'][a] = estimate(paired(instances, a, b, lambda x: x['_edit_hit']), rng)
    # Same, split by whether the failed first attempt edited a source file.
    for label, keep in (('a1_edited', lambda i: bool(i['_a1_edits'])), ('a1_no_edit', lambda i: not i['_a1_edits'])):
        sub = [i for i in instances if keep(i)]
        out[f'retry_edit_hit_{label}'] = {arm: _rate([i['arms'][arm]['_edit_hit'] for i in sub if i['arms'][arm]['_edit_hit'] is not None]) for arm in ARMS}
        out[f'resolved_{label}'] = {arm: _rate([itt(i['arms'][arm]) for i in sub if itt(i['arms'][arm]) is not None]) for arm in ARMS}
        out[f'contrast_resolved_{label}'] = {a: estimate(paired(sub, a, b, itt), rng) for a, b in PRIMARY}
    # Does a correct lesson predict a successful lesson-based retry?
    for arm in ('REFLECT', 'BOTH'):
        for key in ('top1_file_hit', 'file_hit', 'top1_function_hit'):
            groups = defaultdict(list)
            for i in instances:
                y = itt(i['arms'][arm])
                if y is not None:
                    groups[bool(i['lesson'][key])].append(y)
            out.setdefault('lesson_hit_vs_success', {}).setdefault(arm, {})[key] = {
                'hit': _rate(groups[True]), 'miss': _rate(groups[False])}
    # Within-failure: REFLECT minus HISTORY by whether the lesson's first file was a hit.
    for flag in (True, False):
        sub = [i for i in instances if bool(i['lesson']['top1_file_hit']) == flag]
        out.setdefault('reflect_minus_history_by_top1', {})[str(flag)] = estimate(paired(sub, 'REFLECT', 'HISTORY', itt), rng)
    return out


def _rate(v):
    v = [x for x in v if x is not None]
    return {'rate': sum(v) / len(v) if v else None, 'n': len(v)}


def _icc(groups):
    """One-way random-effects ICC(1) for unbalanced groups (ANOVA estimator)."""
    groups = [g for g in groups if g]
    N = sum(len(g) for g in groups); k = len(groups)
    grand = sum(sum(g) for g in groups) / N
    ssb = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups)
    ssw = sum(sum((x - sum(g) / len(g)) ** 2 for x in g) for g in groups)
    msb, msw = ssb / (k - 1), ssw / (N - k)
    n0 = (N - sum(len(g) ** 2 for g in groups) / N) / (k - 1)
    return (msb - msw) / (msb + (n0 - 1) * msw)


def a3(instances, rng, project_of):
    counts = Counter()
    for i in instances:
        ys = [itt(i['arms'][a]) for a in ARMS]
        if None not in ys:
            counts[int(sum(ys))] += 1
    by_failure = [[itt(i['arms'][a]) for a in ARMS if itt(i['arms'][a]) is not None] for i in instances]
    by_task = defaultdict(list)
    for i in instances:
        by_task[i['task']] += [itt(i['arms'][a]) for a in ARMS if itt(i['arms'][a]) is not None]
    # Share of outcome variance explained by condition (pooled over failures).
    all_y = [y for g in by_failure for y in g]; grand = sum(all_y) / len(all_y)
    arm_means = {a: _mean([itt(i['arms'][a]) for i in instances]) for a in ARMS}
    n_arm = {a: sum(1 for i in instances if itt(i['arms'][a]) is not None) for a in ARMS}
    ss_total = sum((y - grand) ** 2 for y in all_y)
    ss_arm = sum(n_arm[a] * (arm_means[a] - grand) ** 2 for a in ARMS)
    # Project-cluster bootstrap of the primary contrasts (11 clusters; descriptive only).
    proj = {}
    for a, b in PRIMARY + SECONDARY:
        by_t = paired(instances, a, b, itt)
        by_p = defaultdict(list)
        for t, v in by_t.items():
            by_p[project_of[t]] += v
        proj[f'{a}-{b}'] = estimate(dict(by_p), rng)
    return {'n_conditions_resolving_failure': dict(sorted(counts.items())),
            'icc_failure': _icc(by_failure), 'icc_task': _icc(list(by_task.values())),
            'share_variance_condition': ss_arm / ss_total, 'project_cluster_bootstrap': proj}


def _mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v)


def a4():
    analysis = json.loads((FINAL / 'analysis-C.json').read_text())
    return {'note': 'Lower/upper 95% bounds of the predeclared contrasts, for statements of excluded effects.',
            'source': 'planning/m1-main-r1/analysis-final/analysis-C.json', 'keys': sorted(analysis)[:40]}


def main():
    instances, rows = load()
    rng = random.Random(SEED)
    project_of = {i['task']: i['task'].split('/')[0] for i in instances}
    out = {'kind': 'POST_HOC_REVISION_R1', 'seed': SEED, 'written': '2026-10-28',
           'reason': 'internal pre-submission review; all analyses post hoc',
           'A2': a2(instances, rows, rng), 'A3': a3(instances, rng, project_of), 'A4': a4()}
    # Added after the M2 validity review (r1): editing any source file vs NONE, and the share of
    # editing retries that touched a fix file. Separate RNG so the estimates above are unchanged.
    rng2 = random.Random(SEED + 1)
    out['A2']['contrast_edited_any_vs_none'] = {a: estimate(paired(instances, a, b, lambda x: x['_edited']), rng2) for a, b in PRIMARY}
    out['A2']['fix_file_given_edited'] = {arm: _rate([i['arms'][arm]['_edit_hit'] for i in instances if i['arms'][arm]['_edited'] == 1.0]) for arm in ARMS}
    (FINAL / 'posthoc-revision-r1.json').write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps(out, indent=1)[:6000])


if __name__ == '__main__':
    main()
