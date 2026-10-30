"""Primary contrasts with the full-suite outcome (planning/fullsuite-rule-r1.json). Post hoc; no model calls.

suite_clean outcome per retry: resolved on the selected tests AND no regression in the stable-pass set of the
fixed checkout. Resolved retries whose task has no reference suite (amendment impl-2b) keep their selected-test
outcome. Sensitivity (decided after seeing the regressions, labelled as such): ignore two kinds of test that do not
check behaviour -- black's self-formatting checks (test_black, test_piping: black.py must be formatted by itself)
and tqdm's timing tests (tests_perf.py, run under machine load).

  python3 m1/fullsuite_analysis.py -> planning/m1-main-r1/analysis-final/fullsuite-analysis-r1.json
"""
import json, random, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_main import estimate, holm, paired, PRIMARY, SECONDARY  # noqa: E402

FINAL = Path(__file__).resolve().parents[1] / 'planning/m1-main-r1/analysis-final'
ARMS = ['NONE', 'HISTORY', 'REFLECT', 'SIGNAL', 'BOTH']
NON_BEHAVIOURAL = ('tests/test_black.py::BlackTestCase::test_black', 'tests/test_black.py::BlackTestCase::test_piping',
                   'tqdm/tests/tests_perf.py::')


def main():
    combined = json.loads((FINAL / 'C.json').read_text())
    rows = json.loads((FINAL / 'fullsuite-r1.json').read_text())['rows']
    by = {(r['task'], r['rep'], r['arm']): r for r in rows}
    out = {'kind': 'POST_HOC_FULLSUITE_ANALYSIS_R1', 'seed': 20261030}
    for label, ignore in (('all_tests', ()), ('behavioural_only', NON_BEHAVIOURAL)):
        def clean(r):
            if r['suite_clean'] is None:
                return True
            regs = [t for t in r.get('regressions', []) if not any(t.startswith(p) for p in ignore)]
            return r['status'] == 'ok' and not regs and (not ignore or r['n_regressions'] <= 50)
        insts = []
        for i in combined['instances']:
            arms = {}
            for a in ARMS:
                v = i['arms'][a]
                if v['outcome'] == 'harness':
                    arms[a] = None
                elif v['outcome'] == 'resolved':
                    arms[a] = 1.0 if clean(by[(i['task'], i['rep'], a)]) else 0.0
                else:
                    arms[a] = 0.0
            insts.append({'task': i['task'], 'arms': arms})
        rng = random.Random(20261030)
        val = lambda x: x  # noqa: E731
        prim = holm([dict(estimate(paired(insts, a, b, val), rng), contrast=f'{a}-{b}') for a, b in PRIMARY])
        sec = [dict(estimate(paired(insts, a, b, val), rng), contrast=f'{a}-{b}') for a, b in SECONDARY]
        rates = {a: sum(x['arms'][a] for x in insts if x['arms'][a] is not None) / sum(1 for x in insts if x['arms'][a] is not None) for a in ARMS}
        regressed = Counter()
        for r in rows:
            if r['suite_clean'] is not None and not clean(r):
                regressed[r['arm']] += 1
        out[label] = {'rates': rates, 'primary': prim, 'secondary': sec, 'regressed_resolved_attempts': dict(regressed)}
    out['resolved_attempts'] = dict(Counter(r['arm'] for r in rows))
    out['not_checked'] = dict(Counter(r['arm'] for r in rows if r['suite_clean'] is None))
    out['edited_unprotected_test_files'] = dict(Counter(r['arm'] for r in rows if r.get('edited_test_files')))
    (FINAL / 'fullsuite-analysis-r1.json').write_text(json.dumps(out, indent=1) + '\n')
    for label in ('all_tests', 'behavioural_only'):
        print(label, {a: round(100 * v, 1) for a, v in out[label]['rates'].items()}, out[label]['regressed_resolved_attempts'])
        for r in out[label]['primary'] + out[label]['secondary']:
            print('  ', r['contrast'], f"{100 * r['difference']:+.1f} [{100 * r['ci95'][0]:+.1f}, {100 * r['ci95'][1]:+.1f}]", 'pHolm', round(r.get('p_holm', r['p_permutation']), 3))
    print(out['resolved_attempts'], out['not_checked'], out['edited_unprotected_test_files'])


if __name__ == '__main__':
    main()
