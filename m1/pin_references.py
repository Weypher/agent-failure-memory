"""Copy buggy/fixed versions of each DEV task's reference-changed files next to the inputs
so lesson scoring uses hash-pinned files. Evaluation-only; never mounted or prompted.
Usage: python3 m1/pin_references.py <manifest.json>
"""
import json, shutil, sys
from pathlib import Path


def main(manifest_path):
    m = Path(manifest_path); manifest = json.loads(m.read_text())
    for t in manifest['tasks']:
        fixed = Path(t['fixed_checkout']); buggy = fixed.parent / 'buggy'
        dest = m.parent / 'reference' / t['task_id'].replace('/', '-')
        for side, src in (('buggy', buggy), ('fixed', fixed)):
            for rel in t['reference_changed']:
                if (src / rel).is_file():
                    (dest / side / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src / rel, dest / side / rel)
    print('pinned', len(manifest['tasks']))


if __name__ == '__main__':
    main(sys.argv[1])
