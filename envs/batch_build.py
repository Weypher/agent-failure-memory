"""Run trial_build.py for every out-of-frame bug of the given projects. No model requests.

Excludes the previous project's 120-task prospective frame and its exposed DEV tasks so
that no buggy/fixed controls run on them. Existing results are skipped, never rebuilt.
Usage: python3 envs/batch_build.py <summary.json> <project> [<project> ...]
"""
import json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD_FRAME = Path('/work/budgted_gate_fail_learning/code/planning/'
                 'topic-screen-after-atomicity-20261014/environment-attribution-r1/prospective-task-frame-120-r1.json')
OLD_EXPOSED = {('fastapi', '1'), ('tqdm', '1'), ('matplotlib', '1')}
sys.path.insert(0, str(HERE))
import trial_build  # noqa: E402


def main(summary, projects, include_frame=False):
    # include_frame: screen the old project's never-exposed frame tasks too (after the
    # eligibility freeze in planning/main-eligibility-rules-r1.json).
    frame = set() if include_frame else {(t['project'], t['task_id']) for t in json.loads(OLD_FRAME.read_text())['tasks']}
    rows = []
    for project in projects:
        bugs = sorted((p.name for p in (trial_build.BUGSINPY / 'projects' / project / 'bugs').iterdir()
                       if p.name.isdigit()), key=int)
        for bug in bugs:
            if (project, bug) in frame or (project, bug) in OLD_EXPOSED:
                continue
            result = trial_build.RUNTIME / trial_build.ROUND / f'{project}-{bug}' / 'result.json'
            if not result.exists():
                try:
                    subprocess.run([sys.executable, str(HERE / 'trial_build.py'), project, bug],
                                   timeout=2400, capture_output=True)
                except subprocess.TimeoutExpired:
                    pass
            if result.exists():
                d = json.loads(result.read_text())
                failed = next((s for s in d['steps'] if s.get('status') in
                               ('download_failed', 'install_failed', 'toolchain_failed', 'failed', 'ssl_missing')), None)
                rows.append({'task': f'{project}/{bug}', 'outcome': d['outcome'],
                             'failed_step': failed and {k: v for k, v in failed.items() if k != 'log'},
                             'error': next((s for s in d['steps'] if s['step'] == 'error'), None)})
            else:
                rows.append({'task': f'{project}/{bug}', 'outcome': 'timeout_or_crash'})
            Path(summary).write_text(json.dumps({'kind': 'FREE_ENVIRONMENT_BATCH_NOT_EXPERIMENT',
                                                 'round': trial_build.ROUND, 'rows': rows, 'model_calls': 0}, indent=2) + '\n')
    counts = {}
    for r in rows:
        counts[r['outcome']] = counts.get(r['outcome'], 0) + 1
    print(json.dumps(counts))


if __name__ == '__main__':
    args = sys.argv[1:]; include = '--include-frame' in args; args = [a for a in args if a != '--include-frame']
    main(args[0], args[1:], include)
