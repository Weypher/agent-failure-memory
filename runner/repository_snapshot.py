"""Host-side content-addressed snapshots of stopped candidate workspaces.

No symlinks are followed. A snapshot is evidence, not approval to score a patch.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def inventory(root, blobs=None):
    root = Path(root).resolve(strict=True)
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort(); files.sort()
        for name in sorted(dirs + files):
            path = Path(directory)/name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            row = {'mode':stat.S_IMODE(info.st_mode)}
            if stat.S_ISLNK(info.st_mode):
                row.update(kind='symlink', target=os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                row.update(kind='directory')
            elif stat.S_ISREG(info.st_mode):
                with path.open('rb') as stream:
                    data = stream.read()
                sha = hashlib.sha256(data).hexdigest()
                row.update(kind='file', sha256=sha, size=len(data))
                if blobs is not None:
                    target = Path(blobs)/sha
                    if target.exists():
                        if target.read_bytes() != data:
                            raise ValueError('Corrupt blob')
                    else:
                        with target.open('xb') as stream:
                            stream.write(data)
            else:
                raise ValueError('Unsupported special file: '+relative)
            result[relative] = row
    return result


def capture(root, output):
    output = Path(output).absolute()
    root = Path(root).resolve(strict=True)
    if output == root or root in output.parents:
        raise ValueError('Snapshot output must be outside workspace')
    output.mkdir(parents=True, exist_ok=False)
    (output/'blobs').mkdir()
    first = inventory(root, output/'blobs')
    second = inventory(root)
    if first != second:
        raise ValueError('Workspace changed during capture')
    manifest = {'entries':first, 'identity':digest(first)}
    with (output/'manifest.json').open('x') as stream:
        json.dump(manifest, stream, sort_keys=True, indent=2)
    return manifest


def delta(before, after):
    return {p:{'before':before.get(p), 'after':after.get(p)}
            for p in sorted(set(before)|set(after)) if before.get(p) != after.get(p)}


def restore(snapshot, destination):
    """Materialize an exact snapshot; reject unsafe paths/links before writing."""
    snapshot = Path(snapshot)
    manifest = json.loads((snapshot/'manifest.json').read_text())
    entries = manifest['entries']
    if digest(entries) != manifest['identity']:
        raise ValueError('Manifest identity mismatch')
    for name, entry in entries.items():
        path = PurePosixPath(name)
        if not name or path.is_absolute() or '..' in path.parts or str(path) != name:
            raise ValueError('Unsafe entry path')
        for parent in path.parents:
            if str(parent) != '.' and entries.get(str(parent), {}).get('kind') != 'directory':
                raise ValueError('Missing directory or symlink ancestor')
        if entry['kind'] == 'symlink':
            target = entry['target']
            normalized = os.path.normpath(str(path.parent / target))
            if os.path.isabs(target) or normalized == '..' or normalized.startswith('../'):
                raise ValueError('External symlink requires separate review')
        elif entry['kind'] == 'file':
            sha = entry['sha256']
            if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
                raise ValueError('Invalid blob identity')
            data = (snapshot/'blobs'/sha).read_bytes()
            if hashlib.sha256(data).hexdigest() != sha or len(data) != entry['size']:
                raise ValueError('Blob identity mismatch')
        elif entry['kind'] != 'directory':
            raise ValueError('Unsupported entry kind')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name, entry in sorted(entries.items(), key=lambda item:(len(PurePosixPath(item[0]).parts),item[0])):
        path = destination/name
        if entry['kind'] == 'directory':
            path.mkdir()
        elif entry['kind'] == 'file':
            with path.open('xb') as stream:
                stream.write((snapshot/'blobs'/entry['sha256']).read_bytes())
            path.chmod(entry['mode'])
        else:
            path.symlink_to(entry['target'])
    for name, entry in sorted(entries.items(), reverse=True):
        if entry['kind'] == 'directory':
            (destination/name).chmod(entry['mode'])
    if inventory(destination) != entries:
        raise ValueError('Restored inventory mismatch')
    return manifest['identity']
