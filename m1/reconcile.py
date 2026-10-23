"""Read-only post-run reconciliation for an M1 stage. Never requests a model.

Recomputes each ledger call on the stage route from its retained raw response usage and
the scope prices, checks every call has request/response artifacts, and totals the
original ledger. Output is written once next to the stage output.
Usage: python3 m1/reconcile.py <scope.json> <out.json>
"""
import hashlib, json, sqlite3, sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.artifacts import digest  # noqa: E402


def main(scope_path, target):
    scope = json.loads(Path(scope_path).read_text()); out = Path(scope['output_root'])
    if not any((out / n).exists() for n in ('complete.json', 'stopped.json', 'entry-error.json')):
        raise SystemExit('Stage has no terminal marker')
    prices = scope['prices']
    with sqlite3.connect(Path(scope['ledger']).as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT id,reserved,charged,status,usage FROM calls WHERE id LIKE ? ORDER BY created,id',
                          (scope['route'] + '/%',)).fetchall()
        total = db.execute('SELECT COUNT(*),COALESCE(SUM(charged),0),COALESCE(SUM(CASE WHEN charged IS NULL THEN reserved END),0) FROM calls').fetchone()
    calls = []
    for call_id, reserved, charged, status, _ in rows:
        base = out / 'responses' / digest(call_id)
        entry = {'call_id': call_id, 'reserved_nusd': reserved, 'charged_nusd': charged, 'status': status,
                 'request': Path(str(base) + '.request.json').exists(), 'response': Path(str(base) + '.json').exists()}
        usage = json.loads(Path(str(base) + '.json').read_text())['response'].get('usage') if entry['response'] else None
        if entry['response'] and not isinstance(usage, dict):
            entry['reconciliation'] = 'RESPONSE_WITHOUT_USAGE_REVIEW'
        elif entry['response']:
            cached = usage.get('prompt_tokens_details', {}).get('cached_tokens', 0)
            cost = (Decimal(usage['prompt_tokens'] - cached) * Decimal(str(prices['input_usd_per_million']))
                    + Decimal(cached) * Decimal(str(prices['cached_input_usd_per_million']))
                    + Decimal(usage['completion_tokens']) * Decimal(str(prices['output_usd_per_million']))) * 1000
            entry['recomputed_nusd'] = int(cost.to_integral_value())
            entry['reconciliation'] = 'MATCH' if entry['recomputed_nusd'] == charged else 'MISMATCH'
        else:
            entry['reconciliation'] = 'NO_RAW_RESPONSE_REVIEW'
        calls.append(entry)
    result = {'kind': 'READ_ONLY_RAW_LEDGER_RECOMPUTATION', 'scope_sha256': hashlib.sha256(Path(scope_path).read_bytes()).hexdigest(),
              'calls': calls, 'all_matched': all(c['reconciliation'] == 'MATCH' for c in calls),
              'stage': {'requests': len(rows), 'charged_nusd': sum(r[2] or 0 for r in rows),
                        'pending_nusd': sum(r[1] for r in rows if r[2] is None)},
              'cumulative': {'requests': total[0], 'charged_nusd': total[1], 'pending_nusd': total[2],
                             'committed_nusd': total[1] + total[2]}}
    with Path(target).open('x') as f:
        json.dump(result, f, indent=1); f.write('\n')
    print(json.dumps({'all_matched': result['all_matched'], **result['stage']}))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
