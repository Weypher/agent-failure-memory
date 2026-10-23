"""Sandboxed, event-scored controls for candidate tasks. No model requests.

For each task: buggy snapshot must score test_failures_observed; buggy snapshot plus the
reference fix's non-protected file changes must score resolved. Each control runs REPEATS
times to detect nondeterminism. Results are written once per round; reruns resume.
Usage: python3 envs/sandbox_controls.py <tasks.json> <out_dir>
"""
import filecmp, json, shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT / 'runner')); sys.path.insert(0, str(ROOT))
import task_spec  # noqa: E402
from repository_snapshot import restore  # noqa: E402
from repository_sandbox import execute  # noqa: E402
from repository_multiturn_structured_r2 import hashes  # noqa: E402
from harness.test_event_score import score  # noqa: E402

REPEATS = 2


def apply_reference(work, fixed, protected):
    """Copy non-protected differences fixed -> work (adds, edits, deletes); returns changed paths."""
    def guarded(rel):
        return any(rel == p or rel.startswith(p.rstrip('/') + '/') for p in protected)
    changed = []
    fixed_files = {p.relative_to(fixed).as_posix() for p in fixed.rglob('*') if p.is_file()}
    work_files = {p.relative_to(work).as_posix() for p in work.rglob('*') if p.is_file()}
    for rel in sorted(fixed_files | work_files):
        if guarded(rel) or '__pycache__' in rel:
            continue
        src, dst = fixed / rel, work / rel
        if rel not in fixed_files:
            dst.unlink(); changed.append(rel)
        elif rel not in work_files or not filecmp.cmp(src, dst, shallow=False):
            dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, dst); changed.append(rel)
    return changed


def run_control(task, spec, work, output):
    record = execute(spec, work, output, task['test_command'], timeout=300)
    for key in ('events', 'lifecycle'):
        try:
            record[key] = json.loads((output / (key + '.json')).read_text())
        except (OSError, ValueError):
            record[key] = None
    return score(record, task['expected_tests'], task['test_hashes'], hashes(work, task['test_hashes']))


def main(tasks_file, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True); rows = []
    summary_path = out / 'summary.json'
    done = {r['task']: r for r in json.loads(summary_path.read_text())['rows']} if summary_path.exists() else {}
    for name in json.loads(Path(tasks_file).read_text())['tasks']:
        if name in done:
            rows.append(done[name]); continue
        base = out / name.replace('/', '-'); row = {'task': name}
        try:
            task = task_spec.build(name, base / 'task')
            spec = json.loads(Path(task['sandbox_spec']).read_text())
            results = {'buggy': [], 'reference': []}
            for i in range(REPEATS):
                for label in ('buggy', 'reference'):
                    work = base / f'{label}-{i}' / 'work'; output = base / f'{label}-{i}' / 'output'
                    restore(task['initial_snapshot'], work); output.parent.mkdir(exist_ok=True)
                    if label == 'reference':
                        row['reference_changed'] = apply_reference(work, Path(task['fixed_checkout']), spec['protected_paths'])
                    assessment = run_control(task, spec, work, output)
                    results[label].append(assessment['status'] if assessment['status'] != 'invalid_execution'
                                          else 'invalid:' + assessment['reason'])
            ok = all(s == 'test_failures_observed' for s in results['buggy']) and all(s == 'resolved' for s in results['reference'])
            row.update(controls=results, eligible=ok, tests=task['expected_tests'], protected=spec['protected_paths'])
        except Exception as error:  # recorded, never retried automatically
            row.update(eligible=False, error=f'{type(error).__name__}: {str(error)[:200]}')
        rows.append(row)
        summary_path.write_text(json.dumps({'kind': 'SANDBOX_CONTROLS_NOT_EXPERIMENT', 'repeats': REPEATS,
                                            'rows': rows, 'model_calls': 0}, indent=1) + '\n')
    print(sum(r.get('eligible', False) for r in rows), 'of', len(rows), 'eligible')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
