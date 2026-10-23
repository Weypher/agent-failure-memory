"""Offline repository tools; no model transport or paid-entry authorization.

A task specification is trusted host input, never supplied by an agent. All
agent commands execute inside the same namespace and writable working copy.
"""
import json
import os
from pathlib import Path
import signal
import subprocess


def command(spec, workspace, output, argv):
    workspace = Path(workspace).resolve(strict=True)
    output = Path(output).resolve(strict=True)
    if workspace == output or workspace in output.parents or output in workspace.parents:
        raise ValueError('Workspace and output must be disjoint')
    args = [spec['bwrap'], '--unshare-all', '--die-with-parent', '--new-session', '--clearenv']
    for host, guest in spec['readonly_mounts']:
        args += ['--ro-bind', host, guest]
    args += ['--bind', str(workspace), '/work', '--bind', str(output), '/output',
             '--tmpfs', '/tmp', '--proc', '/proc', '--dev', '/dev']
    for relative in spec['protected_paths']:
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Protected paths must be relative')
        host = (workspace / path).resolve(strict=True)
        if workspace not in host.parents:
            raise ValueError('Protected path escapes workspace')
        args += ['--ro-bind', str(host), '/work/' + relative]
    args += ['--chdir', '/work']
    for key, value in spec['environment'].items():
        args += ['--setenv', key, value]
    return args + list(argv)


def execute(spec, workspace, output, argv, timeout=60):
    """Retain full raw output on disk, and terminate the process group on timeout."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cmd = command(spec, workspace, output, argv)
    (output / 'command.json').write_text(json.dumps(cmd, indent=2) + '\n')
    timed_out = False
    with (output / 'raw.log').open('wb') as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    result = {'exit': proc.returncode, 'timeout': timed_out}
    (output / 'execution.json').write_text(json.dumps(result) + '\n')
    return result
