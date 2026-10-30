"""Predeclared analysis of M3, the notice-only control (planning/m3-notice/review-r2/<part>/protocol.json). No model calls.

  python3 m3/analyze_m3.py <output_root>/<part> <failures-part.json> <out.json> [ledger.sqlite]

Per part (model), failures as units, tasks as clusters:
  primary family (modified at least one .py file): NOTICE - NONE, HISTORY - NOTICE, REFLECT - NOTICE; Holm over the
  three, two-sided, alpha 0.05; secondary family (resolved on the selected tests): the same three contrasts;
  decision table (protocol): N1 supported iff NOTICE - NONE > 0 with p_Holm < 0.05; a content effect (X - NOTICE) is
  'positive' iff > 0 with p_Holm < 0.05, 'equivalent within 10 points' iff its 95% CI lies inside [-0.10, +0.10],
  'negative' iff < 0 with p_Holm < 0.05, otherwise 'not distinguished';
  descriptive: X - NONE for both outcomes and 'declared done without an edit', rates, endings, missing retries, cost.
"""
import json, random, sqlite3, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'm1'))
from analyze_main import estimate, holm, paired, proportion  # noqa: E402

SEED = 20261030
ARMS = ['NONE', 'NOTICE', 'HISTORY', 'REFLECT']
CONTRASTS = [('NOTICE', 'NONE'), ('HISTORY', 'NOTICE'), ('REFLECT', 'NOTICE')]
ALPHA, MARGIN = 0.05, 0.10


def load(run_root, failures_path):
    frozen = {(f['task'], f['rep']) for f in json.loads(Path(failures_path).read_text())['failures']}
    instances, statuses, seen, missing = [], Counter(), set(), Counter()
    for task in sorted({t for t, _ in frozen}):
        row_path = Path(run_root) / 'tasks' / task.replace('/', '-') / 'row.json'
        if not row_path.exists():
            statuses['missing_row'] += 1; continue
        row = json.loads(row_path.read_text()); statuses[row.get('status')] += 1
        if row.get('status') != 'complete':
            continue  # partial rule: completed tasks only
        for e in row['attempts']:
            key = (task, e['rep'])
            if key not in frozen or key in seen:
                raise SystemExit(f'row does not match the frozen failure list: {key}')
            seen.add(key)
            arms = {}
            for arm in ARMS:
                r = (e.get('retries') or {}).get(arm)
                if r is None or r['m1_outcome'] == 'harness':
                    missing[arm] += 1; continue
                edits = [f for f in r.get('modified_files') or [] if f.endswith('.py')]
                arms[arm] = {'resolved': 1.0 if r['m1_outcome'] == 'resolved' else 0.0, 'edited': 1.0 if edits else 0.0,
                             'done_no_edit': 1.0 if (r['status'] == 'declared_done' and not edits) else 0.0,
                             'subtype': r['m1_subtype']}
            instances.append({'task': task, 'rep': e['rep'], 'arms': arms})
    not_analysed = sorted(frozen - seen)
    return instances, statuses, missing, not_analysed, len(frozen)


def classify(r, directional):
    lo, hi = r['ci95']; sig = r.get('p_holm', 1) < ALPHA
    if sig and r['difference'] > 0:
        return 'supported' if directional else 'positive'
    if sig and r['difference'] < 0:
        return 'opposite' if directional else 'negative'
    if not directional and -MARGIN <= lo and hi <= MARGIN:
        return 'equivalent within 10 points'
    return 'not supported' if directional else 'not distinguished'


def main(run_root, failures_path, out_path, ledger=None):
    instances, statuses, missing, not_analysed, expected = load(run_root, failures_path)
    rng = random.Random(SEED)
    get = lambda key: (lambda a: a[key])  # noqa: E731

    def family(key):
        return holm([dict(estimate(paired(instances, a, b, get(key)), rng), contrast=f'{a}-{b} {key}') for a, b in CONTRASTS])
    primary = family('edited'); secondary = family('resolved')
    decisions = {r['contrast']: classify(r, r['contrast'].startswith('NOTICE-NONE')) for r in primary + secondary}
    descriptive = [dict(estimate(paired(instances, a, 'NONE', get(key)), rng), contrast=f'descriptive {a}-NONE {key}')
                   for a in ('NOTICE', 'HISTORY', 'REFLECT') for key in ('edited', 'resolved', 'done_no_edit')]
    rates = {arm: {key: proportion(instances, lambda i, a=arm, k=key: i['arms'][a][k] if a in i['arms'] else None, rng).get('difference')
                   for key in ('edited', 'resolved', 'done_no_edit')} for arm in ARMS}
    endings = {arm: dict(Counter(i['arms'][arm]['subtype'] for i in instances if arm in i['arms'])) for arm in ARMS}
    cost = None
    if ledger:
        db = sqlite3.connect(Path(ledger).as_uri() + '?mode=ro', uri=True)
        route = 'afm-m3-notice/' + json.loads(Path(failures_path).read_text())['part']
        cost = db.execute('SELECT COUNT(*), COALESCE(SUM(charged),0)/1e9 FROM calls WHERE id LIKE ?', (route + '/%',)).fetchone()
        db.close()
    part = json.loads(Path(failures_path).read_text())['part']
    out = {'kind': 'M3_PREDECLARED_ANALYSIS', 'seed': SEED, 'protocol': f'planning/m3-notice/review-r2/{part}/protocol.json',
           'row_status': dict(statuses), 'partial': bool(not_analysed), 'failures_expected': expected,
           'failures_analysed': len(instances), 'failures_not_analysed': not_analysed, 'missing_retries': dict(missing),
           'primary_edited': primary, 'secondary_resolved': secondary, 'decisions': decisions,
           'descriptive_vs_none': descriptive, 'rates': rates, 'retry_endings': endings, 'ledger_calls_and_usd': cost}
    Path(out_path).write_text(json.dumps(out, indent=1) + '\n')
    for r in primary + secondary + descriptive:
        if 'difference' in r:
            print(r['contrast'], f"{100 * r['difference']:+.1f} [{100 * r['ci95'][0]:+.1f}, {100 * r['ci95'][1]:+.1f}]",
                  'pHolm', r.get('p_holm'), decisions.get(r['contrast'], ''))
    print(json.dumps(rates))


if __name__ == '__main__':
    main(*sys.argv[1:5])
