"""Post hoc (internal pre-submission review, 2026-10-29): intervals for pass rates among retries that edited a fix file,
each memory condition minus NONE (unpaired subsets, task-cluster bootstrap). Descriptive: conditions on a
post-treatment variable. No model calls.

  python3 m1/posthoc_revision_r2.py -> planning/m1-main-r1/analysis-final/posthoc-revision-r2.json
"""
import json, random, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'm2'))
import posthoc_revision as p  # noqa: E402
from analyze_main import itt  # noqa: E402
from analyze_m2 import cond_diff  # noqa: E402


def main():
    instances, rows = p.load()
    p.a2(instances, rows, random.Random(0))  # annotates _edit_hit; estimates are discarded
    conv = []
    for i in instances:
        arms = {a: {'outcome': v.get('outcome'), 'edit_hit': v['_edit_hit']} for a, v in i['arms'].items()}
        conv.append({'task': i['task'], 'arms': arms})
    rng = random.Random(20261031)
    out = {'kind': 'POST_HOC_REVISION_R2', 'note': 'resolved | edited fix file, condition minus NONE',
           'contrasts': {a: cond_diff(conv, a, 'NONE', rng) for a in ('HISTORY', 'REFLECT', 'SIGNAL', 'BOTH')}}
    path = p.FINAL / 'posthoc-revision-r2.json'
    path.write_text(json.dumps(out, indent=1) + '\n')
    for a, r in out['contrasts'].items():
        print(a, f"{100 * r['difference']:+.1f} [{100 * r['ci95'][0]:+.1f}, {100 * r['ci95'][1]:+.1f}]")


if __name__ == '__main__':
    main()
