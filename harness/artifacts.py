"""Content identities and append-only artifacts; no credentials belong here."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_once(path, value):
    """An existing artifact may only be reused when its content is identical."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (canonical(value) + "\n").encode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.pending-', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic publication without replacement. A killed writer can leave only
        # an unreferenced .pending file, never a partial authoritative result.
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError(f"Refusing to overwrite artifact: {path}")
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    return value


class EventLog:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("x", encoding="utf-8")

    def emit(self, event, **payload):
        self.handle.write(canonical({"event": event, "time_unix": time.time(), **payload}) + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def close(self):
        self.handle.close()


def source_identity(scope='all'):
    files = [ROOT / "environment_adapter.py", ROOT / "requirements.txt"]
    if scope=='episode':
        files += [ROOT/'harness'/f for f in ('__init__.py','artifacts.py','memory.py','agent.py','runner.py')]
    else:
        files += sorted((ROOT / "harness").rglob("*.py"))
    entries = {str(p.relative_to(ROOT)): file_hash(p) for p in files}
    try:
        git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                           text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        git_head = None  # Content hashes also identify exported/archive bundles.
    return {"sha256": digest(entries), "files": entries, "git_head": git_head}
