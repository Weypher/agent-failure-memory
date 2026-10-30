"""Build the frozen M3 failure lists from the M1 combined table and the M2 rows. No model calls.

  python3 m3/build_failures.py <out_dir>   -> failures-mini.json, failures-gpt54.json

Each entry: task, rep, source, and the stored HISTORY and REFLECT retry prompts of that failure with sha256.
"""
import hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'm1'))
from fullsuite import BATCH_RUN, FINAL, RUNTIME  # noqa: E402


def entry(task, rep, source, fdir):
    out = {'task': task, 'rep': rep, 'source': source}
    for arm in ('HISTORY', 'REFLECT'):
        p = fdir / f'R-{arm}.messages.json'
        if not p.exists():
            raise SystemExit(f'missing {p}')
        out[f'{arm.lower()}_messages'] = str(p); out[f'{arm.lower()}_sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def main(out_dir):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=False)
    c = json.loads((FINAL / 'C.json').read_text()); run_of = {}
    for src in c['sources']:
        for a in json.loads((ROOT / src['path']).read_text())['first_attempts']:
            run_of[a['task']] = BATCH_RUN[Path(src['path']).stem]
    mini = [entry(i['task'], i['rep'], f"M1/{run_of[i['task']]}",
                  RUNTIME / f"m1-main-paid-{run_of[i['task']]}/tasks/{i['task'].replace('/', '-')}/F-{i['rep']}") for i in c['instances']]
    g = []
    for p in sorted((RUNTIME / 'm2-paid-r1/main/tasks').glob('*/row.json')):
        row = json.loads(p.read_text())
        for e in row['attempts']:
            if e['A1']['m1_outcome'] == 'unresolved':
                g.append(entry(row['task'], e['rep'], 'M2/main', p.parent / f"F-{e['rep']}"))
    for name, lst in (('mini', mini), ('gpt54', g)):
        assert len({(x['task'], x['rep']) for x in lst}) == len(lst)
        (out / f'failures-{name}.json').write_text(json.dumps({'part': name, 'failures': lst}, indent=1) + '\n')
        print(name, len(lst), len({x['task'] for x in lst}))


if __name__ == '__main__':
    main(sys.argv[1])
