"""Scripted dry run of m1/run_dev.py. SCRIPTED_DEBUG: no network, credentials or real ledger.

Replaces only the transports, access check and gate; the ledger is a temporary COPY of
the original so reservation/settlement code paths run. Scenarios: natural failure -> lesson
-> four retries; resolved first attempt; context_limit; over-bound lesson (NONE/SIGNAL only);
integrity-flagged attempt; stop on a lesson model error with the in-progress row kept.
Usage: python3 m1/dryrun_dev.py <manifest.json> <out_dir>
"""
import json, shutil, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / 'runner', ROOT / 'm1'):
    sys.path.insert(0, str(p))
import run_dev  # noqa: E402
from harness.agent import ModelError, ModelOutputIncomplete  # noqa: E402


class FakeTransport:
    """Settles a tiny fixed cost through the real stage ledger to exercise accounting."""
    script = {}

    def __init__(self, cfg, ledger, response_dir):
        self.ledger, self.response_dir = ledger, Path(response_dir); self.response_dir.mkdir(parents=True, exist_ok=True)

    def complete(self, messages, call_id):
        from harness.artifacts import digest
        self.ledger.reserve(call_id, 1000)
        text = FakeTransport.script[call_id] if call_id in FakeTransport.script else self.default(call_id)
        if text is None:  # transient HTTP 503 with the same evidence the real transport leaves
            (self.response_dir / (digest(call_id) + '.error.json')).write_text(json.dumps({'call_id': call_id, 'http_status': 503, 'error_code': 'other'}))
            self.ledger.unknown(call_id, 'uncertain_http_503'); raise ModelError('scripted provider error')
        if text == '__INVALID__':  # model-caused invalid output: settled, no transport evidence
            self.ledger.settle(call_id, 500, {'prompt_tokens': 1, 'completion_tokens': 1})
            (self.response_dir / (digest(call_id) + '.json')).write_text(json.dumps({'response': {'choices': [{'finish_reason': 'stop'}]}}))
            raise ModelError('scripted invalid structured output')
        self.ledger.settle(call_id, 500, {'prompt_tokens': 1, 'completion_tokens': 1})
        if text == '__INCOMPLETE__':  # settled, retained, finish_reason=length
            (self.response_dir / (digest(call_id) + '.json')).write_text(json.dumps({'response': {'choices': [{'finish_reason': 'length'}]}}))
            raise ModelOutputIncomplete('scripted truncated output')
        (self.response_dir / (digest(call_id) + '.json')).write_text(json.dumps({'response': {'choices': [{'finish_reason': 'stop'}]}}))
        return SimpleNamespace(text=text, cost_nusd=500)


class FakeOp(FakeTransport):
    def default(self, call_id):
        return '{"action":"done"}'


class FakeLesson(FakeTransport):
    def default(self, call_id):
        return json.dumps({'suspected_files': ['x.py'], 'suspected_functions': ['f'], 'root_cause': 'r',
                           'what_failed': 'w', 'next_steps': ['n']}, sort_keys=True)


CODE = ['m1/run_dev.py', 'm1/stage.py', 'm1/memory.py', 'm1/integrity.py', 'm1/accuracy.py', 'runner/repository_multiturn_structured_r2.py',
        'harness/structured_reflection_provider.py', 'harness/structured_repository_provider.py']


def scripted(manifest, route):
    """Per-task scripted operations covering every orchestrator branch."""
    import base64
    script, expect = {}, {}
    ref = Path(manifest['tasks'][0]['messages_path']).parent / 'reference'
    for t in manifest['tasks']:
        name = t['task_id']; a1 = f'{route}/{name}/A1'
        if name == 'tornado/5':  # write the pinned reference file -> resolved, no retries
            rel = t['reference_changed'][0]
            data = base64.b64encode((ref / 'tornado-5' / 'fixed' / rel).read_bytes()).decode()
            script[a1 + '/1'] = json.dumps({'action': 'shell', 'command': f'echo {data} | base64 -d > {rel}'})
            expect[name] = 'resolved'
        elif name == 'black/1':  # huge tool output -> next request over bound -> context_limit
            script[a1 + '/1'] = json.dumps({'action': 'shell', 'command': 'cat black.py'}); expect[name] = 'context_limit'
        elif name == 'black/4':  # 8th output huge -> response_limit, lesson request over bound
            for n in range(1, 8):
                script[f'{a1}/{n}'] = json.dumps({'action': 'shell', 'command': 'true'})
            script[a1 + '/8'] = json.dumps({'action': 'shell', 'command': 'cat black.py'}); expect[name] = 'lesson_context_limit'
        elif name == 'thefuck/3':  # creates conftest.py -> integrity flag, excluded
            script[a1 + '/1'] = json.dumps({'action': 'shell', 'command': 'touch conftest.py'}); expect[name] = 'integrity'
        else:
            expect[name] = 'natural'
    return script, expect


def main(manifest_path, out):
    import hashlib
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    tmp = Path(tempfile.mkdtemp(prefix='m1-dryrun-ledger-')); ledger = tmp / 'budget.sqlite'
    shutil.copyfile(run_dev.stage.LEDGER, ledger)
    manifest = json.loads(Path(manifest_path).read_text())
    run_dev.stage.check = lambda *a, **k: None  # gate replaced in dry run only
    run_dev.load_credentials = lambda path: None
    run_dev.check_model_access = lambda cfg: {'authenticated': True, 'model_id': 'x'}
    run_dev.StructuredRepositoryModel, run_dev.StructuredReflectionModel = FakeOp, FakeLesson
    results = {}
    for scenario in ('mixed', 'stop_on_lesson_error'):
        run_dev.ROUTE = f'afm-m1-dryrun-{scenario}-{tmp.name}'; run_dev.LIMITS = {run_dev.ROUTE: 4_000_000_000}
        script, expect = scripted(manifest, run_dev.ROUTE)
        if scenario == 'stop_on_lesson_error':
            first_natural = next(n for n, e in expect.items() if e == 'natural')
            script = {f'{run_dev.ROUTE}/{first_natural}/LESSON/1': None}
        FakeTransport.script = script
        scope = {'manifest': str(manifest_path), 'protocol': str(manifest_path), 'output_root': str(out / scenario),
                 'credentials_file': '/dev/null', 'ledger': str(ledger), 'prices': {},
                 'model_config': {'id': 'x', 'input_token_limit': 96000}}
        scope_path = out / f'{scenario}-scope.json'; scope_path.write_text(json.dumps(scope))
        try:
            run_dev.execute(str(scope_path), '/dev/null'); error = None
        except Exception as e:
            error = type(e).__name__
        o = out / scenario
        rows = json.loads(((o / 'outcomes.json') if (o / 'outcomes.json').exists() else (o / 'outcomes-partial.json')).read_text())
        results[scenario] = {'error': error, 'complete': (o / 'complete.json').exists(), 'expect': expect, 'rows': [
            {'task': r['task'], 'A1': r.get('A1', {}).get('status'), 'resolved': r.get('A1', {}).get('resolved'),
             'integrity': r.get('A1', {}).get('integrity_flags'), 'lesson': r.get('lesson'),
             'retries': r.get('retries') if isinstance(r.get('retries'), (str, type(None))) else
             {a: (v['status'], v['resolved']) for a, v in r['retries'].items()}} for r in rows]}
    mixed = {r['task']: r for r in results['mixed']['rows']}
    assert results['mixed']['complete'] and results['mixed']['error'] is None
    for name, e in results['mixed']['expect'].items():
        r = mixed[name]
        if e == 'resolved':
            assert r['A1'] == 'declared_done' and r['resolved'] is True and r['retries'] == 'not_applicable', r
        elif e == 'context_limit':
            assert r['A1'] == 'context_limit' and r['resolved'] is None and r['retries'] == 'not_applicable', r
        elif e == 'lesson_context_limit':
            assert r['A1'] == 'response_limit' and r['lesson']['status'] == 'lesson_context_limit' and set(r['retries']) == {'NONE', 'SIGNAL'}, r
        elif e == 'integrity':
            assert r['integrity'] == ['conftest.py'] and r['resolved'] is None and r['retries'] == 'not_applicable', r
        else:
            assert r['resolved'] is False and r['lesson']['status'] == 'valid' and set(r['retries']) == set(['NONE', 'REFLECT', 'SIGNAL', 'BOTH']), r
    stop = results['stop_on_lesson_error']
    assert stop['error'] == 'ModelError' and not stop['complete'] and stop['rows'][-1]['A1'] == 'declared_done'  # in-progress row kept
    code = {c: hashlib.sha256((ROOT / c).read_bytes()).hexdigest() for c in CODE}
    (out / 'dryrun.json').write_text(json.dumps({'kind': 'SCRIPTED_DEBUG_NOT_EXPERIMENT', 'paid_calls': 0, 'code_sha256': code,
                                                  'ledger_copy': str(ledger), 'results': results}, indent=1) + '\n')
    print('M1 DEV dry run PASS', json.dumps({k: {'complete': v['complete'], 'error': v['error']} for k, v in results.items()}))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
