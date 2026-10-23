"""Review gate and bounded transports for M1 paid stages (pattern of the reviewed old gate).

check(): exact frozen scope with hashed files, two fresh PASS reviews bound to the scope
hash, original ledger, fixed model and stage caps. Bounded: every call re-checks the
gate, follows the orchestrator's declared call sequence, and stops the stage on failure.
"""
import hashlib, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.agent import ModelOutputIncomplete  # noqa: E402

LEDGER = Path('/mnt/d/doc/niw/researches/budgted_gate_fail_learning/code/runs/budget.sqlite')
MODEL = 'gpt-5.4-mini-2026-03-17'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_identity(path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runner'))
    from repository_snapshot import inventory, digest
    return digest(inventory(path))


def trees_changed(trees):
    return [p for p, identity in trees.items() if tree_identity(p) != identity]


def check(scope_path, receipt_path, permission, route, limits, required, deep=True, verify_files=None, verify_trees=None):
    """deep/None: all files (+ all trees if deep). verify_files/verify_trees: hash only these subsets
    (used per attempt/per call in large scopes; a full check runs at stage start)."""
    scope = json.loads(Path(scope_path).read_text()); receipt = json.loads(Path(receipt_path).read_text())
    identity = sha(scope_path)
    if scope.get('permission') != permission or receipt.get('permission') != permission or scope.get('frozen') is not True \
            or receipt.get('scope_sha256') != identity:
        raise ValueError('Exact frozen permission required')
    files = scope['files']
    if not set(map(str, required)).issubset(files):
        raise ValueError('Incomplete reviewed execution scope')
    if any(not Path(path).is_absolute() for path in files):
        raise ValueError('Relative path in scope')
    targets = files if verify_files is None else {p: files[p] for p in map(str, verify_files)}
    for path, h in targets.items():
        if sha(path) != h:
            raise ValueError('Reviewed files changed')
    trees = scope.get('trees', {})
    tree_targets = (trees if deep else {}) if verify_trees is None else {t: trees[t] for t in verify_trees}
    if trees_changed(tree_targets):
        raise ValueError('Runtime tree changed')
    reviews = receipt.get('reviews', [])
    if len(reviews) != 2 or {r['role'] for r in reviews} != {'implementation', 'validity'}:
        raise ValueError('Two reviewers required')
    ids = {r['reviewer_id'] for r in reviews}
    if len(ids) != 2 or not scope.get('prior_reviewer_ids') or ids & set(scope['prior_reviewer_ids']):
        raise ValueError('Fresh reviewers required')
    for r in reviews:
        if r.get('fork_turns') != 'none' or sha(r['path']) != r['sha256']:
            raise ValueError('Report identity mismatch')
        report = json.loads(Path(r['path']).read_text())
        for k, v in {'verdict': 'PASS', 'permission': permission, 'scope_sha256': identity,
                     'role': r['role'], 'reviewer_id': r['reviewer_id']}.items():
            if report.get(k) != v:
                raise ValueError('Review does not approve this stage')
    if Path(scope['ledger']).resolve() != LEDGER or scope['stage_limits_nusd'] != limits or scope['route'] != route:
        raise ValueError('Original ledger and stage ceilings required')
    model = scope['model_config']
    if model['id'] != MODEL or model['input_token_limit'] != 96000 or model['max_output_tokens'] != 3000:
        raise ValueError('Fixed model and token ceilings required')
    return scope


class Bounded:
    """Wraps one official transport; the orchestrator declares each call sequence first."""
    kind = 'live'

    def __init__(self, transport, gate, route, used):
        self.transport, self.gate, self.route = transport, gate, route
        self.used = used          # shared set of prefixes across transports: no prefix twice
        self.prefix = None; self.limit = 0; self.turn = 0; self.failed = [False]

    def share_failure(self, other):
        self.failed = other.failed

    def clear_failure(self):
        """Circuit-breaker design: after an isolated, recorded transient provider error the caller may continue."""
        self.failed[0] = False; self.prefix = None

    def begin(self, prefix, limit):
        if self.failed[0] or self.prefix is not None or prefix in self.used or not prefix.startswith(self.route + '/'):
            raise ValueError('Unscheduled call sequence')
        self.used.add(prefix); self.prefix, self.limit, self.turn = prefix, limit, 0

    def end(self):
        self.prefix = None

    def complete(self, messages, call_id):
        from harness.artifacts import digest, write_once
        if self.failed[0] or self.prefix is None or self.turn >= self.limit or call_id != f'{self.prefix}/{self.turn + 1}':
            raise ValueError('Unscheduled call')
        self.gate()
        self.turn += 1
        try:
            reply = self.transport.complete(messages, call_id)
            raw = json.loads((self.transport.response_dir / (digest(call_id) + '.json')).read_text())
            write_once(self.transport.response_dir / (digest(call_id) + '.finish.json'),
                       {'call_id': call_id, 'finish_reason': raw['response']['choices'][0].get('finish_reason')})
            return reply
        except ModelOutputIncomplete:
            # Settled, retained, model-caused: the caller classifies it; the stage continues.
            write_once(self.transport.response_dir / (digest(call_id) + '.finish.json'),
                       {'call_id': call_id, 'finish_reason': 'incomplete_or_refused'})
            raise
        except BaseException:
            self.failed[0] = True
            raise
