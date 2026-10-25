"""Turn a built r7 environment into a sandboxed runner task. No model requests.

Selected tests are BugsInPy's bug-revealing tests (run_test.sh), normalised to pytest
node ids so one event recorder serves unittest and pytest tasks. Test files' directories
and pytest configuration files are read-only for the agent and hashed for the scorer.
"""
import hashlib, json, re, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT / 'runner'))
import trial_build  # noqa: E402
from repository_snapshot import capture  # noqa: E402

BWRAP = '/home/user/.local/share/budgeted-gate-fail-learning/tools/bubblewrap/usr/bin/bwrap'
INSTRUMENTATION = trial_build.RUNTIME / 'instrumentation'
RUNNER_PYTEST = 'pytest==6.2.5'
CONFIG_FILES = ['setup.cfg', 'pytest.ini', 'tox.ini', 'pyproject.toml', 'conftest.py']


def node_ids(commands, checkout):
    ids = []
    for cmd in commands:
        argv = cmd.split()
        if argv[0] == 'pytest' or argv[:3] in (['python', '-m', 'pytest'], ['python3', '-m', 'pytest']):
            ids += [a for a in argv[(1 if argv[0] == 'pytest' else 3):] if not a.startswith('-')]
        elif argv[:3] == ['python', '-m', 'unittest']:
            for target in [a for a in argv[3:] if not a.startswith('-')]:
                parts = target.split('.')
                for cut in range(len(parts) - 1, 0, -1):  # longest module prefix that is a file
                    path = Path(*parts[:cut]).with_suffix('.py')
                    if (checkout / path).is_file():
                        ids.append('::'.join([path.as_posix()] + parts[cut:])); break
                else:
                    raise ValueError(f'Cannot map unittest target {target}')
        else:
            raise ValueError(f'Unsupported test command {cmd}')
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate selected tests')
    return ids


def runner_packages(exe, version):
    """Shared pytest for the event recorder; a task's own pinned pytest takes precedence."""
    target = trial_build.RUNTIME / 'runner-packages' / version
    if (target / 'done.json').exists():
        return target
    tools = trial_build.RUNTIME / 'runner-packages' / ('tools-' + version); tools.mkdir(parents=True, exist_ok=True)
    short = ''.join(version.split('.')[:2])
    subprocess.run([sys.executable, '-m', 'pip', 'download', '-q', '--no-deps', '-d', str(tools), '--only-binary=:all:',
                    '--python-version', short, 'pip==21.3.1' if int(short) < 37 else 'pip==23.0.1'], check=True)
    pip_wheel = next(tools.glob('pip-*.whl'))
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(tools), 'PYTHONPATH': str(pip_wheel),
           'SSL_CERT_FILE': '/etc/ssl/certs/ca-certificates.crt', 'PIP_CACHE_DIR': str(trial_build.RUNTIME / 'pip-cache')}
    subprocess.run([str(exe), '-m', 'pip', 'install', '-q', '--target', str(target), RUNNER_PYTEST], env=env, check=True)
    (target / 'done.json').write_text(json.dumps({'pytest': RUNNER_PYTEST}) + '\n')
    return target


def instrumentation():
    INSTRUMENTATION.mkdir(parents=True, exist_ok=True)
    for name in ('phase_probe_plugin.py', 'phase_probe_runner.py'):
        shutil.copyfile(ROOT / 'runner' / name, INSTRUMENTATION / name)
    # Minimal identity for the sandbox user (getpass/pwd lookups); no host accounts exposed.
    (INSTRUMENTATION / 'passwd').write_text('agent:x:1000:1000:agent:/tmp:/bin/sh\n')
    (INSTRUMENTATION / 'group').write_text('agent:x:1000:\n')
    return INSTRUMENTATION


def file_hashes(checkout, protected):
    out = {}
    for rel in protected:
        path = checkout / rel
        files = [path] if path.is_file() else sorted(p for p in path.rglob('*') if p.is_file())
        for f in files:
            if '__pycache__' in f.parts:
                continue
            out[f.relative_to(checkout).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def collect(spec, out, exe, tests, command):
    """Expected test ids = test events the scorer's own recorder emits for the selections
    (one recorded run on the buggy snapshot; handles parametrization and old pytest ids)."""
    from repository_snapshot import restore
    from repository_sandbox import execute
    work = out / 'collect' / 'work'; output = out / 'collect' / 'output'
    restore(out / 'initial-snapshot', work)
    execute(spec, work, output, command, timeout=300)
    events = json.loads((output / 'events.json').read_text())['events']
    ids = list(dict.fromkeys(e['nodeid'] for e in events if e.get('event') == 'test'))
    plain = lambda i: i.replace('::()', '')  # noqa: E731  (pytest<4 instance segment)
    if not ids or any(not any(plain(i) == t or plain(i).startswith(t + '[') or plain(i).startswith(t + '::') for t in tests) for i in ids):
        raise ValueError('Recorded tests did not match selected tests')
    shutil.rmtree(out / 'collect')
    return ids


def build(task, out):
    project, bug = task.split('/')
    env_root = trial_build.RUNTIME / trial_build.ROUND / f'{project}-{bug}'
    result = json.loads((env_root / 'result.json').read_text())
    meta = result['bug_info']; bdir = trial_build.BUGSINPY / 'projects' / project / 'bugs' / bug
    exe = Path(next(s['path'] for s in result['steps'] if s['step'] == 'interpreter'))
    install = exe.parent.parent
    buggy = env_root / 'buggy'
    commands = [c.strip() for c in (bdir / 'run_test.sh').read_text().splitlines() if c.strip()]
    tests = node_ids(commands, buggy)
    test_files = [t.strip() for t in meta['test_file'].split(';') if t.strip()]
    protected = sorted({str(Path(t).parent) for t in test_files} | {c for c in CONFIG_FILES if (buggy / c).is_file()})
    protected = [p for p in protected if p not in ('.', '')] or test_files
    pythonpath = [p for p in meta.get('pythonpath', '').split(':') if p]
    work_paths = ['/work/' + p for p in pythonpath if (buggy / p).is_dir()]
    if any('build/lib' in p for p in pythonpath) and (buggy / 'lib').is_dir():
        work_paths.append('/work/lib')
    rp = runner_packages(exe, meta['python_version']); inst = instrumentation()
    deps = [trial_build.RUNTIME / 'deps' / ('openssl-' + trial_build.OPENSSL) / 'install', trial_build.RUNTIME / 'deps' / 'prefix']
    mounts = [['/usr', '/usr'], ['/lib', '/lib'], ['/lib64', '/lib64'], ['/bin', '/bin'], ['/etc/alternatives', '/etc/alternatives']]
    mounts += [[str(p), str(p)] for p in [install, *deps, env_root / 'packages', env_root / 'tools' / 'site', rp, inst] if p.exists()]
    mounts += [[str(inst / 'passwd'), '/etc/passwd'], [str(inst / 'group'), '/etc/group']]
    spec = {'bwrap': BWRAP, 'readonly_mounts': mounts, 'protected_paths': protected,
            'environment': {'PHASE_PROBE_OUTPUT': '/output/events.json', 'PHASE_PROBE_LIFECYCLE': '/output/lifecycle.json',
                            'PYTHONPATH': ':'.join([str(inst)] + work_paths + ['/work', str(env_root / 'packages'), str(rp),
                                                                              str(env_root / 'tools' / 'site')]),
                            'PYTHONDONTWRITEBYTECODE': '1', 'LANG': 'C.UTF-8', 'PATH': f'{exe.parent}:/usr/bin:/bin', 'HOME': '/tmp', 'USER': 'agent', 'LOGNAME': 'agent'}}
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    (out / 'spec.json').write_text(json.dumps(spec, indent=1) + '\n')
    snapshot = capture(buggy, out / 'initial-snapshot')
    command = [str(exe), '-m', 'phase_probe_runner', *tests, '-p', 'no:cacheprovider', '-p', 'phase_probe_plugin']
    expanded = collect(spec, out, exe, tests, command)
    task_record = {'task_id': task, 'project': project, 'python_version': meta['python_version'],
                   'initial_snapshot': str(out / 'initial-snapshot'), 'snapshot_identity': snapshot['identity'],
                   'fixed_checkout': str(env_root / 'fixed'), 'sandbox_spec': str(out / 'spec.json'),
                   'test_command': command,
                   'selected_tests': tests, 'expected_tests': expanded, 'test_hashes': file_hashes(buggy, protected),
                   'environment_result_sha256': hashlib.sha256((env_root / 'result.json').read_bytes()).hexdigest()}
    (out / 'task.json').write_text(json.dumps(task_record, indent=1) + '\n')
    return task_record
