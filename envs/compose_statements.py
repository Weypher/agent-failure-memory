"""Apply the draft statement rule (planning/statement-rule-draft-r1.json) to fetched provenance.

No model requests. Uses only public provenance and the reference diff (for leakage
screening, never for the statement). Writes one statement per eligible task plus a
summary with every exclusion reason. Rerunnable: outputs are derived, inputs unchanged.
Usage: python3 envs/compose_statements.py <provenance.json> <env_batch.json> <out_dir>
"""
import difflib, hashlib, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import trial_build  # noqa: E402
from fetch_statements import CLOSING, REF  # noqa: E402

MAX_CHARS = 8000
WRAPPER = 'Resolve the following issue reported for this repository.\n\n'
HTML_COMMENT = re.compile(r'<!--.*?-->', re.S)


def load(path):
    return json.loads(Path(path).read_text())['body']


def linked_issues(row):
    """Issues the fix explicitly closes; else a sole plain commit reference that is an issue."""
    recs = row['records']
    closing = set(CLOSING.findall(row['commit_message']))
    for n, rec in recs.items():
        if rec['kind'] == 'pull':
            body = load(rec['cache'])
            closing |= set(CLOSING.findall((body.get('title') or '') + '\n' + (body.get('body') or '')))
    chosen = sorted((n for n in closing if recs.get(n, {}).get('kind') == 'issue'), key=int)
    basis = 'closing_reference'
    if not chosen and len(row['commit_refs']) == 1 and recs.get(row['commit_refs'][0], {}).get('kind') == 'issue':
        chosen, basis = row['commit_refs'], 'sole_commit_reference'
    return chosen, basis


def added_lines(task):
    """Added non-test source lines of the reference fix (buggy -> fixed)."""
    root = trial_build.RUNTIME / trial_build.ROUND / task.replace('/', '-')
    added = set()
    for fixed in (root / 'fixed').rglob('*.py'):
        rel = fixed.relative_to(root / 'fixed')
        if any(part in ('test', 'tests', 'testing') or part.startswith('test_') for part in rel.parts):
            continue
        buggy = root / 'buggy' / rel
        old = buggy.read_text(errors='replace').splitlines() if buggy.exists() else []
        new = fixed.read_text(errors='replace').splitlines()
        if old == new:
            continue
        for line in difflib.unified_diff(old, new, lineterm='', n=0):
            if line.startswith('+') and not line.startswith('+++') and len(line[1:].strip()) >= 20:
                added.add(line[1:].strip())
    return added


def main(provenance, env_batch, out):
    out = Path(out); (out / 'tasks').mkdir(parents=True, exist_ok=True)
    env = {r['task']: r['outcome'] for r in json.loads(Path(env_batch).read_text())['rows']}
    rows = []
    for row in json.loads(Path(provenance).read_text())['rows']:
        task = row['task']; entry = {'task': task, 'environment': env.get(task, 'not_built')}
        issues, basis = linked_issues(row)
        if not issues:
            entry['eligible'], entry['reason'] = False, 'no_linked_issue'
        else:
            parts, sources = [], []
            for n in issues:
                cache = row['records'][n]['cache']; body = load(cache)
                text = HTML_COMMENT.sub('', (body.get('body') or '').replace('\r\n', '\n').replace('\r', '\n')).strip()
                parts.append(f"Issue title: {body.get('title', '').strip()}\n\n{text}")
                sources.append({'issue': int(n), 'url': body.get('html_url'), 'created_at': body.get('created_at'),
                                'api_sha256': hashlib.sha256(Path(cache).read_bytes()).hexdigest()})
            statement = WRAPPER + '\n\n'.join(parts) + '\n'
            leaked = [l for l in added_lines(task) if l in statement] if entry['environment'] == 'controls_as_expected' else None
            entry.update(basis=basis, sources=sources, chars=len(statement))
            if leaked is None:
                entry['eligible'], entry['reason'] = False, 'environment_not_eligible'
            elif leaked:
                entry['eligible'], entry['reason'], entry['leaked_lines'] = False, 'solution_leakage', leaked[:5]
            elif len(statement) > MAX_CHARS:
                entry['eligible'], entry['reason'] = False, 'too_long'
            else:
                entry['eligible'] = True
                (out / 'tasks' / (task.replace('/', '-') + '.txt')).write_bytes(statement.encode())
                entry['statement_sha256'] = hashlib.sha256(statement.encode()).hexdigest()
        rows.append(entry)
    summary = {'kind': 'DERIVED_STATEMENTS_DRAFT_RULE_NOT_FROZEN', 'rule': 'planning/statement-rule-draft-r1.json',
               'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'rows': rows, 'model_calls': 0}
    (out / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    counts = {}
    for r in rows:
        key = 'eligible' if r['eligible'] else r['reason']
        counts[key] = counts.get(key, 0) + 1
    print(json.dumps(counts))


if __name__ == '__main__':
    main(*sys.argv[1:4])
