"""Build the per-failure-instance table from a main-stage output. Evaluation-only, read-only ledger.

One row per natural failure instance (task, rep): lesson location accuracy (lenient/strict),
deterministic baselines (attempt edits, traceback frames), rule-based claim checks, and per
arm retry outcome, cost (ledger charges of the arm's calls) and wall-clock seconds.
Usage: python3 m1/score_main.py <scope.json> <out.json>
"""
import json, sqlite3, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / 'runner'))
from repository_snapshot import restore  # noqa: E402
import accuracy, claims, memory  # noqa: E402


KEPT = None  # set via --kept <kept-tasks.json>


def main(scope_path, target):
    scope = json.loads(Path(scope_path).read_text()); out = Path(scope['output_root'])
    manifest = json.loads(Path(scope['manifest']).read_text()); ref_root = Path(scope['manifest']).parent / 'reference'
    with sqlite3.connect(Path(scope['ledger']).as_uri() + '?mode=ro', uri=True) as db:
        charges = dict(db.execute('SELECT id, charged FROM calls WHERE id LIKE ?', (scope['route'] + '/%',)).fetchall())

    def cost(prefix):
        return sum(v or 0 for k, v in charges.items() if k.startswith(prefix + '/'))

    first_attempts, instances = [], []
    for t in manifest['tasks']:
        name = t['task_id']; case = out / 'tasks' / name.replace('/', '-')
        row_path = case / 'row.json'
        if KEPT is not None and name in KEPT and not row_path.exists():
            raise SystemExit(f'Kept task {name} has no row')
        if not row_path.exists():
            continue
        row = json.loads(row_path.read_text())
        if KEPT is not None and name not in KEPT:
            continue  # resumed design: only the frozen kept list of a stopped route is scored
        if KEPT is not None and row.get('status') != 'complete':
            raise SystemExit(f'Kept task {name} is not complete')
        spec = json.loads(Path(t['sandbox_spec']).read_text()); protected = spec['protected_paths']
        ref_dir = ref_root / name.replace('/', '-')
        reference = accuracy.reference_locations(ref_dir / 'buggy', ref_dir / 'fixed', t['reference_changed'])
        initial = Path(tempfile.mkdtemp(prefix='m1-initial-')) / 'work'
        restore(t['initial_snapshot'], initial)  # hash-pinned buggy state for the attempt baseline
        for entry in row['attempts']:
            if 'A1' not in entry:  # attempt not started (stage stopped): missing, not an outcome
                continue
            a1 = entry['A1']; rep = entry['rep']
            first_attempts.append({'task': name, 'rep': rep, 'status': a1['status'], 'outcome': a1.get('m1_outcome'),
                                   'subtype': a1.get('m1_subtype'), 'resolved': a1['resolved'],
                                   'integrity_flags': a1.get('integrity_flags'), 'responses': a1['responses'],
                                   'seconds': a1['elapsed_seconds'], 'cost_nusd': cost(f"{scope['route']}/{name}/A1-{rep}")})
            if 'retries' not in entry and 'lesson' not in entry:
                continue
            fdir = case / f'F-{rep}'
            record = json.loads((fdir / 'record.json').read_text())['record']; sig = json.loads((fdir / 'signals.json').read_text())
            final_raw = (case / f'A1-{rep}' / 'attempt' / 'evaluation' / 'raw.log').read_text(errors='replace')
            attempt_loc = accuracy.attempt_locations(initial, case / f'A1-{rep}' / 'work',
                                                     memory.source_changes(a1['modified_files']), protected)
            trace_loc = accuracy.traceback_locations(final_raw, protected, case / f'A1-{rep}' / 'work')
            inst = {'task': name, 'rep': rep, 'a1_status': a1['status'], 'a1_subtype': a1.get('m1_subtype'),
                    'lesson_status': (entry.get('lesson') or {}).get('status', 'not_reached'),
                    'baseline_attempt': accuracy.score_locations(attempt_loc['files'], attempt_loc['functions'], reference),
                    'baseline_traceback': accuracy.score_locations(trace_loc['files'], trace_loc['functions'], reference),
                    'arms': {}}
            if (fdir / 'lesson.json').exists():
                lesson = json.loads((fdir / 'lesson.json').read_text())
                inst['lesson'] = accuracy.score_lesson(lesson, reference)
                inst['claims'] = claims.check(lesson, record, t['expected_tests'])
            lesson_cost = cost(f"{scope['route']}/{name}/LESSON-{rep}")
            for arm, r in (entry.get('retries') or {}).items():
                arm_cost = cost(f"{scope['route']}/{name}/R{rep}-{arm}") + (lesson_cost if arm in ('REFLECT', 'BOTH') else 0)
                inst['arms'][arm] = {'status': r['status'], 'outcome': r.get('m1_outcome'), 'subtype': r.get('m1_subtype'),
                                     'resolved': r['resolved'], 'responses': r['responses'],
                                     'integrity_flags': r.get('integrity_flags'),
                                     'seconds': r['elapsed_seconds'] + ((entry.get('lesson') or {}).get('seconds', 0) if arm in ('REFLECT', 'BOTH') else 0),
                                     'cost_nusd': arm_cost}  # REFLECT/BOTH include the lesson call
            inst['lesson_cost_nusd'] = lesson_cost
            instances.append(inst)
    Path(target).write_text(json.dumps({'kind': 'MAIN_INSTANCE_TABLE', 'first_attempts': first_attempts,
                                        'instances': instances}, indent=1) + '\n')
    print(len(first_attempts), 'first attempts;', len(instances), 'failure instances')


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--kept' in args:
        i = args.index('--kept'); KEPT = set(json.loads(Path(args[i + 1]).read_text())); del args[i:i + 2]
    main(args[0], args[1])
