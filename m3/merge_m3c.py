"""Combine the gpt54 part (completed tasks only) with its resume gpt54c, then run the frozen M3 analysis on the
frozen 85-failure gpt54 list. Written and hashed (planning/m3-notice/merge-frozen.json) before any gpt-5.4 M3
outcome was read. Rows of stopped/errored gpt54 tasks are never opened beyond their status.

  python3 m3/merge_m3.py <out.json>
"""
import json, sqlite3, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = Path('/runtime/m3-paid-r1')
FROZEN = Path('/runtime/m3-inputs-r2/failures-gpt54.json')
RESUME = Path('/runtime/m3-inputs-r4/failures-gpt54c.json')
LEDGER = '/ledger-project/runs/budget.sqlite'


def main(out_path):
    frozen = {f['task'] for f in json.loads(FROZEN.read_text())['failures']}
    resume = {f['task'] for f in json.loads(RESUME.read_text())['failures']}
    kept = set(json.loads(RESUME.read_text())['completed_tasks_kept'])
    assert not (kept & resume) and kept | resume == frozen, 'tasks must partition the frozen gpt54 list'
    tmp = Path(tempfile.mkdtemp(prefix='m3merge-')) / 'tasks'; tmp.mkdir()
    for task in sorted(frozen):
        key = task.replace('/', '-')
        src = RUN / ('gpt54' if task in kept else 'gpt54c') / 'tasks' / key / 'row.json'
        if task in kept and json.loads(src.read_text())['status'] != 'complete':
            raise SystemExit(f'kept task not complete: {task}')
        (tmp / key).mkdir(); (tmp / key / 'row.json').symlink_to(src)
    subprocess.run([sys.executable, str(ROOT / 'm3/analyze_m3.py'), str(tmp.parent), str(FROZEN), out_path], check=True)
    out = json.loads(Path(out_path).read_text())
    db = sqlite3.connect(Path(LEDGER).as_uri() + '?mode=ro', uri=True)
    cost = {r: db.execute('SELECT COUNT(*), COALESCE(SUM(charged),0)/1e9 FROM calls WHERE id LIKE ?', (f'afm-m3-notice/{r}/%',)).fetchone()
            for r in ('gpt54', 'gpt54c')}
    db.close()
    out.update(ledger_calls_and_usd={'gpt54 (incl. discarded partial work)': cost['gpt54'], 'gpt54c': cost['gpt54c']},
               sessions={'gpt54': {'tasks': len(kept), 'note': 'stopped on HTTP 520; completed tasks kept'},
                         'gpt54c': {'tasks': len(resume), 'note': 'resume of uncompleted tasks'}},
               protocol=['planning/m3-notice/review-r2/gpt54/protocol.json', 'planning/m3-notice/review-r4/gpt54c/protocol.json (resume addenda supersede "no resume"; second resume after gpt54b stopped)'])
    Path(out_path).write_text(json.dumps(out, indent=1) + '\n')


if __name__ == '__main__':
    main(sys.argv[1])
