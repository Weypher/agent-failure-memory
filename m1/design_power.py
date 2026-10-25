"""POST-HOC design calculation (not preregistered): task clusters needed to detect a given gain.

Uses the task-cluster bootstrap standard error implied by each primary 95% interval (width / 3.92) and the
normal approximation n_needed = n_tasks * ((z_{1-a/2} + z_{power}) * se / d)^2.
Usage: python3 m1/design_power.py <analysis-C.json> <out.json>
"""
import json, sys
from pathlib import Path
from statistics import NormalDist

z = NormalDist().inv_cdf


def main(src, target):
    a = json.loads(Path(src).read_text())
    rows = []
    for c in a['primary_itt']:
        se = (c['ci95'][1] - c['ci95'][0]) / 3.92
        need = {f'd={d}_alpha={al}': round(((z(1 - al / 2) + z(0.8)) * se / d) ** 2 * c['n_tasks'])
                for d in (0.05, 0.07, 0.10) for al in (0.05, 0.0125)}
        rows.append({'contrast': c['contrast'], 'n_tasks_with_failures': c['n_tasks'], 'se': round(se, 4), 'tasks_needed_power80': need})
    Path(target).write_text(json.dumps({'kind': 'POST_HOC_DESIGN_CALCULATION', 'source': src, 'rows': rows}, indent=1) + '\n')
    print(json.dumps(rows))


if __name__ == '__main__':
    main(*sys.argv[1:3])
