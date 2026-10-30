"""Post hoc full-suite regression check (planning/fullsuite-rule-r1.json). Offline; no model calls.

  python3 m1/fullsuite.py baseline   # fixed checkout of each task with failures, twice
  python3 m1/fullsuite.py patched    # every resolved retry (and resolved first attempts of those tasks)
  python3 m1/fullsuite.py summarize  # per-run regression records -> planning/m1-main-r1/analysis-final/fullsuite-r1.json

Retained run material is only read: each workspace is copied before tests run.
"""
import hashlib, json, os, shutil, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner.repository_sandbox import execute

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path('/runtime')
OUT = RUNTIME / 'fullsuite-r1'
SCRATCH = Path(os.environ.get('FULLSUITE_SCRATCH', tempfile.gettempdir()))
FINAL = ROOT / 'planning/m1-main-r1/analysis-final'
RULE = json.loads((ROOT / 'planning/fullsuite-rule-r1.json').read_text())
ROOTS = RULE['suite']['roots']
TIMEOUT = RULE['suite']['timeout_seconds']
BATCH_RUN = {'T2': 'r2', 'T3': 'r3', 'T4': 'r4'}
WORKERS = int(os.environ.get('FULLSUITE_WORKERS', '8'))


def index():
    """Kept tasks with failures: manifest entry, run directory, resolved attempts to check."""
    combined = json.loads((FINAL / 'C.json').read_text())
    run_of = {}
    for source in combined['sources']:
        batch = Path(source['path']).stem
        for a in json.loads((ROOT / source['path']).read_text())['first_attempts']:
            run_of[a['task']] = BATCH_RUN[batch]
    entries = {}
    for run in set(run_of.values()):
        scope = json.loads((RUNTIME / f'm1-main-paid-{run}/scope.json').read_text())
        for t in json.loads(Path(scope['manifest']).read_text())['tasks']:
            if run_of.get(t['task_id']) == run:
                entries[t['task_id']] = dict(t, run=run)
    failing = {i['task'] for i in combined['instances']}
    units = []
    for i in combined['instances']:
        for arm, v in i['arms'].items():
            if v['outcome'] == 'resolved':
                units.append({'task': i['task'], 'rep': i['rep'], 'kind': 'retry', 'arm': arm, 'dir': f"R{i['rep']}-{arm}"})
    for a in combined['first_attempts']:
        if a['task'] in failing and a['outcome'] == 'resolved':
            units.append({'task': a['task'], 'rep': a['rep'], 'kind': 'first', 'arm': 'FIRST', 'dir': f"A1-{a['rep']}"})
    return {t: entries[t] for t in sorted(failing)}, units


def _hash_tree(root):
    out = {}
    for p in sorted(Path(root).rglob('*')):
        if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts:
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _test_files(base, roots):
    """Test modules under the roots, listed explicitly so pytest's file-name pattern does not matter."""
    files = []
    for r in roots:
        for p in sorted((Path(base) / r).rglob('*.py')):
            name = p.name
            if name.startswith('test') or name.endswith('_test.py') or name.endswith('_tests.py'):
                files.append(str(p.relative_to(base)))
    return files


def _argv(entry, files):
    python = entry['test_command'][0]
    return [python, '-m', 'phase_probe_runner', *files, '--continue-on-collection-errors',
            '-p', 'no:cacheprovider', '-p', 'phase_probe_plugin']


def _roots(entry, mode):
    if mode == 'full':
        return ROOTS[entry['project']]
    return sorted({str(Path(t.split('::')[0]).parent) for t in entry['selected_tests']})


def _run(entry, source, out_dir, mode):
    """Copy source, overlay suite-root files from the fixed checkout, run the suite."""
    out_dir = Path(out_dir)
    if (out_dir / 'record.json').exists():
        return json.loads((out_dir / 'record.json').read_text())
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    scratch = Path(tempfile.mkdtemp(prefix='fs-', dir=SCRATCH))
    work = scratch / 'work'
    try:
        shutil.copytree(source, work, symlinks=True)
        fixed = Path(entry['fixed_checkout'])
        roots = _roots(entry, mode)
        edited_tests = []
        for r in roots:
            before, ref = _hash_tree(work / r) if (work / r).exists() else {}, _hash_tree(fixed / r)
            edited_tests += [f'{r}/{k}' for k in set(before) | set(ref) if before.get(k) != ref.get(k)]
            if (work / r).exists():
                shutil.rmtree(work / r)
            shutil.copytree(fixed / r, work / r, symlinks=True)
        spec = json.loads(Path(entry['sandbox_spec']).read_text())
        spec = dict(spec, environment=dict(spec['environment'], PHASE_PROBE_OUTPUT='/output/events.json',
                                           PHASE_PROBE_LIFECYCLE='/output/lifecycle.json'))
        start = time.monotonic()
        result = execute(spec, work, out_dir / 'exec', _argv(entry, _test_files(fixed, roots)), timeout=TIMEOUT)
        seconds = time.monotonic() - start
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    lifecycle = _load(out_dir / 'exec/lifecycle.json')
    events = _load(out_dir / 'exec/events.json')
    phases = {}
    for e in (events or {}).get('events', []):
        if e.get('event') == 'test':
            phases.setdefault(e['nodeid'], {})[e['phase']] = e['outcome']
    record = {'mode': mode, 'roots': roots, 'seconds': round(seconds, 1), 'timeout': result['timeout'],
              'returned': (lifecycle or {}).get('state') == 'returned', 'exit': result['exit'],
              'collected': (events or {}).get('testscollected'),
              'passed': sorted(n for n, p in phases.items() if p and all(v == 'passed' for v in p.values()) and 'call' in p),
              'n_nodes': len(phases), 'edited_test_files': sorted(edited_tests)}
    (out_dir / 'record.json').write_text(json.dumps(record) + '\n')
    return record


def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _key(task):
    return task.replace('/', '-')


def baseline():
    tasks, _ = index()
    def one(item):
        task, entry = item
        recs = [_run(entry, entry['fixed_checkout'], OUT / 'baseline' / _key(task) / f'full-{k}', 'full') for k in (1, 2)]
        ok = all(r['returned'] and not r['timeout'] for r in recs)
        if not ok:
            recs = [_run(entry, entry['fixed_checkout'], OUT / 'baseline' / _key(task) / f'dirs-{k}', 'dirs') for k in (1, 2)]
        mode = 'full' if ok else 'dirs'
        stable = sorted(set(recs[0]['passed']) & set(recs[1]['passed']))
        flaky = sorted(set(recs[0]['passed']) ^ set(recs[1]['passed']))
        summary = {'task': task, 'mode': mode, 'stable_pass': stable, 'flaky': flaky,
                   'seconds': [r['seconds'] for r in recs], 'returned': [r['returned'] for r in recs]}
        (OUT / 'baseline' / _key(task) / 'summary.json').write_text(json.dumps(summary) + '\n')
        print(task, mode, len(stable), 'stable', len(flaky), 'flaky', summary['seconds'], flush=True)
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(one, tasks.items()))


def patched():
    tasks, units = index()
    def one(u):
        entry = tasks[u['task']]
        base = json.loads((OUT / 'baseline' / _key(u['task']) / 'summary.json').read_text())
        src = RUNTIME / f"m1-main-paid-{entry['run']}/tasks/{_key(u['task'])}/{u['dir']}/work"
        rec = _run(entry, src, OUT / 'patched' / _key(u['task']) / u['dir'], base['mode'])
        print(u['task'], u['dir'], rec['seconds'], flush=True)
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(one, units))


def summarize():
    tasks, units = index()
    rows = []
    for u in units:
        base = json.loads((OUT / 'baseline' / _key(u['task']) / 'summary.json').read_text())
        rec = _load(OUT / 'patched' / _key(u['task']) / u['dir'] / 'record.json')
        if rec is None:
            rows.append(dict(u, status='missing'))
            continue
        broken = sorted(set(base['stable_pass']) - set(rec['passed']))
        ok_run = rec['returned'] and not rec['timeout']
        rows.append(dict(u, project=tasks[u['task']]['project'], mode=base['mode'], status='ok' if ok_run else 'did_not_return',
                         n_stable=len(base['stable_pass']), regressions=broken[:50], n_regressions=len(broken),
                         suite_clean=ok_run and not broken, edited_test_files=rec['edited_test_files']))
    out = {'kind': 'POST_HOC_FULLSUITE_R1', 'rule': 'planning/fullsuite-rule-r1.json', 'rows': rows}
    (FINAL / 'fullsuite-r1.json').write_text(json.dumps(out, indent=1) + '\n')
    from collections import Counter
    c = Counter((r['arm'], r.get('suite_clean')) for r in rows)
    for arm in ('FIRST', 'NONE', 'HISTORY', 'REFLECT', 'SIGNAL', 'BOTH'):
        print(arm, 'clean', c[(arm, True)], 'regressed', c[(arm, False)], 'missing', c[(arm, None)])


if __name__ == '__main__':
    {'baseline': baseline, 'patched': patched, 'summarize': summarize}[sys.argv[1]]()
