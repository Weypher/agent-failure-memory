"""Freeze main-stage inputs from the eligible pool. No model requests.

Eligible = statement-eligible AND sandbox-control-eligible AND initial prompt bound <= 32000,
minus DEV tasks and old-project exposed tasks (planning/main-eligibility-rules-r1.json).
No sampling: every eligible task enters. Writes base prompts, statements, pinned references.
Usage: python3 m1/prepare_main.py <statements summary> <sandbox summary>[,<sandbox summary>...]
       <dev manifest> <out_dir> <first_attempt_repetitions> <workers>
"""
import hashlib, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / 'runner', ROOT / 'm1'):
    sys.path.insert(0, str(p))
from repository_snapshot import restore  # noqa: E402
from repository_sandbox import execute  # noqa: E402
from repository_feedback import render  # noqa: E402
from harness.artifacts import digest  # noqa: E402
from harness.repository_action import input_upper_bound  # noqa: E402
import memory  # noqa: E402
from prompts import SYSTEM  # noqa: E402

MAX_INITIAL_BOUND = 32000
OLD_EXPOSED = {'fastapi/1', 'tqdm/1', 'matplotlib/1'}


def main(statements, sandboxes, dev_manifest, out, reps, workers):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    st = {r['task']: r for r in json.loads(Path(statements).read_text())['rows']}
    sb = {}
    for path in sandboxes.split(','):
        for r in json.loads(Path(path).read_text())['rows']:
            sb[r['task']] = dict(r, _dir=str(Path(path).parent / r['task'].replace('/', '-')))
    dev = {t['task_id'] for t in json.loads(Path(dev_manifest).read_text())['tasks']}
    excluded = sorted(dev | OLD_EXPOSED)
    pool = sorted(t for t in st if st[t]['eligible'] and sb.get(t, {}).get('eligible') and t not in dev and t not in OLD_EXPOSED)
    tasks, skipped = [], []
    for name in pool:
        task = json.loads((Path(sb[name]['_dir']) / 'task' / 'task.json').read_text())
        spec = json.loads(Path(task['sandbox_spec']).read_text())
        statement = (Path(statements).parent / 'tasks' / (name.replace('/', '-') + '.txt')).read_bytes().decode()
        assert hashlib.sha256(statement.encode()).hexdigest() == st[name]['statement_sha256']
        init = out / 'initial' / name.replace('/', '-'); restore(task['initial_snapshot'], init / 'work')
        record = execute(spec, init / 'work', init / 'output', task['test_command'], timeout=300)
        messages = memory.initial_messages(SYSTEM, statement, render((init / 'output' / 'raw.log').read_text(errors='replace'), record, 'RAW'))
        if input_upper_bound(messages) > MAX_INITIAL_BOUND:
            skipped.append({'task': name, 'initial_bound': input_upper_bound(messages)}); continue
        stem = name.replace('/', '-')
        (out / f'{stem}.messages.json').write_text(json.dumps(messages, indent=1) + '\n')
        (out / f'{stem}.statement.txt').write_bytes(statement.encode())
        fixed = Path(task['fixed_checkout']); buggy = fixed.parent / 'buggy'
        for side, src in (('buggy', buggy), ('fixed', fixed)):
            for rel in sb[name]['reference_changed']:
                if (src / rel).is_file():
                    dest = out / 'reference' / stem / side / rel; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src / rel, dest)
        tasks.append(dict(task, statement_sha256=st[name]['statement_sha256'], messages_path=str(out / f'{stem}.messages.json'),
                          statement_path=str(out / f'{stem}.statement.txt'), messages_sha256=digest(messages),
                          input_upper_bound=input_upper_bound(messages), reference_changed=sb[name]['reference_changed']))
    manifest = {'status': 'MAIN_INPUTS_FROZEN_FOR_REVIEW', 'first_attempt_repetitions': int(reps), 'workers': int(workers),
                'arms': list(memory.ARMS), 'excluded': excluded, 'skipped_over_bound': skipped, 'eligible_pool': len(pool),
                'tasks': tasks, 'paid_calls': 0}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    print(json.dumps({'tasks': len(tasks), 'skipped_over_bound': len(skipped), 'pool': len(pool)}))


if __name__ == '__main__':
    main(*sys.argv[1:7])
