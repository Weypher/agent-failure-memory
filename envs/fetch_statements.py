"""Collect public provenance for task statements of out-of-frame BugsInPy tasks. No model requests.

For each task: fixed-commit message from a local commits-only bare clone; referenced
numbers (#N) in that message; GitHub API records for each referenced number (issue or PR),
and for PRs the issues they reference. Raw API responses are stored unmodified.
Composing statements is a separate, pre-specified rule; this script never writes prompts.

Usage: python3 envs/fetch_statements.py <tasks.json from batch_build> <out_dir>
Unauthenticated GitHub API (60/h): requests are paced and cached; reruns resume.
"""
import json, re, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import trial_build  # noqa: E402

PACE = 62.0
REF = re.compile(r'(?<![\w/])#(\d+)\b')
CLOSING = re.compile(r'\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)', re.I)
_last = [0.0]


def api(url, cache):
    if cache.exists():
        return json.loads(cache.read_text())
    wait = PACE - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
                                               'User-Agent': 'agent-failure-memory-provenance'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = {'status': r.status, 'url': url, 'body': json.loads(r.read())}
    except urllib.error.HTTPError as e:
        data = {'status': e.code, 'url': url, 'body': None}
        if e.code in (403, 429):  # rate limited: do not cache, stop cleanly
            raise SystemExit('Rate limited; rerun later to resume')
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, indent=1) + '\n')
    return data


def clone(owner_repo, base):
    path = base / 'clones' / (owner_repo.replace('/', '__') + '.git')
    if not path.exists():
        subprocess.run(['git', 'clone', '--bare', '--filter=tree:0', '--quiet',
                        f'https://github.com/{owner_repo}.git', str(path)], check=True, timeout=1800)
    return path


def main(tasks_file, out):
    out = Path(out); rows = []
    tasks = [r['task'] for r in json.loads(Path(tasks_file).read_text())['rows']]
    for task in tasks:
        project, bug = task.split('/')
        pdir = trial_build.BUGSINPY / 'projects' / project
        meta = trial_build.info(pdir / 'bugs' / bug / 'bug.info')
        owner_repo = trial_build.info(pdir / 'project.info')['github_url'].rstrip('/').split('github.com/')[1]
        repo = clone(owner_repo, out)
        sha = meta['fixed_commit_id']
        message = subprocess.run(['git', '-C', str(repo), 'log', '-1', '--format=%B', sha],
                                 capture_output=True, text=True).stdout
        numbers = sorted(set(REF.findall(message)), key=int)
        records = {}
        cache = out / 'api' / owner_repo.replace('/', '__')
        if not numbers:  # commit not referencing a number: ask which PR contains it
            pulls = api(f'https://api.github.com/repos/{owner_repo}/commits/{sha}/pulls', cache / f'commit-{sha}-pulls.json')
            numbers = [str(p['number']) for p in (pulls['body'] or [])]
        queue = list(numbers)
        while queue:
            n = queue.pop(0)
            if n in records:
                continue
            rec = api(f'https://api.github.com/repos/{owner_repo}/issues/{n}', cache / f'issue-{n}.json')
            body = rec['body'] or {}
            kind = 'pull' if body.get('pull_request') else ('issue' if body else 'missing')
            records[n] = {'kind': kind, 'status': rec['status'], 'cache': str(cache / f'issue-{n}.json')}
            if kind == 'pull':  # issues a PR declares it closes
                for m in CLOSING.findall((body.get('title') or '') + '\n' + (body.get('body') or '')):
                    queue.append(m)
        rows.append({'task': task, 'repo': owner_repo, 'fixed_commit': sha, 'commit_message': message,
                     'commit_refs': sorted(set(REF.findall(message)), key=int), 'records': records,
                     'has_issue': any(r['kind'] == 'issue' for r in records.values())})
        (out / 'provenance.json').write_text(json.dumps({'kind': 'PUBLIC_PROVENANCE_NOT_PROMPTS', 'rows': rows,
                                                        'model_calls': 0}, indent=1) + '\n')
    print(sum(r['has_issue'] for r in rows), 'of', len(rows), 'tasks link at least one issue')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
