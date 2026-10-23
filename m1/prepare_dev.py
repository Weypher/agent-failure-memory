"""Freeze DEV pilot inputs. No model requests.

DEV tasks: the four environment-trial tasks plus SEED-drawn extra tasks, all required
to be statement-eligible and sandbox-control-eligible. For each task the initial test
output is produced once in the sandbox and the base prompt is written and hashed.
Usage: python3 m1/prepare_dev.py <statements summary> <sandbox summary> <out_dir> <n_extra>
"""
import hashlib, json, random, sys
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

TRIAL = ['youtube-dl/1', 'tornado/5', 'black/1', 'ansible/6']
SEED = 20261014
MAX_INITIAL_BOUND = 32000


def main(statements, sandbox, out, n_extra):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    st = {r['task']: r for r in json.loads(Path(statements).read_text())['rows']}
    sb_path = Path(sandbox); sb = {r['task']: r for r in json.loads(sb_path.read_text())['rows']}
    eligible = sorted(t for t in st if st[t]['eligible'] and sb.get(t, {}).get('eligible'))
    trial = [t for t in TRIAL if t in eligible]
    # Seeded order over the pool; tasks whose initial prompt exceeds MAX_INITIAL_BOUND are
    # skipped (technical, pre-outcome rule added at freeze: youtube-dl/27 initial log 74 KB).
    order = random.Random(SEED).sample(sorted(set(eligible) - set(trial)), len(set(eligible) - set(trial)))
    tasks, extra, skipped = [], [], []
    for name in trial + order:
        if name not in trial and len(extra) >= int(n_extra):
            break
        base_dir = sb_path.parent / name.replace('/', '-')
        task = json.loads((base_dir / 'task' / 'task.json').read_text())
        spec = json.loads(Path(task['sandbox_spec']).read_text())
        statement = (Path(statements).parent / 'tasks' / (name.replace('/', '-') + '.txt')).read_bytes().decode()
        assert hashlib.sha256(statement.encode()).hexdigest() == st[name]['statement_sha256']
        init = out / 'initial' / name.replace('/', '-'); restore(task['initial_snapshot'], init / 'work')
        record = execute(spec, init / 'work', init / 'output', task['test_command'], timeout=300)
        raw = (init / 'output' / 'raw.log').read_text(errors='replace')
        messages = memory.initial_messages(SYSTEM, statement, render(raw, record, 'RAW'))
        path = out / (name.replace('/', '-') + '.messages.json'); path.write_text(json.dumps(messages, indent=1) + '\n')
        statement_path = out / (name.replace('/', '-') + '.statement.txt'); statement_path.write_bytes(statement.encode())
        if input_upper_bound(messages) > MAX_INITIAL_BOUND:
            skipped.append({'task': name, 'initial_bound': input_upper_bound(messages)}); continue
        if name not in trial:
            extra.append(name)
        tasks.append(dict(task, statement_sha256=st[name]['statement_sha256'], messages_path=str(path), statement_path=str(statement_path),
                          messages_sha256=digest(messages), input_upper_bound=input_upper_bound(messages),
                          reference_changed=sb[name]['reference_changed'], initial_test_exit=record['exit']))
    manifest = {'status': 'DEV_INPUTS_FROZEN_FOR_REVIEW', 'selection': {'trial': trial, 'extra': extra, 'seed': SEED,
                'max_initial_bound': MAX_INITIAL_BOUND, 'skipped_over_bound': skipped,
                'eligible_pool': len(eligible)}, 'tasks': tasks, 'arms': list(memory.ARMS), 'paid_calls': 0}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    print(json.dumps(manifest['selection']))


if __name__ == '__main__':
    main(*sys.argv[1:5])
