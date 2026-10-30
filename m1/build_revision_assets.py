"""Tables for the post hoc revision analyses (FORGE 2027). Reads analysis-final/*.json, writes paper-forge/generated/.

  python3 m1/build_revision_assets.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / 'planning/m1-main-r1/analysis-final'
GEN = ROOT / 'paper-forge/generated'
ARMS = ['NONE', 'HISTORY', 'REFLECT', 'SIGNAL', 'BOTH']


def pct(x):
    return f'{100 * x:.1f}'


def ci(e):
    lo, hi = e['ci95']
    f = lambda v: f'{100 * v:+.1f}'.replace('-', '$-$')
    return f"{f(e['difference'])} [{f(lo)}, {f(hi)}]"


def mechanism():
    a2 = json.loads((FINAL / 'posthoc-revision-r1.json').read_text())['A2']
    rows = []
    for arm in ARMS:
        g = a2['resolved_given_edit_hit'][arm]; fx = a2['fix_file_given_edited'][arm]
        contrast = '--' if arm == 'NONE' else ci(a2['contrast_edited_any_vs_none'][arm])
        rows.append(f"{arm} & {pct(a2['retry_edited_any'][arm])} & {contrast} & {pct(fx['rate'])} & "
                    f"{pct(g['hit']['rate'])} ({g['hit']['n']}) & {pct(g['miss']['rate'])} ({g['miss']['n']}) \\\\")
    tex = ['\\setlength\\tabcolsep{4pt}\\begin{tabular}{lrcrrr}', '\\toprule',
           'Condition & Edited source & $\\Delta$ vs NONE (pp) & Fix file among & \\multicolumn{2}{c}{Resolved (\\%) if fix file} \\\\',
           ' & (\\%) & 95\\% CI & editing (\\%) & edited ($n$) & not edited ($n$) \\\\', '\\midrule', *rows, '\\bottomrule', '\\end{tabular}']
    (GEN / 'tab-mechanism.tex').write_text('\n'.join(tex) + '\n')


def fullsuite():
    path = FINAL / 'fullsuite-analysis-r1.json'
    if not path.exists():
        return
    a = json.loads(path.read_text())
    rows = json.loads((FINAL / 'fullsuite-r1.json').read_text())['rows']
    out = ['\\setlength\\tabcolsep{4pt}\\begin{tabular}{lrrrcc}', '\\toprule',
           'Condition & Resolved & Broke other & Suite-clean & $\\Delta$ vs NONE (pp) & $\\Delta$ vs NONE (pp) \\\\',
           ' & retries & tests & (\\%) & all tests & behavioural tests$^f$ \\\\', '\\midrule']
    prim = {r['contrast'].split('-')[0]: r for r in a['all_tests']['primary']}
    beh = {r['contrast'].split('-')[0]: r for r in a['behavioural_only']['primary']}
    for arm in ARMS:
        bad = sum(1 for x in rows if x['arm'] == arm and x['suite_clean'] is False)
        c1 = '--' if arm == 'NONE' else ci(prim[arm]); c2 = '--' if arm == 'NONE' else ci(beh[arm])
        out.append(f"{arm} & {a['resolved_attempts'][arm]} & {bad} & {pct(a['all_tests']['rates'][arm])} & {c1} & {c2} \\\\")
    out += ['\\bottomrule', '\\end{tabular}']
    (GEN / 'tab-fullsuite.tex').write_text('\n'.join(out) + '\n')


if __name__ == '__main__':
    mechanism(); fullsuite()
    print((GEN / 'tab-mechanism.tex').read_text())


def m2_table():
    """M1 (gpt-5.4-mini) and M2 (gpt-5.4) side by side; no pooling, no test of difference."""
    path = ROOT / 'planning/m2-gpt54/analysis-m2.json'
    if not path.exists():
        return
    m2 = json.loads(path.read_text()); a = json.loads((FINAL / 'analysis-C.json').read_text())
    p1 = json.loads((FINAL / 'posthoc-revision-r1.json').read_text())['A2']
    c1 = {r['contrast']: r for r in a['primary_itt'] + a['secondary_itt']}
    tri = lambda v: ' / '.join(f'{x:.1f}' for x in v)  # NONE / HISTORY / REFLECT  # noqa: E731
    h1 = {r['contrast'].split()[0]: r for r in m2['H1_edited_any_source']}
    h4 = {r['contrast'].split()[0]: r for r in m2['H4_resolved']}
    rows = [
        ('First attempts resolved', '268 / 449', f"{m2['first_attempts']['outcomes'].get('resolved', 0)} / {m2['first_attempts']['n']}"),
        ('Failures (tasks with failures)', '181 (52)', f"{m2['failures']} ({m2['tasks_with_failures']})"),
        ('Plain retry resolved (\\%)', '29.8', pct(m2['rates']['NONE']['resolved']['difference'])),
        ('Edited a .py file: HISTORY $-$ NONE$^g$', ci(p1['contrast_edited_any_vs_none']['HISTORY']), ci(h1['HISTORY-NONE'])),
        ('Edited a .py file: REFLECT $-$ NONE$^g$', ci(p1['contrast_edited_any_vs_none']['REFLECT']), ci(h1['REFLECT-NONE'])),
        ('Fix file among editing retries (\\%)$^h$', tri([100 * p1['fix_file_given_edited'][k]['rate'] for k in ARMS[:3]]),
         tri([100 * m2['rates'][k]['fix_file_given_edited']['difference'] for k in ARMS[:3]])),
        ('Resolved if fix file edited (\\%)$^h$', tri([100 * p1['resolved_given_edit_hit'][k]['hit']['rate'] for k in ARMS[:3]]),
         tri([100 * m2['rates'][k]['resolved_given_fix_file']['difference'] for k in ARMS[:3]])),
        ('Resolved: HISTORY $-$ NONE', ci(c1['HISTORY-NONE']), ci(h4['HISTORY-NONE'])),
        ('Resolved: REFLECT $-$ NONE', ci(c1['REFLECT-NONE']), ci(h4['REFLECT-NONE'])),
        ('Resolved: REFLECT $-$ HISTORY', ci(c1['REFLECT-HISTORY']), ci(m2['H3_reflect_minus_history'])),
    ]
    out = ['\\setlength\\tabcolsep{4pt}\\begin{tabular}{lcc}', '\\toprule',
           ' & M1: \\texttt{gpt-5.4-mini} & M2: \\texttt{gpt-5.4} \\\\', '\\midrule']
    out += [f'{a} & {b} & {c} \\\\' for a, b, c in rows]
    out += ['\\bottomrule', '\\end{tabular}']
    (GEN / 'tab-m2.tex').write_text('\n'.join(out) + '\n')


if __name__ == '__main__':
    m2_table()


def m3_table():
    """M3 notice-only control, both models; values from the frozen analyses (no pooling)."""
    paths = {'mini': ROOT / 'planning/m3-notice/analysis-mini.json', 'gpt54': ROOT / 'planning/m3-notice/analysis-gpt54.json'}
    if not all(p.exists() for p in paths.values()):
        return
    a = {k: json.loads(p.read_text()) for k, p in paths.items()}
    def cell(d, fam, contrast):
        r = next(x for x in d[fam] if x['contrast'] == contrast)
        dec = d['decisions'][contrast].replace('equivalent within 10 points', 'equivalent').replace('not distinguished', 'n.d.').replace('not supported', 'n.s.')
        return f"{ci(r)} ({dec})"
    rows = []
    for fam, key, label in (('primary_edited', 'edited', 'Modified a .py file'), ('secondary_resolved', 'resolved', 'Resolved')):
        for c, name in (('NOTICE-NONE', 'NOTICE $-$ NONE'), ('HISTORY-NOTICE', 'HISTORY $-$ NOTICE'), ('REFLECT-NOTICE', 'REFLECT $-$ NOTICE')):
            rows.append(f"{label}: {name} & {cell(a['mini'], fam, c + ' ' + key)} & {cell(a['gpt54'], fam, c + ' ' + key)} \\\\")
    rates = ' & '.join(' / '.join(f"{100 * a[m]['rates'][arm]['resolved']:.0f}" for arm in ('NONE', 'NOTICE', 'HISTORY', 'REFLECT')) for m in ('mini', 'gpt54'))
    out = ['\\setlength\\tabcolsep{3pt}\\begin{tabular}{lcc}', '\\toprule',
           ' & \\texttt{gpt-5.4-mini} (156 failures)$^i$ & \\texttt{gpt-5.4} (85 failures) \\\\', '\\midrule', *rows,
           '\\midrule', f'Resolved (\\%): NONE / NOTICE / HISTORY / REFLECT & {rates} \\\\', '\\bottomrule', '\\end{tabular}']
    (GEN / 'tab-m3.tex').write_text('\n'.join(out) + '\n')


if __name__ == '__main__':
    m3_table()
