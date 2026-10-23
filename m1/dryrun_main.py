"""Scripted dry run of m1/run_main.py (parallel workers). SCRIPTED_DEBUG: no network, credentials or real ledger.

Each spawned worker patches run_main (gate, credentials, transports) before running the real
task_worker against a temporary COPY of the original ledger. Scenario 'mixed' covers resolved,
context_limit, natural failure with all five arms, HISTORY/REFLECT construction and the
integrity screen; scenario 'stop' injects a provider error and checks that every worker stops.
Usage: python3 m1/dryrun_main.py <manifest.json> <out_dir>
"""
import hashlib, json, multiprocessing, os, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / 'runner', ROOT / 'm1'):
    sys.path.insert(0, str(p))
import run_main  # noqa: E402
import dryrun_dev  # noqa: E402  (FakeOp/FakeLesson transports)

CODE = ['m1/run_main.py', 'm1/stage.py', 'm1/memory.py', 'm1/integrity.py', 'm1/score_main.py', 'm1/analyze_main.py', 'm1/accuracy.py', 'm1/claims.py', 'runner/repository_multiturn_structured_r3.py',
        'harness/structured_reflection_provider.py', 'harness/structured_repository_provider.py']


def patched_worker(args):
    route, script = json.loads(os.environ['M1_DRYRUN'])
    run_main.ROUTE = route; run_main.LIMITS = {route: 60_000_000_000}
    run_main.stage.check = lambda *a, **k: None
    run_main.load_credentials = lambda path: None
    run_main.StructuredRepositoryModel, run_main.StructuredReflectionModel = dryrun_dev.FakeOp, dryrun_dev.FakeLesson
    dryrun_dev.FakeTransport.script = script
    return run_main.task_worker(args)


def scripts(manifest, route, scenario):
    s = {}
    for t in manifest['tasks']:
        n = t['task_id']
        if n == 'black/1':
            s[f'{route}/{n}/A1-1/1'] = json.dumps({'action': 'shell', 'command': 'cat black.py'})  # context_limit -> unresolved
        if n == 'thefuck/3':
            s[f'{route}/{n}/A1-1/1'] = json.dumps({'action': 'shell', 'command': 'touch conftest.py'})  # integrity
        if n == 'tornado/5':
            s[f'{route}/{n}/A1-1/1'] = json.dumps({'action': 'shell', 'command': 'echo "def (" >> tornado/ioloop.py'})  # broken import
        if n == 'luigi/12':
            s[f'{route}/{n}/A1-1/1'] = json.dumps({'action': 'shell', 'command': 'sleep 100'})  # tool timeout
        if n == 'youtube-dl/21':
            s[f'{route}/{n}/A1-1/1'] = json.dumps({'action': 'shell', 'command': 'true'})
            s[f'{route}/{n}/A1-1/2'] = '__INCOMPLETE__'  # output truncation inside an attempt -> output_limit
        if n == 'thefuck/6':
            s[f'{route}/{n}/LESSON-1/1'] = '__INCOMPLETE__'  # truncated lesson -> censored
        if n == 'luigi/12':
            s[f'{route}/{n}/A1-2/1'] = None  # transient provider error -> repetition missing, task continues
        if n == 'youtube-dl/12':
            s[f'{route}/{n}/LESSON-1/1'] = None  # transient lesson error -> lesson_harness, REFLECT/BOTH not run
        if n == 'black/4':
            for k in range(1, 8):
                s[f'{route}/{n}/A1-2/{k}'] = json.dumps({'action': 'shell', 'command': 'true'})
            s[f'{route}/{n}/A1-2/8'] = json.dumps({'action': 'shell', 'command': 'cat black.py'})  # big record, capped
    if scenario == 'invalid':  # model-caused invalid output: must stop immediately, not count as transient
        s[f"{route}/{manifest['tasks'][0]['task_id']}/A1-1/1"] = '__INVALID__'
    if scenario == 'stop':  # harness (provider) error inside the first retry of the first task
        first = manifest['tasks'][0]['task_id']
        for arm in run_main.memory.ARMS:
            s[f'{route}/{first}/R1-{arm}/1'] = None
    return s


def main(manifest_path, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    tmp = Path(tempfile.mkdtemp(prefix='m1-main-dryrun-')); ledger = tmp / 'budget.sqlite'
    shutil.copyfile(run_main.stage.LEDGER, ledger)
    manifest = json.loads(Path(manifest_path).read_text()); results = {}
    for scenario in ('mixed', 'stop', 'invalid'):
        route = f'afm-m1-main-dryrun-{scenario}-{tmp.name}'
        scope = {'manifest': str(manifest_path), 'protocol': str(manifest_path), 'output_root': str(out / scenario),
                 'stage_note': 'dry run',
                 'credentials_file': '/dev/null', 'ledger': str(ledger), 'prices': {}, 'route': route,
                 'model_config': {'id': 'x', 'input_token_limit': 96000}}
        scope_path = out / f'{scenario}-scope.json'; scope_path.write_text(json.dumps(scope)); (out / scenario).mkdir()
        os.environ['M1_DRYRUN'] = json.dumps([route, scripts(manifest, route, scenario)])
        with multiprocessing.get_context('spawn').Pool(manifest['workers']) as pool:
            rows = pool.map(patched_worker, [(str(scope_path), '/dev/null', i) for i in range(len(manifest['tasks']))], chunksize=1)
        results[scenario] = {'stopped': (out / scenario / 'stopped.json').exists(), 'rows': rows}
    mixed = {r['task']: r for r in results['mixed']['rows']}
    assert not results['mixed']['stopped'] and all(r['status'] == 'complete' for r in mixed.values()), [r.get('error') for r in mixed.values()]
    assert len(list((out / 'mixed' / 'harness-events').glob('*.json'))) == 2  # below the breaker
    for name, r in mixed.items():
        for e in r['attempts']:
            a1 = e['A1']
            if name == 'thefuck/3' and e['rep'] == 1:
                assert a1['m1_outcome'] == 'excluded_integrity' and 'retries' not in e, e
                continue
            if name == 'luigi/12' and e['rep'] == 2:
                assert a1['m1_outcome'] == 'harness' and a1['m1_harness_kind'] == 'transient' and 'retries' not in e, e
                continue
            if name == 'youtube-dl/12' and e['rep'] == 1:
                assert e['lesson']['status'] == 'lesson_harness' and set(e['retries']) == {'NONE', 'HISTORY', 'SIGNAL'}, e
                continue
            if name == 'thefuck/6' and e['rep'] == 1:
                assert e['lesson']['status'] == 'lesson_output_limit' and set(e['retries']) == {'NONE', 'HISTORY', 'SIGNAL'}, e
                continue
            if name == 'youtube-dl/21' and e['rep'] == 1:
                assert a1['status'] == 'output_limit' and a1['m1_outcome'] == 'unresolved', a1['m1_subtype']
            assert a1['m1_outcome'] == 'unresolved' and e['lesson']['status'] == 'valid' and set(e['retries']) == set(run_main.memory.ARMS), (name, e)
            assert all(v['m1_outcome'] == 'unresolved' for v in e['retries'].values())
            if name == 'black/1' and e['rep'] == 1:
                assert a1['status'] == 'context_limit'
            if name == 'tornado/5' and e['rep'] == 1:
                assert a1['candidate_assessment']['status'] == 'invalid_execution', a1['m1_subtype']
            if name == 'luigi/12' and e['rep'] == 1:
                assert a1['status'] == 'tool_timeout', a1['m1_subtype']
    hist = Path(out / 'mixed' / 'tasks' / 'black-4' / 'F-2' / 'R-HISTORY.messages.json').read_text()
    assert 'characters omitted' in hist  # capped failure record in HISTORY prompt
    stop = results['stop']
    assert stop['stopped'] and all(r['status'] in ('stopped', 'error') for r in stop['rows'])
    first = stop['rows'][0]['attempts'][0]
    assert first['A1']['m1_outcome'] == 'unresolved' and sum(v['m1_outcome'] == 'harness' for v in first['retries'].values()) >= 1
    assert json.loads((out / 'stop' / 'stopped.json').read_text()).get('reason') == 'circuit_breaker'
    inv = results['invalid']
    assert inv['stopped'] and not (out / 'invalid' / 'harness-events').exists(), 'invalid output must stop without a breaker event'
    stop_marker = json.loads((out / 'invalid' / 'stopped.json').read_text())
    assert stop_marker.get('detail', {}).get('ledger_status') != 'uncertain_transport_error', stop_marker
    ev = [json.loads(p.read_text()) for p in (out / 'mixed' / 'harness-events').glob('*.json')]
    assert all(e.get('http_status') == 503 and e.get('ledger_status') == 'uncertain_http_503' for e in ev), ev
    # Scoring and analysis must run on complete and stopped outputs (main impl review MAIN-IMPL-1).
    import score_main, analyze_main
    for scenario in ('mixed', 'stop'):
        score_main.main(str(out / f'{scenario}-scope.json'), str(out / f'{scenario}-instances.json'))
        analyze_main.B = 200
        analyze_main.main(str(out / f'{scenario}-instances.json'), str(out / f'{scenario}-analysis.json'))
    code = {c: hashlib.sha256((ROOT / c).read_bytes()).hexdigest() for c in CODE}
    summary = {'kind': 'SCRIPTED_DEBUG_NOT_EXPERIMENT', 'paid_calls': 0, 'code_sha256': code, 'ledger_copy': str(ledger),
               'mixed': {n: [{'rep': e['rep'], 'A1': e['A1']['status'], 'retries': sorted(e.get('retries', {}))} for e in r['attempts']]
                         for n, r in mixed.items()},
               'stop': [{'task': r['task'], 'status': r['status'], 'attempts': len(r['attempts'])} for r in stop['rows']]}
    (out / 'dryrun.json').write_text(json.dumps(summary, indent=1) + '\n')
    print('M1 MAIN dry run PASS', json.dumps(summary['stop']))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
