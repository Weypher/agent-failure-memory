"""M2 stage entry: exploratory replication of M1 with gpt-5.4-2026-03-05 and three conditions (NONE, HISTORY,
REFLECT). Copied from m1/run_main.py (route r4 logic) with only these changes: model id, permission, routes
(smoke/main parts nested under one stage cap), cumulative authorization USD220, three arms, smoke task subset.
User authorization 2026-10-28 (ledger amendment b92baaa1...). Requires frozen scope and two fresh PASS reviews.

Original M1 docstring follows.

For each task and first-attempt repetition r: attempt A1-r; if it is a natural failure
(final evaluation not passing for any agent-caused reason, incl. broken builds, timeouts and
context limits; no integrity flag; subtype recorded), build one failure
record, one strict lesson (unless the lesson request exceeds the bound: censored), the
deterministic signal digest, and run one retry per available arm in an order rotated by
(task index + r). Harness failures are never task failures. Route r4 circuit breaker: an isolated
transient provider error (ModelError, not budget) marks only that attempt/lesson missing and the
stage continues; BREAKER such events, any budget stop, infrastructure error or harness-side invalid
evaluation stops the stage. Tasks run
in parallel worker processes on the original ledger; a stop marker written by any worker halts
all workers before their next attempt or lesson call.
Usage: python3 m1/run_main.py --scope <scope.json> --receipt <receipt.json>
"""
import argparse, json, multiprocessing, sqlite3, sys, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / 'runner', ROOT / 'm1'):
    sys.path.insert(0, str(p))
from repository_snapshot import restore, capture  # noqa: E402
from repository_multiturn_structured_r3 import run  # noqa: E402
from harness.agent import ModelOutputIncomplete, ModelError, BudgetStop  # noqa: E402
from harness.artifacts import write_once  # noqa: E402
from harness.provider import load_credentials, check_model_access  # noqa: E402
from harness.structured_repository_provider import StructuredRepositoryModel  # noqa: E402
from harness.structured_reflection_provider import StructuredReflectionModel, lesson_input_bound  # noqa: E402
from harness.stage_budget import StageBudgetLedger  # noqa: E402
import memory, stage, integrity  # noqa: E402
from debug_pipeline import modified  # noqa: E402

PERMISSION = 'M2_GPT54_ONLY'
STAGE = 'afm-m2-gpt54'
MODEL_ID = 'gpt-5.4-2026-03-05'
PART_CAPS = {'smoke': 4_000_000_000, 'main': 120_000_000_000}
ALL_LIMITS = {STAGE: 120_000_000_000, **{f'{STAGE}/{k}': v for k, v in PART_CAPS.items()}}
SMOKE_TASKS = ['thefuck/16', 'black/22']   # smoke: one repetition each; data are debug records, never analysed
ARMS = ['NONE', 'HISTORY', 'REFLECT']
PART = 'main'                               # set by --part before workers start (passed to workers explicitly)
ROUTE = f'{STAGE}/{PART}'
LIMITS = {STAGE: ALL_LIMITS[STAGE], ROUTE: ALL_LIMITS[ROUTE]}
stage.MODEL = MODEL_ID


def set_part(part):
    global PART, ROUTE, LIMITS
    PART, ROUTE = part, f'{STAGE}/{part}'
    LIMITS = {STAGE: ALL_LIMITS[STAGE], ROUTE: ALL_LIMITS[ROUTE]}
BREAKER = 3            # stage stops after this many transient provider errors (attempts/lessons marked missing)
FATAL_WAIT_S = 600     # pre-attempt wait for a persisted fatal provider state to clear
CODE = ['m1/run_main.py', 'm1/stage.py', 'm1/memory.py', 'm1/prompts.py', 'm1/integrity.py', 'm1/debug_pipeline.py',
        'runner/repository_multiturn_structured_r3.py', 'runner/repository_sandbox.py', 'runner/repository_snapshot.py',
        'runner/repository_loop.py', 'runner/repository_feedback.py', 'harness/__init__.py', 'harness/agent.py',
        'harness/artifacts.py', 'harness/budget.py', 'harness/provider.py', 'harness/stage_budget.py',
        'harness/repository_action.py', 'harness/structured_repository_provider.py',
        'harness/structured_reflection_provider.py', 'harness/test_event_score.py', 'm2/run_m2.py',
        'runner/repository_multiturn_structured_r2.py']  # imported at load time by m1/debug_pipeline.py (M2 impl review r1)


def required(scope):
    manifest = json.loads(Path(scope['manifest']).read_text())
    req = {str(ROOT / c) for c in CODE} | {scope['protocol'], scope['manifest']}
    for t in manifest['tasks']:
        req |= {t['messages_path'], t['statement_path'], t['sandbox_spec'], str(Path(t['initial_snapshot']) / 'manifest.json')}
    return req, manifest


def mark_stop(stop_file, value):
    """First writer wins; later workers keep the existing marker."""
    try:
        write_once(stop_file, value)
    except Exception:
        if not stop_file.exists():
            raise


# Outcome classification (main review r1: MV1, MAIN-IMPL-2). Only harness failures stop the
# stage; everything the agent can cause is scored by the final evaluation.
HARNESS_STATUSES = {'provider_or_budget_stop', 'infrastructure_error', 'invalid_action_stop'}
HARNESS_REASONS = {'malformed_record', 'invalid_test_manifest', 'invalid_hash_maps', 'test_integrity_unverified',
                   'unknown_timeout_status'}


TRANSIENT_HTTP = {400, 408, 409, 429, 500, 502, 503, 504}


def transient_evidence(out, ledger_db, call_id):
    """Transport-level evidence for the failing call (main review r7 MV7-1 / IMPL-R7-1). Model-caused
    errors (invalid structured output or lesson, missing content, usage, snapshot mismatch, settle errors)
    and pre-request fatal blocks have none of these and therefore stop the stage."""
    from harness.artifacts import digest
    base = str(out / 'responses' / digest(call_id))
    status = (ledger_db.execute('SELECT status FROM calls WHERE id=?', (call_id,)).fetchone() or [None])[0]
    detail = {'call_id': call_id, 'ledger_status': status}
    if Path(base + '.error.json').exists():
        e = json.loads(Path(base + '.error.json').read_text())
        detail.update(http_status=e.get('http_status'), error_code=e.get('error_code'))
        return e.get('http_status') in TRANSIENT_HTTP, detail
    if status == 'uncertain_transport_error':
        return True, detail
    if Path(base + '.pacing.json').exists() and not Path(base + '.request.json').exists():
        detail['pacing_deferral'] = True
        return True, detail
    return False, detail


def harness_kind(result, attempt_dir, out=None, ledger_db=None, call_id=None):
    """'transient' only for a runner provider error of type ModelError WITH transport evidence; else 'stop'."""
    if result['status'] != 'provider_or_budget_stop':
        return 'stop', {}
    errors = sorted((Path(attempt_dir) / 'attempt').glob('*-provider-error.json'))
    kind = json.loads(errors[-1].read_text()).get('type') if errors else None
    if kind != 'ModelError' or out is None:
        return 'stop', {'type': kind}
    ok, detail = transient_evidence(out, ledger_db, call_id)
    detail['type'] = kind
    return ('transient' if ok else 'stop'), detail


def record_event(out, stop_file, name, label, detail):
    """One file per transient provider error; BREAKER events stop the stage (counted across workers)."""
    events = out / 'harness-events'; events.mkdir(exist_ok=True)
    write_once(events / f"{name.replace('/', '-')}--{label}.json", dict(detail, task=name, at=label))
    if len(list(events.glob('*.json'))) >= BREAKER:
        mark_stop(stop_file, {'status': 'STOPPED_FOR_REVIEW', 'reason': 'circuit_breaker', 'events': BREAKER})


def classify(result):
    """harness | excluded_integrity | resolved | unresolved (agent-caused breakage/timeouts are unresolved)."""
    if result['status'] in HARNESS_STATUSES:
        return 'harness'
    assessment = result.get('candidate_assessment') or {}
    if not assessment or (assessment.get('status') == 'invalid_execution' and assessment.get('reason') in HARNESS_REASONS):
        return 'harness'
    if result.get('integrity_flags'):
        return 'excluded_integrity'
    return 'resolved' if assessment.get('status') == 'resolved' else 'unresolved'


def subtype(result):
    assessment = result.get('candidate_assessment') or {}
    return result['status'] + ('/' + assessment['reason'] if assessment.get('status') == 'invalid_execution' else '')


def task_worker(args):
    """One task end-to-end in its own process; returns its row (also written once to disk)."""
    scope_path, receipt_path, index, part = args
    set_part(part)
    scope = json.loads(Path(scope_path).read_text()); req, manifest = required(scope)
    task = manifest['tasks'][index]; name = task['task_id']
    out = Path(scope['output_root']) / PART; case = out / 'tasks' / name.replace('/', '-'); stop_file = out / 'stopped.json'
    row = {'task': name, 'attempts': []}; ledger = None
    try:
        code_files = {str(ROOT / c) for c in CODE} | {scope['protocol'], scope['manifest']}
        spec_mounts = json.loads(Path(task['sandbox_spec']).read_text())['readonly_mounts']
        task_files = code_files | {task['messages_path'], task['statement_path'], task['sandbox_spec'],
                                   str(Path(task['sandbox_spec']).parent / 'task.json'), str(Path(task['initial_snapshot']) / 'manifest.json')} \
            | {str(f) for f in (Path(task['initial_snapshot']) / 'blobs').iterdir() if f.is_file()} \
            | {h for h, _ in spec_mounts if h in scope.get('files', {})} | {json.loads(Path(task['sandbox_spec']).read_text())['bwrap']}
        task_trees = [h for h, _ in spec_mounts if h in scope.get('trees', {})]
        # Per call: code, protocol, manifest, receipt/reports. Per attempt: plus this task's inputs and mounted runtime trees.
        gate = lambda: stage.check(scope_path, receipt_path, PERMISSION, STAGE, ALL_LIMITS, req, verify_files=code_files, verify_trees=[])  # noqa: E731
        deep_gate = lambda: stage.check(scope_path, receipt_path, PERMISSION, STAGE, ALL_LIMITS, req, verify_files=task_files, verify_trees=task_trees)  # noqa: E731
        load_credentials(scope['credentials_file'])
        cfg = {'model': scope['model_config'], 'budget': scope['prices'], 'phase': 'main'}
        with sqlite3.connect(Path(scope['ledger']).as_uri() + '?mode=ro', uri=True) as db:
            authorization = json.loads(db.execute('SELECT authorization FROM meta WHERE id=1').fetchone()[0])
        ledger = StageBudgetLedger(scope['ledger'], authorization, route=ROUTE, limits={k: v / 1e9 for k, v in LIMITS.items()})
        used = set()
        op = stage.Bounded(StructuredRepositoryModel(cfg, ledger, out / 'responses'), gate, ROUTE, used)
        lesson_model = stage.Bounded(StructuredReflectionModel(cfg, ledger, out / 'responses'), gate, ROUTE, used)
        lesson_model.share_failure(op)
        spec = json.loads(Path(task['sandbox_spec']).read_text())
        base = json.loads(Path(task['messages_path']).read_text())
        statement = Path(task['statement_path']).read_bytes().decode()
        initial_paths = list(json.loads((Path(task['initial_snapshot']) / 'manifest.json').read_text())['entries'])

        def attempt(label, messages):
            if stop_file.exists():
                raise RuntimeError('stage stopped by another worker')
            d = case / label
            wait_provider_ok()
            deep_gate()
            if restore(task['initial_snapshot'], d / 'work') != task['snapshot_identity']:
                raise ValueError('Initial snapshot differs')
            op.begin(f'{ROUTE}/{name}/{label}', 8)
            result = run(model=op, messages=messages, call_prefix=f'{ROUTE}/{name}/{label}', spec=spec, workspace=d / 'work',
                         test_command=task['test_command'], expected_tests=task['expected_tests'],
                         test_hashes=task['test_hashes'], arm='RAW', output=d / 'attempt')
            op.end(); capture(d / 'work', d / 'final-snapshot')
            result['modified_files'] = modified(task['initial_snapshot'], d / 'work')
            result['integrity_flags'] = integrity.flags(result['modified_files'], initial_paths)
            result['m1_outcome'] = classify(result); result['m1_subtype'] = subtype(result)
            if result['m1_outcome'] == 'harness':
                kind, detail = harness_kind(result, d, out, ledger.db, f'{ROUTE}/{name}/{label}/{op.turn}')
                result['m1_harness_kind'] = kind; result['m1_harness_detail'] = detail
                if kind == 'stop':
                    mark_stop(stop_file, {'status': 'STOPPED_FOR_REVIEW', 'task': name, 'at': label, 'subtype': result['m1_subtype'], 'detail': detail})
                else:  # isolated transient provider error with transport evidence: attempt missing, stage continues
                    record_event(out, stop_file, name, label, dict(detail, subtype=result['m1_subtype']))
                    op.clear_failure()
            return d, result

        def wait_provider_ok():
            deadline = time.monotonic() + FATAL_WAIT_S
            state = getattr(op.transport, '_state', lambda: {})
            while state().get('fatal'):
                if stop_file.exists():
                    raise RuntimeError('stage stopped by another worker')
                if time.monotonic() > deadline:
                    mark_stop(stop_file, {'status': 'STOPPED_FOR_REVIEW', 'task': name, 'reason': 'provider_fatal_state_persisted'})
                    raise RuntimeError('stage stopped by another worker')
                time.sleep(30)

        for rep in range(1, (1 if PART == 'smoke' else manifest['first_attempt_repetitions']) + 1):
            entry = {'rep': rep}; row['attempts'].append(entry)
            d1, first = attempt(f'A1-{rep}', base); entry['A1'] = first
            if stop_file.exists():
                break
            if first['m1_outcome'] == 'harness':  # transient: this repetition is missing
                continue
            if first['m1_outcome'] != 'unresolved':  # natural failure = final evaluation not passing, no integrity flag
                continue
            final_raw = (d1 / 'attempt' / 'evaluation' / 'raw.log').read_text(errors='replace')
            transcript = json.loads((d1 / 'attempt' / 'transcript.json').read_text())
            evaluation = json.loads((d1 / 'attempt' / 'evaluation.json').read_text())
            record = memory.failure_record(transcript, final_raw)
            sig = memory.signals(evaluation, final_raw, first['modified_files'])
            request = memory.reflection_messages(statement, transcript, final_raw)
            fdir = case / f'F-{rep}'; fdir.mkdir(parents=True, exist_ok=False)
            write_once(fdir / 'record.json', {'record': record}); write_once(fdir / 'signals.json', sig)
            write_once(fdir / 'reflection-request.json', request)
            bound = lesson_input_bound(request)
            if bound > scope['model_config']['input_token_limit']:
                lesson = None; entry['lesson'] = {'status': 'lesson_context_limit', 'input_upper_bound': bound}
            else:
                if stop_file.exists():
                    raise RuntimeError('stage stopped by another worker')
                prefix = f'{ROUTE}/{name}/LESSON-{rep}'
                lesson_model.begin(prefix, 1); started = time.monotonic()
                lesson_status = 'valid'
                try:
                    lesson = json.loads(lesson_model.complete(request, prefix + '/1').text)
                except ModelOutputIncomplete:
                    lesson = None; lesson_status = 'lesson_output_limit'  # censored like an over-bound lesson
                except BudgetStop:
                    raise
                except ModelError as error:
                    ok, detail = transient_evidence(out, ledger.db, prefix + '/1')
                    if type(error) is not ModelError or not ok:  # model-caused (e.g. invalid lesson) or no evidence: stop
                        raise
                    lesson = None; lesson_status = 'lesson_harness'  # transient: lesson missing, REFLECT/BOTH not run
                    record_event(out, stop_file, name, f'LESSON-{rep}', dict(detail, type=type(error).__name__))
                    lesson_model.clear_failure()
                lesson_model.end()
                if lesson is None:
                    entry['lesson'] = {'status': lesson_status, 'input_upper_bound': bound, 'seconds': time.monotonic() - started}
                else:
                    write_once(fdir / 'lesson.json', lesson)
                    entry['lesson'] = {'status': 'valid', 'input_upper_bound': bound, 'seconds': time.monotonic() - started}
            available = [a for a in ARMS if lesson is not None or a not in ('REFLECT', 'BOTH')]
            k = (index + rep) % len(ARMS)
            order = [a for a in (ARMS[k:] + ARMS[:k]) if a in available]
            entry['retry_order'] = order; entry['retries'] = {}
            for arm in order:
                messages = memory.retry_messages(base, arm, lesson, sig, record)
                write_once(fdir / f'R-{arm}.messages.json', messages)
                _, result = attempt(f'R{rep}-{arm}', messages)
                entry['retries'][arm] = result
                if stop_file.exists():
                    break  # harness-missing retries (transient) are skipped by analysis; others continue
            if stop_file.exists():
                break
        row['status'] = 'stopped' if stop_file.exists() else 'complete'
    except BaseException as error:
        row['status'] = 'error'; row['error'] = type(error).__name__
        row['frames'] = [f'{Path(f.filename).name}:{f.lineno}' for f in traceback.extract_tb(error.__traceback__)]
        if not isinstance(error, RuntimeError) or 'stopped by another worker' not in str(error):
            mark_stop(stop_file, {'status': 'STOPPED_FOR_REVIEW', 'task': name, 'error': type(error).__name__})
    finally:
        if ledger is not None:
            try:
                ledger.db.close()
            except Exception:
                pass
    try:  # a worker never raises: its row (possibly partial) is always returned and persisted
        write_once(case / 'row.json', row)
    except Exception as error:
        row['row_write_error'] = type(error).__name__
    return row


def execute(scope_path, receipt_path, part):
    set_part(part)
    scope = json.loads(Path(scope_path).read_text()); req, manifest = required(scope)
    stage.check(scope_path, receipt_path, PERMISSION, STAGE, ALL_LIMITS, req, deep=True)
    if PART == 'main':  # smoke must have completed cleanly (M2 impl review r1)
        smoke = Path(scope['output_root']) / 'smoke'
        if not (smoke / 'complete.json').exists() or (smoke / 'stopped.json').exists() \
                or any((smoke / 'harness-events').glob('*.json')):
            raise ValueError('Smoke part has not completed cleanly')
    with sqlite3.connect(Path(scope['ledger']).as_uri() + '?mode=ro', uri=True) as db:
        authorization = json.loads(db.execute('SELECT authorization FROM meta WHERE id=1').fetchone()[0])
        if db.execute('SELECT 1 FROM calls WHERE id LIKE ? LIMIT 1', (ROUTE + '/%',)).fetchone():
            raise ValueError('Stage already attempted; no automatic retry')
        if PART == 'main' and not db.execute('SELECT 1 FROM calls WHERE id LIKE ? LIMIT 1', (f'{STAGE}/smoke/%',)).fetchone():
            raise ValueError('Smoke part must run first')
    if authorization['authorized_usd'] != 220:
        raise ValueError('Unexpected cumulative authorization')
    out = Path(scope['output_root']) / PART; out.mkdir(parents=True, exist_ok=False)
    write_once(out / 'scope.json', scope)
    load_credentials(scope['credentials_file'])
    cfg = {'model': scope['model_config'], 'budget': scope['prices'], 'phase': 'main'}
    access = check_model_access(cfg); write_once(out / 'access.json', access)
    if not access.get('authenticated') or access.get('model_id') != cfg['model']['id']:
        raise ValueError('Model access unavailable')
    StageBudgetLedger(scope['ledger'], authorization, route=ROUTE, limits={k: v / 1e9 for k, v in LIMITS.items()}).db.close()
    jobs = [(scope_path, receipt_path, i, PART) for i, t in enumerate(manifest['tasks'])
            if PART == 'main' or t['task_id'] in SMOKE_TASKS]
    try:
        with multiprocessing.get_context('spawn').Pool(manifest['workers']) as pool:
            rows = pool.map(task_worker, jobs, chunksize=1)
    except BaseException as error:
        rows = [json.loads(p.read_text()) for p in sorted((out / 'tasks').glob('*/row.json'))]
        write_once(out / 'outcomes-partial.json', rows)
        write_once(out / 'entry-error.json', {'status': 'STOPPED_FOR_REVIEW', 'type': type(error).__name__})
        raise
    marker = 'stopped.json' if (out / 'stopped.json').exists() else 'complete.json'
    if marker == 'complete.json':
        write_once(out / 'complete.json', {'status': 'MAIN_COMPLETE_REQUIRES_RECONCILIATION_AND_FINAL_REVIEW', 'tasks': len(rows)})
    write_once(out / 'outcomes.json', rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--scope', required=True); parser.add_argument('--receipt', required=True)
    parser.add_argument('--part', required=True, choices=sorted(PART_CAPS))
    args = parser.parse_args(); execute(args.scope, args.receipt, args.part)
