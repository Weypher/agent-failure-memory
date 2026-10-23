"""M1 DEV pilot stage entry. Exposed DEV tasks only; no main-study or holdout permission.

Per task (manifest order): attempt 1 -> if a natural failure (complete attempt, valid
final evaluation, tests failing): one structured lesson request, deterministic signals,
then one retry per arm in a task-rotated order. Any provider, budget, interface or
infrastructure problem stops the stage for review; nothing is retried automatically.
Usage: python3 m1/run_dev.py --scope <scope.json> --receipt <receipt.json>
"""
import argparse, json, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / 'runner', ROOT / 'm1'):
    sys.path.insert(0, str(p))
from repository_snapshot import restore, capture  # noqa: E402
from repository_multiturn_structured_r2 import run  # noqa: E402
from harness.artifacts import write_once  # noqa: E402
from harness.provider import load_credentials, check_model_access  # noqa: E402
from harness.structured_repository_provider import StructuredRepositoryModel  # noqa: E402
from harness.structured_reflection_provider import StructuredReflectionModel, lesson_input_bound  # noqa: E402
from harness.stage_budget import StageBudgetLedger  # noqa: E402
import memory, stage, integrity  # noqa: E402
from debug_pipeline import modified  # noqa: E402

PERMISSION = 'M1_DEV_PILOT_ONLY'
ROUTE = 'afm-m1-dev-r1'
LIMITS = {ROUTE: 4_000_000_000}
CODE = ['m1/run_dev.py', 'm1/stage.py', 'm1/memory.py', 'm1/prompts.py', 'm1/accuracy.py', 'm1/debug_pipeline.py', 'm1/integrity.py', 'm1/score_lessons.py',
        'runner/repository_multiturn_structured_r2.py', 'runner/repository_sandbox.py', 'runner/repository_snapshot.py',
        'runner/repository_loop.py', 'runner/repository_feedback.py', 'harness/__init__.py', 'harness/agent.py',
        'harness/artifacts.py', 'harness/budget.py', 'harness/provider.py', 'harness/stage_budget.py',
        'harness/repository_action.py', 'harness/structured_repository_provider.py',
        'harness/structured_reflection_provider.py', 'harness/test_event_score.py']


def required(scope):
    manifest = json.loads(Path(scope['manifest']).read_text())
    req = {str(ROOT / c) for c in CODE} | {scope['protocol'], scope['manifest']}
    for t in manifest['tasks']:
        req |= {t['messages_path'], t['statement_path'], t['sandbox_spec'], str(Path(t['initial_snapshot']) / 'manifest.json')}
    return req, manifest


def execute(scope_path, receipt_path):
    scope = json.loads(Path(scope_path).read_text()); req, manifest = required(scope)
    gate = lambda: stage.check(scope_path, receipt_path, PERMISSION, ROUTE, LIMITS, req, deep=False)  # noqa: E731
    deep_gate = lambda: stage.check(scope_path, receipt_path, PERMISSION, ROUTE, LIMITS, req, deep=True)  # noqa: E731
    deep_gate()
    out = Path(scope['output_root']); out.mkdir(parents=True, exist_ok=False)
    write_once(out / 'scope.json', scope); ledger = None; rows = []
    try:
        load_credentials(scope['credentials_file'])
        cfg = {'model': scope['model_config'], 'budget': scope['prices'], 'phase': 'dev'}
        access = check_model_access(cfg); write_once(out / 'access.json', access)
        if not access.get('authenticated') or access.get('model_id') != cfg['model']['id']:
            raise ValueError('Model access unavailable')
        with sqlite3.connect(Path(scope['ledger']).as_uri() + '?mode=ro', uri=True) as db:
            authorization = json.loads(db.execute('SELECT authorization FROM meta WHERE id=1').fetchone()[0])
            if db.execute('SELECT 1 FROM calls WHERE id LIKE ? LIMIT 1', (ROUTE + '/%',)).fetchone():
                raise ValueError('Stage already attempted; no automatic retry')
        if authorization['authorized_usd'] != 160:
            raise ValueError('Unexpected cumulative authorization')
        ledger = StageBudgetLedger(scope['ledger'], authorization, route=ROUTE, limits={k: v / 1e9 for k, v in LIMITS.items()})
        used = set()
        op = stage.Bounded(StructuredRepositoryModel(cfg, ledger, out / 'responses'), gate, ROUTE, used)
        lesson_model = stage.Bounded(StructuredReflectionModel(cfg, ledger, out / 'responses'), gate, ROUTE, used)
        lesson_model.share_failure(op)
        for index, task in enumerate(manifest['tasks']):
            name = task['task_id']; case = out / name.replace('/', '-'); spec = json.loads(Path(task['sandbox_spec']).read_text())
            base = json.loads(Path(task['messages_path']).read_text()); row = {'task': name}
            rows.append(row)  # in-progress task is visible in partial outcomes

            def attempt(label, messages):
                d = case / label
                deep_gate()  # runtime trees verified before every attempt; files on every call
                if restore(task['initial_snapshot'], d / 'work') != task['snapshot_identity']:
                    raise ValueError('Initial snapshot differs')
                op.begin(f'{ROUTE}/{name}/{label}', 8)
                result = run(model=op, messages=messages, call_prefix=f'{ROUTE}/{name}/{label}', spec=spec, workspace=d / 'work',
                             test_command=task['test_command'], expected_tests=task['expected_tests'],
                             test_hashes=task['test_hashes'], arm='RAW', output=d / 'attempt')
                op.end(); capture(d / 'work', d / 'final-snapshot')
                result['modified_files'] = modified(task['initial_snapshot'], d / 'work')
                result['integrity_flags'] = integrity.flags(result['modified_files'])
                if result['integrity_flags']:
                    result['resolved'] = None  # excluded from repair outcomes; reported
                return d, result

            def stop_needed(result):
                return result['status'] not in ('declared_done', 'response_limit', 'context_limit') or \
                    (result.get('candidate_assessment') or {}).get('status') == 'invalid_execution'

            d1, first = attempt('A1', base)
            row['A1'] = first
            if stop_needed(first):
                write_once(out / 'stopped.json', {'status': 'STOPPED_FOR_REVIEW', 'task': name, 'at': 'A1'}); break
            if first['resolved'] is not False:  # resolved, censored (context_limit) or integrity-flagged: no retries
                row['retries'] = 'not_applicable'; continue
            evaluation = json.loads((d1 / 'attempt' / 'evaluation.json').read_text())
            final_raw = (d1 / 'attempt' / 'evaluation' / 'raw.log').read_text(errors='replace')
            transcript = json.loads((d1 / 'attempt' / 'transcript.json').read_text())
            sig = memory.signals(evaluation, final_raw, first['modified_files'])
            request = memory.reflection_messages(Path(task['statement_path']).read_bytes().decode(), transcript, final_raw)
            write_once(case / 'signals.json', sig); write_once(case / 'reflection-request.json', request)
            bound = lesson_input_bound(request)
            if bound > scope['model_config']['input_token_limit']:
                # Frozen rule: an over-bound lesson request is not sent; the lesson is censored
                # (lesson_context_limit) and only arms that need no lesson run for this task.
                lesson = None; row['lesson'] = {'status': 'lesson_context_limit', 'input_upper_bound': bound}
                arms_available = ('NONE', 'SIGNAL')
            else:
                lesson_model.begin(f'{ROUTE}/{name}/LESSON', 1)
                lesson = json.loads(lesson_model.complete(request, f'{ROUTE}/{name}/LESSON/1').text)
                lesson_model.end(); write_once(case / 'lesson.json', lesson)
                row['lesson'] = {'status': 'valid', 'input_upper_bound': bound}; arms_available = memory.ARMS
            rotated = list(memory.ARMS[index % 4:] + memory.ARMS[:index % 4])
            arms = [a for a in rotated if a in arms_available]
            row['retry_order'] = arms; row['retries'] = {}
            for arm in arms:
                messages = memory.retry_messages(base, arm, lesson, sig)
                write_once(case / f'R-{arm}.messages.json', messages)
                _, result = attempt(f'R-{arm}', messages)
                row['retries'][arm] = result
                if stop_needed(result):
                    write_once(out / 'stopped.json', {'status': 'STOPPED_FOR_REVIEW', 'task': name, 'at': arm}); break
            if (out / 'stopped.json').exists():
                break
        else:
            write_once(out / 'complete.json', {'status': 'DEV_COMPLETE_REQUIRES_RECONCILIATION_NOT_MAIN_PERMISSION',
                                               'tasks': len(rows)})
        write_once(out / 'outcomes.json', rows)
    except BaseException as error:
        write_once(out / 'outcomes-partial.json', rows)
        write_once(out / 'entry-error.json', {'status': 'STOPPED_FOR_REVIEW', 'type': type(error).__name__})
        raise
    finally:
        if ledger is not None:
            ledger.db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--scope', required=True); parser.add_argument('--receipt', required=True)
    args = parser.parse_args(); execute(args.scope, args.receipt)
