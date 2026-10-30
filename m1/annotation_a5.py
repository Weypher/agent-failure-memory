"""A5: stratified sample of lessons for blind human annotation of diagnosis correctness (post hoc).

  python3 m1/annotation_a5.py build   -> planning/annotation-a5/ (items.md, sheet-A.csv, sheet-B.csv, key.json)
  python3 m1/annotation_a5.py score   -> agreement (Cohen's kappa) and rates, after both sheets are filled

Annotators see the issue, the failed attempt's diff and test errors, the lesson, and the reference diff.
They do not see any retry outcome. Item order is random (seeded) and identical for both annotators.
"""
import csv, difflib, json, random, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fullsuite import BATCH_RUN, FINAL, ROOT, RUNTIME

OUT = ROOT / 'planning/annotation-a5'
N, SEED = 60, 20261028
FIELDS = ['item', 'q1_root_cause', 'q2_what_failed', 'q3_next_steps', 'note']


def _sources():
    combined = json.loads((FINAL / 'C.json').read_text())
    run_of = {}
    for source in combined['sources']:
        for a in json.loads((ROOT / source['path']).read_text())['first_attempts']:
            run_of[a['task']] = BATCH_RUN[Path(source['path']).stem]
    entries = {}
    for run in set(run_of.values()):
        scope = json.loads((RUNTIME / f'm1-main-paid-{run}/scope.json').read_text())
        manifest = Path(scope['manifest'])
        for t in json.loads(manifest.read_text())['tasks']:
            if run_of.get(t['task_id']) == run:
                entries[t['task_id']] = dict(t, run=run, reference_dir=str(manifest.parent / 'reference' / t['task_id'].replace('/', '-')))
    return combined['instances'], entries


def sample(instances):
    by_p = defaultdict(list)
    for i in instances:
        by_p[i['task'].split('/')[0]].append(i)
    total = len(instances)
    alloc = {p: max(2, int(N * len(v) / total)) for p, v in by_p.items()}
    rem = sorted(by_p, key=lambda p: -(N * len(by_p[p]) / total - int(N * len(by_p[p]) / total)))
    k = 0
    while sum(alloc.values()) < N:
        alloc[rem[k % len(rem)]] += 1; k += 1
    while sum(alloc.values()) > N:
        p = max(alloc, key=lambda q: alloc[q] - N * len(by_p[q]) / total); alloc[p] -= 1
    rng = random.Random(SEED)
    chosen = []
    for p in sorted(by_p):
        pool = sorted(by_p[p], key=lambda i: (i['task'], i['rep']))
        chosen += rng.sample(pool, min(alloc[p], len(pool)))
    rng.shuffle(chosen)
    return chosen, alloc


def _diff(a_root, b_root, files, limit=160):
    out = []
    for f in files:
        a, b = Path(a_root) / f, Path(b_root) / f
        old = a.read_text(errors='replace').splitlines() if a.exists() else []
        new = b.read_text(errors='replace').splitlines() if b.exists() else []
        out += list(difflib.unified_diff(old, new, f'a/{f}', f'b/{f}', lineterm='', n=3))
    if len(out) > limit:
        out = out[:limit] + [f'[... {len(out) - limit} more diff lines omitted]']
    return '\n'.join(out) or '(no source changes)'


def _clip(text, limit):
    return text if len(text) <= limit else text[:limit] + f'\n[... {len(text) - limit} characters omitted]'


def build():
    instances, entries = _sources()
    chosen, alloc = sample(instances)
    OUT.mkdir(parents=True, exist_ok=True)
    md = [Path(OUT / 'guidelines.md').read_text() if (OUT / 'guidelines.md').exists() else '', '\n---\n']
    key = []
    for n, inst in enumerate(chosen, 1):
        task, rep = inst['task'], inst['rep']
        e = entries[task]
        tdir = RUNTIME / f"m1-main-paid-{e['run']}/tasks/{task.replace('/', '-')}"
        lesson = json.loads((tdir / f'F-{rep}' / 'lesson.json').read_text())
        signals = json.loads((tdir / f'F-{rep}' / 'signals.json').read_text())
        buggy = Path(e['fixed_checkout']).parent / 'buggy'
        a1_files = [f for f in signals.get('modified_files') or [] if f.endswith('.py')]
        ref_files = inst['lesson']['reference_files']
        statement = Path(e['statement_path']).read_text(errors='replace')
        item = f'item-{n:02d}'
        key.append({'item': item, 'task': task, 'rep': rep})
        md += [f'\n## {item}\n',
               '### Issue (task statement)\n', '```text', _clip(statement, 3000), '```',
               '### Failed attempt: files it changed and its diff\n', '```diff', _diff(buggy, tdir / f'A1-{rep}' / 'work', a1_files), '```',
               '### Failed attempt: final test status and error lines\n', '```text',
               f"status: {signals.get('status')}\nfailing: " + '; '.join(signals.get('failing') or []) + '\n' +
               _clip('\n'.join(signals.get('error_lines') or []), 2500), '```',
               '### Lesson written by the model\n',
               f"- **Suspected files:** {', '.join(lesson.get('suspected_files') or [])}",
               f"- **Suspected functions:** {', '.join(lesson.get('suspected_functions') or [])}",
               f"- **Root cause:** {lesson.get('root_cause')}",
               f"- **What failed:** {lesson.get('what_failed')}",
               '- **Next steps:**'] + [f'  - {s}' for s in lesson.get('next_steps') or []] + [
               '\n### Reference fix (source files only)\n', '```diff', _diff(Path(e['reference_dir']) / 'buggy', Path(e['reference_dir']) / 'fixed', ref_files), '```']
    (OUT / 'items.md').write_text('\n'.join(md) + '\n')
    for who in ('A', 'B'):
        with open(OUT / f'sheet-{who}.csv', 'w', newline='') as f:
            w = csv.writer(f); w.writerow(FIELDS)
            for k in key:
                w.writerow([k['item'], '', '', '', ''])
    (OUT / 'key.json').write_text(json.dumps({'seed': SEED, 'allocation': alloc, 'items': key}, indent=1) + '\n')
    print('items', len(key), 'allocation', alloc)


def _kappa(a, b):
    n = len(a); cats = sorted(set(a) | set(b))
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[c] * cb[c] for c in cats) / n / n
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def score():
    rows = {}
    for who in ('A', 'B'):
        with open(OUT / f'sheet-{who}.csv') as f:
            rows[who] = {r['item']: r for r in csv.DictReader(f)}
    out = {}
    for q in FIELDS[1:4]:
        items = [i for i in rows['A'] if rows['A'][i][q].strip() and rows['B'].get(i, {}).get(q, '').strip()]
        a = [rows['A'][i][q].strip().lower() for i in items]; b = [rows['B'][i][q].strip().lower() for i in items]
        out[q] = {'n': len(items), 'kappa': _kappa(a, b) if items else None, 'agreement': sum(x == y for x, y in zip(a, b)) / len(items) if items else None,
                  'A': dict(Counter(a)), 'B': dict(Counter(b))}
    (OUT / 'agreement.json').write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    {'build': build, 'score': score}[sys.argv[1]]()
