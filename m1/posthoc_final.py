"""POST-HOC analyses requested by the final validity review (m1_final_validity_r1). Not preregistered.

Reads the combined instance table (C.json) and route outputs; no model calls. Reports:
B conditional on whether the failed attempt edited source files; candidate counts and precision;
function-level rates on instances with a reference function; per-project table; excluding black;
leave-one-project-out; NONE rate by route; context/output-limit counts per arm; finish-reason
distribution of truncated responses; context-limited retries whose final workspace resolved.
Usage: python3 m1/posthoc_final.py <C.json> <T2.json> <T3.json> <T4.json> <out.json> <route output roots...>
"""
import json, random, sys
from collections import defaultdict, Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_main import paired, estimate, itt, PRIMARY  # noqa: E402

ARMS = ('NONE', 'HISTORY', 'REFLECT', 'SIGNAL', 'BOTH')


def rate(xs):
    xs = [x for x in xs if x is not None]
    return {'n': len(xs), 'rate': (sum(xs) / len(xs)) if xs else None}


def contrasts(inst, rng, B=2000):
    import analyze_main
    analyze_main.B = B
    return {f'{a}-{b}': {k: v for k, v in estimate(paired(inst, a, b, itt), rng).items() if k in ('difference', 'ci95', 'n_instances', 'n_tasks')}
            for a, b in PRIMARY}


def main(c_path, t2, t3, t4, target, *roots):
    rng = random.Random(20261016)
    inst = json.loads(Path(c_path).read_text())['instances']
    out = {'kind': 'POST_HOC_NOT_PREREGISTERED', 'source': c_path}
    # B conditional on attempt edits
    edited = [i for i in inst if i['baseline_attempt']['n_suspected_files'] > 0]
    unedited = [i for i in inst if i['baseline_attempt']['n_suspected_files'] == 0]
    def b(sub):
        return {'n': len(sub),
                'lesson_file_hit': rate([float(i['lesson']['file_hit']) for i in sub]),
                'lesson_top1_file_hit': rate([float(i['lesson']['top1_file_hit']) for i in sub]),
                'attempt_file_hit': rate([float(i['baseline_attempt']['file_hit']) for i in sub]),
                'attempt_top1_file_hit': rate([float(i['baseline_attempt']['top1_file_hit']) for i in sub]),
                'lesson_mean_files': sum(i['lesson']['n_suspected_files'] for i in sub) / len(sub) if sub else None,
                'attempt_mean_files': sum(i['baseline_attempt']['n_suspected_files'] for i in sub) / len(sub) if sub else None,
                'lesson_file_precision': rate([i['lesson']['file_precision'] for i in sub]),
                'attempt_file_precision': rate([i['baseline_attempt']['file_precision'] for i in sub]),
                'lesson_beyond_attempt_file': rate([float(i['lesson']['file_hit'] and not i['baseline_attempt']['file_hit']) for i in sub])}
    out['B_by_attempt_edits'] = {'attempt_edited_source': b(edited), 'attempt_no_source_edits': b(unedited), 'all': b(inst)}
    with_fn = [i for i in inst if i['lesson']['reference_functions']]
    out['B_functions_applicable'] = {'n': len(with_fn),
                                     'lesson_function_hit': rate([float(i['lesson']['function_hit']) for i in with_fn]),
                                     'lesson_function_hit_strict': rate([float(i['lesson']['function_hit_strict']) for i in with_fn]),
                                     'attempt_function_hit': rate([float(i['baseline_attempt']['function_hit']) for i in with_fn])}
    single = Counter(tuple(i['lesson']['reference_files']) for i in inst)
    out['B_reference_file_concentration'] = {'most_common': [[list(k), v] for k, v in single.most_common(5)],
                                             'excluding_black_lesson_file_hit': rate([float(i['lesson']['file_hit']) for i in inst if not i['task'].startswith('black/')])}
    # Per project, excluding black, leave-one-project-out
    by_proj = defaultdict(list)
    for i in inst:
        by_proj[i['task'].split('/')[0]].append(i)
    out['per_project'] = {p: {'instances': len(v), 'tasks': len({i['task'] for i in v}),
                              **{a: rate([itt(i['arms'][a]) for i in v if a in i['arms']])['rate'] for a in ARMS}}
                          for p, v in sorted(by_proj.items())}
    out['excluding_black'] = contrasts([i for i in inst if not i['task'].startswith('black/')], rng)
    out['leave_one_project_out'] = {p: {k: round(v['difference'], 4) for k, v in contrasts([i for i in inst if not i['task'].startswith(p + '/')], rng, B=200).items()}
                                    for p in sorted(by_proj)}
    # Routes
    for label, path in (('r2_kept', t2), ('r3_kept', t3), ('r4', t4)):
        ri = json.loads(Path(path).read_text())['instances']
        out.setdefault('by_route', {})[label] = {'instances': len(ri), **{a: rate([itt(i['arms'][a]) for i in ri if a in i['arms']])['rate'] for a in ARMS}}
    # Limits per arm; context-limited but resolved
    out['retry_limits_by_arm'] = {a: {'context_limit': sum(1 for i in inst if i['arms'].get(a, {}).get('status') == 'context_limit'),
                                      'output_limit': sum(1 for i in inst if i['arms'].get(a, {}).get('status') == 'output_limit'),
                                      'context_limit_resolved': sum(1 for i in inst if i['arms'].get(a, {}).get('status') == 'context_limit'
                                                                    and i['arms'][a].get('outcome') == 'resolved')} for a in ARMS}
    def cl_zero(x):
        return 0.0 if x.get('status') == 'context_limit' else itt(x)
    import analyze_main
    analyze_main.B = 2000
    out['sensitivity_context_limit_as_unresolved'] = {f'{a}-{b}': round(estimate(paired(inst, a, b, cl_zero), rng)['difference'], 4) for a, b in PRIMARY}
    # Finish reasons of truncated/refused responses
    fr = Counter()
    for root in roots:
        for f in Path(root, 'responses').glob('*.incomplete.json'):
            d = json.loads(f.read_text()); fr[(Path(root).name, d.get('finish_reason'), d.get('refusal'))] += 1
    out['incomplete_finish_reasons'] = [[list(k), v] for k, v in sorted(fr.items())]
    Path(target).write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps({'B_edited': out['B_by_attempt_edits']['attempt_edited_source']['n'], 'excl_black': {k: round(v['difference'], 3) for k, v in out['excluding_black'].items()}}))


if __name__ == '__main__':
    main(*sys.argv[1:])
