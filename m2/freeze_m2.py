"""Freeze the M2 scope for independent review. No model requests.

  python3 m2/freeze_m2.py <manifest.json> <protocol-draft.json> <review_dir> <output_root>
"""
import hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'm2'))
import run_m2  # noqa: E402
from run_m2 import stage  # noqa: E402

R4_SCOPE = Path('/runtime/m1-main-paid-r4/scope.json')


def main(manifest_path, protocol_src, review_dir, output_root):
    review = Path(review_dir).resolve(); review.mkdir(parents=True, exist_ok=False)
    manifest_path = str(Path(manifest_path).resolve()); manifest = json.loads(Path(manifest_path).read_text())
    protocol = json.loads(Path(protocol_src).read_text())
    protocol.update(status='FROZEN_FOR_INDEPENDENT_REVIEW', frozen=True,
                    frame_counts={'tasks': len(manifest['tasks']), 'first_attempt_repetitions': manifest['first_attempt_repetitions'],
                                  'workers': manifest['workers'], 'arms': manifest['arms']})
    (review / 'protocol.json').write_text(json.dumps(protocol, indent=1) + '\n')
    r4 = json.loads(R4_SCOPE.read_text())
    r8 = json.loads((ROOT / 'planning/m1-main-r1/review-r8/receipt.json').read_text())
    model = dict(r4['model_config'], id=run_m2.MODEL_ID)
    prices = {'authorization_evidence': ('User authorization 2026-10-28 PDT: cumulative USD220 incl. history; stage afm-m2-gpt54 '
                                         'capped at USD120 (ledger amendment b92baaa1e11c45427990d67c8464535858f61666a1b926c9be858f1dd896508f).'),
              'authorized_phases': ['dev', 'pilot', 'main', 'review'], 'authorized_usd': 220,
              'cached_input_usd_per_million': 0.25, 'input_usd_per_million': 2.5, 'output_usd_per_million': 15,
              'price_checked': '2026-10-28', 'price_source': 'https://developers.openai.com/api/docs/models/gpt-5.4'}
    scope = {'permission': run_m2.PERMISSION, 'frozen': True, 'route': run_m2.STAGE, 'stage_limits_nusd': run_m2.ALL_LIMITS,
             'protocol': str(review / 'protocol.json'), 'manifest': manifest_path, 'model_config': model, 'prices': prices,
             'ledger': r4['ledger'], 'credentials_file': r4['credentials_file'], 'output_root': output_root,
             'prior_reviewer_ids': sorted(set(r4['prior_reviewer_ids']) | {r['reviewer_id'] for r in r8['reviews']}
                                       | {'reviewer/m2_gpt54_impl_r1', 'reviewer/m2_gpt54_validity_r1'})}
    req, _ = run_m2.required(scope)
    files = set(req) | {str(ROOT / p) for p in ['m2/analyze_m2.py', 'm2/freeze_m2.py', 'm1/analyze_main.py', 'm1/accuracy.py',
                                                'planning/m2-gpt54/protocol-draft.json', 'AGENTS.md',
                                                'm1/posthoc_revision.py', 'planning/m1-main-r1/analysis-final/posthoc-revision-r1.json',
                                                'planning/m1-main-r1/analysis-final/analysis-C.json', 'm1/fullsuite.py',
                                                'planning/m2-gpt54/review-r1/implementation/report.json',
                                                'planning/m2-gpt54/review-r1/validity/report.json']}
    files |= {str(f) for f in (Path(manifest_path).parent / 'reference').rglob('*') if f.is_file()}
    trees = set()
    for t in manifest['tasks']:
        files |= {str(f) for f in (Path(t['initial_snapshot']) / 'blobs').iterdir() if f.is_file()}
        files.add(str(Path(t['sandbox_spec']).parent / 'task.json'))
        spec = json.loads(Path(t['sandbox_spec']).read_text())
        trees |= {h for h, _ in spec['readonly_mounts'] if h.startswith('/home/') and Path(h).is_dir()}
        files |= {h for h, _ in spec['readonly_mounts'] if h.startswith('/home/') and Path(h).is_file()}
        files.add(spec['bwrap'])
    scope['files'] = {f: hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in sorted(files)}
    scope['trees'] = {t: stage.tree_identity(t) for t in sorted(trees)}
    (review / 'scope.json').write_text(json.dumps(scope, indent=1) + '\n')
    print(json.dumps({'files': len(scope['files']), 'trees': len(scope['trees']),
                      'sha256': hashlib.sha256((review / 'scope.json').read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main(*sys.argv[1:5])
