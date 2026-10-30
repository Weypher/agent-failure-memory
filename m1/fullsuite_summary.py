"""Summarise the full-suite regression check under planning/fullsuite-rule-r1.json incl. amendment impl-2.

  python3 m1/fullsuite_summary.py   -> planning/m1-main-r1/analysis-final/fullsuite-r1.json
"""
import json, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fullsuite import FINAL, OUT, _key, _load, index  # noqa: E402


def main():
    tasks, units = index()
    rows = []
    for u in units:
        base = json.loads((OUT / 'baseline' / _key(u['task']) / 'summary.json').read_text())
        rec = _load(OUT / 'patched' / _key(u['task']) / u['dir'] / 'record.json')
        row = dict(u, project=tasks[u['task']]['project'], mode=base['mode'], n_stable=len(base['stable_pass']))
        if not any(base['returned']):
            row.update(status='not_checked_no_reference', suite_clean=None)
        elif rec is None:
            row.update(status='missing', suite_clean=None)
        else:
            broken = sorted(set(base['stable_pass']) - set(rec['passed']))
            ok = rec['returned']  # amendment impl-2(a): pytest returned, even if the process hung on exit
            row.update(status='ok' if ok else 'did_not_return', regressions=broken[:50], n_regressions=len(broken),
                       suite_clean=bool(ok and not broken), edited_test_files=rec['edited_test_files'],
                       killed_after_return=bool(rec['timeout'] and rec['returned']))
        rows.append(row)
    out = {'kind': 'POST_HOC_FULLSUITE_R1', 'rule': 'planning/fullsuite-rule-r1.json (incl. amendments impl-1, impl-2)', 'rows': rows}
    (FINAL / 'fullsuite-r1.json').write_text(json.dumps(out, indent=1) + '\n')
    c = Counter((r['arm'], r['suite_clean']) for r in rows)
    for arm in ('FIRST', 'NONE', 'HISTORY', 'REFLECT', 'SIGNAL', 'BOTH'):
        print(arm, 'clean', c[(arm, True)], 'regressed', c[(arm, False)], 'not checked', c[(arm, None)])


if __name__ == '__main__':
    main()
