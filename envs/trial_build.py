"""Free environment trial build for one BugsInPy task. No model requests.

Steps (each recorded, stops at first failure):
  interpreter  reuse an existing official-source build or build one (no ssl; offline use)
  sources      GitHub tarballs of buggy and fixed commits
  packages     host pip downloads binary wheels for the target Python, installs with --target
  controls     run BugsInPy run_test.sh on buggy (expect fail) and fixed (expect pass)

Usage: python3 envs/trial_build.py <project> <bug_id>
Writes <runtime>/<project>-<bug>/result.json once; an existing result is never overwritten.
"""
import hashlib, json, os, re, shutil, signal, subprocess, sys, tarfile, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUGSINPY = HERE / 'sources' / 'bugsinpy'
RUNTIME = Path('/home/ywb61/.local/share/agent-failure-memory/runtime')
ROUND = 'trial-r7'  # r6: no bz2/lzma/sqlite, unconstrained extras, removed PyPI pins, pkg_resources  # r5: pytest pins dropped, Windows-only pins, host-marker deps  # r1-r3 kept as record: segfault, wheel-only/self-pin, no ssl, zipped setuptools, buggy tests not replaced
# Recorded per-project shims, applied identically to buggy and fixed checkouts.
# Stdlib backports that shadow the standard library on versions that already include it.
WINDOWS_ONLY = {'pywin32', 'pypiwin32', 'pywinpty', 'wmi', 'comtypes', 'windows-curses'}
BACKPORTS = {'dataclasses': (3, 7), 'typing': (3, 5), 'enum34': (3, 4), 'futures': (3, 0), 'asyncio': (3, 4), 'contextvars': (3, 7), 'importlib-resources': (99, 0)}
SHIMS = {'black': {'_black_version.py': 'version = "0+bugsinpy"\n'}}
OPENSSL = '1.1.1w'
# Interpreters already built from official sources in the previous project (read-only reuse).
OLD = Path('/home/ywb61/.local/share/budgeted-gate-fail-learning/runtime/bugsinpy-cross-project-v1')
TIMEOUT = 600


def info(path):
    values = {}
    for line in path.read_text(errors='replace').splitlines():
        m = re.match(r'\s*([A-Za-z_]+)\s*=\s*"?([^"]*)"?\s*$', line)
        if m:
            values[m.group(1)] = m.group(2).strip()
    return values


def run(cmd, cwd, log, env=None, timeout=TIMEOUT):
    with open(log, 'wb') as out:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                env=env, start_new_session=True)
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL); proc.wait()
            return 'TIMEOUT'


def openssl(steps):
    root = RUNTIME / 'deps' / ('openssl-' + OPENSSL); install = root / 'install'
    if (install / 'lib' / 'libssl.so').exists():
        return install
    root.mkdir(parents=True, exist_ok=True)
    url = f'https://www.openssl.org/source/openssl-{OPENSSL}.tar.gz'
    archive = root / f'openssl-{OPENSSL}.tar.gz'
    if not archive.exists():
        archive.write_bytes(urllib.request.urlopen(url, timeout=120).read())
    with tarfile.open(archive) as t:
        t.extractall(root, filter='data')
    src = root / f'openssl-{OPENSSL}'
    for name, cmd in [('configure', ['./config', f'--prefix={install}', f'--openssldir={install}/ssl', 'shared']),
                      ('make', ['make', '-j4']), ('install', ['make', 'install_sw'])]:
        code = run(cmd, src, root / f'{name}.log', timeout=1200)
        if code != 0:
            raise RuntimeError(f'openssl {name} failed: {code}')
    steps.append({'step': 'openssl', 'version': OPENSSL, 'source_url': url,
                  'source_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
    return install


DEPS = [('bzip2', 'https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz'),
        ('xz', 'https://github.com/tukaani-project/xz/releases/download/v5.4.6/xz-5.4.6.tar.gz'),
        ('sqlite', 'https://www.sqlite.org/2024/sqlite-autoconf-3450100.tar.gz')]


def libdeps(steps):
    prefix = RUNTIME / 'deps' / 'prefix'
    if (prefix / 'done.json').exists():
        return prefix
    work = RUNTIME / 'deps' / 'build'; work.mkdir(parents=True, exist_ok=True); record = []
    for name, url in DEPS:
        archive = work / url.rsplit('/', 1)[1]
        if not archive.exists():
            archive.write_bytes(urllib.request.urlopen(url, timeout=120).read())
        with tarfile.open(archive) as t:
            t.extractall(work, filter='data')
        src = work / archive.name[:-len('.tar.gz')]
        if name == 'bzip2':
            cmds = [['make', 'CFLAGS=-fPIC -O2 -D_FILE_OFFSET_BITS=64'], ['make', 'install', f'PREFIX={prefix}']]
        else:
            cmds = [['./configure', f'--prefix={prefix}', '--enable-shared', 'CFLAGS=-fPIC -O2'], ['make', '-j4'], ['make', 'install']]
        for i, cmd in enumerate(cmds):
            code = run(cmd, src, work / f'{name}-{i}.log', timeout=1200)
            if code != 0:
                raise RuntimeError(f'{name} build failed at {cmd[0]}: {code}')
        record.append({'name': name, 'url': url, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
    (prefix / 'done.json').write_text(json.dumps(record, indent=1) + '\n')
    steps.append({'step': 'libdeps', 'built': record})
    return prefix


def interpreter(version, steps):
    # CPython < 3.8 built by gcc 13 needs -O0 (segfaults otherwise; same finding as old 3.6.9-o0-r1).
    o0 = tuple(map(int, version.split('.')[:2])) < (3, 8)
    name = version + ('-o0' if o0 else '')
    name += '-full'
    ssl_root = openssl(steps); dep = libdeps(steps)
    for base in [RUNTIME / 'python' / name / 'install']:
        exe = base / 'bin' / ('python' + '.'.join(version.split('.')[:2]))
        if exe.exists():
            steps.append({'step': 'interpreter', 'status': 'reused' if OLD in exe.parents else 'existing', 'path': str(exe)})
            return exe
    root = RUNTIME / 'python' / name; root.mkdir(parents=True, exist_ok=True)
    url = f'https://www.python.org/ftp/python/{version}/Python-{version}.tgz'
    archive = root / f'Python-{version}.tgz'
    if not archive.exists():
        archive.write_bytes(urllib.request.urlopen(url, timeout=60).read())
    with tarfile.open(archive) as t:
        t.extractall(root, filter='data')
    src = root / f'Python-{version}'
    for name, cmd in [('configure', ['./configure', f'--prefix={root}/install', '--without-ensurepip'] + (['CFLAGS=-O0'] if o0 else [])
                       + ([f'--with-openssl={ssl_root}'] if tuple(map(int, version.split('.')[:2])) >= (3, 7) else [])
                       + [f'CPPFLAGS=-I{ssl_root}/include -I{dep}/include', f'LDFLAGS=-L{ssl_root}/lib -Wl,-rpath,{ssl_root}/lib -L{dep}/lib -Wl,-rpath,{dep}/lib']),
                      ('make', ['make', '-j4']), ('install', ['make', 'altinstall'])]:
        code = run(cmd, src, root / f'{name}.log')
        if code != 0:
            steps.append({'step': 'interpreter', 'status': 'failed', 'phase': name, 'exit': code})
            return None
    exe = root / 'install' / 'bin' / ('python' + '.'.join(version.split('.')[:2]))
    probe = subprocess.run([str(exe), '-c', 'import ssl, zlib, bz2, lzma, sqlite3; print(ssl.OPENSSL_VERSION)'], capture_output=True, text=True)
    if probe.returncode != 0:
        steps.append({'step': 'interpreter', 'status': 'ssl_missing', 'path': str(exe)})
        return None
    steps.append({'step': 'interpreter', 'status': 'built', 'path': str(exe), 'ssl': probe.stdout.strip(),
                  'source_url': url, 'source_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
    return exe


def source(github, commit, dest, steps, label):
    owner_repo = github.rstrip('/').split('github.com/')[1]
    url = f'https://codeload.github.com/{owner_repo}/tar.gz/{commit}'
    archive = dest.parent / f'{label}-{commit}.tar.gz'
    if not archive.exists():
        archive.write_bytes(urllib.request.urlopen(url, timeout=120).read())
    if dest.exists():
        shutil.rmtree(dest)
    tmp = dest.parent / (label + '-extract'); shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir()
    with tarfile.open(archive) as t:
        t.extractall(tmp, filter='data')
    (inner,) = list(tmp.iterdir()); inner.rename(dest); tmp.rmdir()
    steps.append({'step': 'source', 'label': label, 'commit': commit, 'url': url,
                  'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})


def requirements(path, project, version='3.8'):
    have = tuple(map(int, version.split('.')[:2]))
    if not path.exists():
        return []
    raw = path.read_bytes()
    text = raw.decode('utf-16') if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else raw.decode('utf-8', 'replace')
    keep = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('-e ') or 'git+' in line:
            continue
        name = re.split(r'[=<>~! ]', line)[0].lower().replace('_', '-')
        if name in (project.lower(), project.lower().replace('-', '')) or (name.startswith(project.lower()) and 'dev' in line):
            continue  # the project under test comes from the checkout
        if name in WINDOWS_ONLY:
            continue  # not installable on Linux
        if name in BACKPORTS and have >= BACKPORTS[name]:
            continue  # stdlib provides it; installing the backport breaks the interpreter
        keep.append(line)
    return keep


def packages(exe, version, reqs, extra, root, steps):
    """Target-interpreter pip (built with OpenSSL) so markers/tags match the target Python."""
    pkgs = root / 'packages'; wheels = root / 'wheels'; tools = root / 'tools'
    wheels.mkdir(exist_ok=True); tools.mkdir(exist_ok=True)
    short = ''.join(version.split('.')[:2])
    req_file = root / 'requirements.used.txt'; req_file.write_text('\n'.join(reqs) + '\n')
    extra_file = root / 'requirements.extra.txt'; extra_file.write_text('\n'.join(extra) + '\n')
    if not (reqs + extra):
        pkgs.mkdir(exist_ok=True)
        steps.append({'step': 'packages', 'status': 'none_required'}); return pkgs
    # Installer toolchain (pure-python wheels fetched by host pip).
    pip_pin = 'pip==21.3.1' if int(short) < 37 else 'pip==23.0.1'
    tool_code = run([sys.executable, '-m', 'pip', 'download', '--no-deps', '-d', str(tools), '--only-binary=:all:',
                     '--python-version', short, pip_pin,
                     'setuptools==44.1.1' if int(short) < 37 else 'setuptools==59.6.0', 'wheel==0.37.1'],
                    root, root / 'pip-tools.log')
    if tool_code != 0:
        steps.append({'step': 'packages', 'status': 'download_failed', 'exit': ['tools', tool_code]}); return None
    pip_wheel = next(tools.glob('pip-*.whl')); site = tools / 'site'
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'LANG': 'C.UTF-8', 'PYTHONPATH': str(pip_wheel),
           'SSL_CERT_FILE': '/etc/ssl/certs/ca-certificates.crt', 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
           'PIP_CACHE_DIR': str(RUNTIME / 'pip-cache')}  # shared download cache; installs stay per task
    code = run([str(exe), '-m', 'pip', 'install', '--no-deps', '--no-index', '--find-links', str(tools),
                '--target', str(site), 'setuptools', 'wheel'], root, root / 'pip-tools-install.log', env=env)
    if code != 0:
        steps.append({'step': 'packages', 'status': 'toolchain_failed', 'exit': code}); return None
    env['PYTHONPATH'] = os.pathsep.join([str(pip_wheel), str(site)])
    # BugsInPy pins are a full freeze: fetch exactly them, no resolution.
    code = 0; dropped = []
    for attempt in range(12):
        if not reqs:
            break
        req_file.write_text('\n'.join(reqs) + '\n')
        log = root / f'pip-download-{attempt}.log'
        code = run([str(exe), '-m', 'pip', 'download', '--no-deps', '--no-build-isolation', '-d', str(wheels),
                    '-r', str(req_file)], root, log, env=env)
        missing = re.findall(r'No matching distribution found for (\S+)', log.read_text(errors='replace'))
        if code == 0 or not missing:
            break
        # A pin no longer published on PyPI: drop and record; controls decide eligibility.
        dropped += [m for m in missing if m in reqs]
        reqs = [r for r in reqs if r not in missing]
    if code == 0 and extra:  # test-runner pins: resolve dependencies under the BugsInPy pins
        code = run([str(exe), '-m', 'pip', 'download', '--no-build-isolation', '-d', str(wheels),
                    '-c', str(req_file), '-r', str(extra_file)], root, root / 'pip-download-extra.log', env=env)
    if code != 0:
        steps.append({'step': 'packages', 'status': 'download_failed', 'exit': code}); return None
    code = run([str(exe), '-m', 'pip', 'install', '--no-deps', '--no-index', '--no-build-isolation',
                '--find-links', str(wheels), '--target', str(pkgs), *[str(w) for w in sorted(wheels.iterdir())]],
               root, root / 'pip-install.log', env=env)
    if code != 0:
        steps.append({'step': 'packages', 'status': 'install_failed', 'exit': code}); return None
    steps.append({'step': 'packages', 'status': 'installed', 'requirements': len(reqs), 'extra': extra, 'dropped_unavailable': dropped,
                  'files': {w.name: hashlib.sha256(w.read_bytes()).hexdigest() for w in sorted(wheels.iterdir())}})
    return pkgs


def control(exe, checkout, pkgs, pythonpath, commands, root, label):
    env = {'PATH': f'{exe.parent}:/usr/bin:/bin', 'HOME': str(root / 'home'), 'LANG': 'C.UTF-8',
           'PYTHONDONTWRITEBYTECODE': '1',  # plugin autoload allowed: BugsInPy pins plugins (e.g. pytest-mock)
           'PYTHONPATH': os.pathsep.join([str(checkout / p) for p in pythonpath]
                                         + ([str(checkout / 'lib')] if any('build/lib' in p for p in pythonpath) and (checkout / 'lib').is_dir() else [])
                                         + [str(checkout), str(pkgs), str(root / 'tools' / 'site')])}
    (root / 'home').mkdir(exist_ok=True)
    bindir = root / 'bin'; bindir.mkdir(exist_ok=True)
    for alias in ('python', 'python3'):
        link = bindir / alias
        if not link.exists():
            link.symlink_to(exe)
    env['PATH'] = f'{bindir}:' + env['PATH']
    results = []
    for i, cmd in enumerate(commands):
        argv = cmd.split()
        if argv[0] == 'pytest':
            argv = ['python', '-m', 'pytest', '-p', 'no:cacheprovider'] + argv[1:]
        log = root / f'control-{label}-{i}.log'
        code = run(argv, checkout, log, env=env, timeout=300)
        results.append({'command': argv, 'exit': code, 'log': str(log)})
    return results


def main(project, bug):
    pdir = BUGSINPY / 'projects' / project; bdir = pdir / 'bugs' / bug
    meta = info(bdir / 'bug.info'); github = info(pdir / 'project.info')['github_url']
    root = RUNTIME / ROUND / f'{project}-{bug}'; root.mkdir(parents=True, exist_ok=True)
    result_path = root / 'result.json'
    if result_path.exists():
        raise SystemExit('Existing result; inspect rather than rerun')
    steps = []; outcome = 'incomplete'
    try:
        exe = interpreter(meta['python_version'], steps)
        if exe:
            source(github, meta['buggy_commit_id'], root / 'buggy', steps, 'buggy')
            source(github, meta['fixed_commit_id'], root / 'fixed', steps, 'fixed')
            commands = [c.strip() for c in (bdir / 'run_test.sh').read_text().splitlines() if c.strip()]
            extra = ['pytest==' + m.group(1) for m in re.finditer(r'pytest==([\d.]+)', (bdir / 'setup.sh').read_text())] \
                if (bdir / 'setup.sh').exists() else []
            reqs = requirements(bdir / 'requirements.txt', project, meta['python_version'])
            if extra:  # setup.sh pin overrides only an exact pytest pin
                reqs = [r for r in reqs if not re.match(r'pytest\s*==', r, re.I)]
            elif any(c.startswith('pytest') for c in commands) and not any(re.match(r'pytest\s*==', r, re.I) for r in reqs):
                extra = ['pytest==6.2.5' if tuple(map(int, meta['python_version'].split('.')[:2])) >= (3, 6) else 'pytest==4.6.11']
            pkgs = packages(exe, meta['python_version'], reqs, extra, root, steps)
            if pkgs:
                prepared = []
                for rel in [t for t in meta.get('test_file', '').split(';') if t.strip()]:
                    src_t, dst_t = root / 'fixed' / rel.strip(), root / 'buggy' / rel.strip()
                    dst_t.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src_t, dst_t)
                    prepared.append({'copied_fixed_test_into_buggy': rel.strip()})
                for line in ((bdir / 'setup.sh').read_text().splitlines() if (bdir / 'setup.sh').exists() else []):
                    if line.strip().startswith('touch '):
                        for rel in line.split()[1:]:
                            for side in ('buggy', 'fixed'):
                                (root / side / rel).parent.mkdir(parents=True, exist_ok=True); (root / side / rel).touch()
                        prepared.append({'setup_touch': line.strip()})
                    elif line.strip():
                        prepared.append({'setup_skipped': line.strip()})
                for rel, text in SHIMS.get(project, {}).items():
                    for side in ('buggy', 'fixed'):
                        if not (root / side / rel).exists():
                            (root / side / rel).write_text(text)
                    prepared.append({'shim': rel})
                steps.append({'step': 'prepare', 'actions': prepared})
                pythonpath = [p for p in meta.get('pythonpath', '').split(':') if p]
                buggy = control(exe, root / 'buggy', pkgs, pythonpath, commands, root, 'buggy')
                fixed = control(exe, root / 'fixed', pkgs, pythonpath, commands, root, 'fixed')
                steps.append({'step': 'controls', 'buggy': buggy, 'fixed': fixed})
                ok = any(r['exit'] not in (0, 'TIMEOUT') for r in buggy) and all(r['exit'] == 0 for r in fixed)
                outcome = 'controls_as_expected' if ok else 'controls_unexpected'
    except Exception as error:  # record and stop; never retried automatically
        steps.append({'step': 'error', 'type': type(error).__name__, 'message': str(error)[:300]})
        outcome = 'error'
    result_path.write_text(json.dumps({'project': project, 'bug': bug, 'bug_info': meta, 'outcome': outcome,
                                       'steps': steps, 'model_calls': 0}, indent=2) + '\n')
    print(project, bug, outcome)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
