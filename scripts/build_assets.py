"""Build paper figures and LaTeX tables directly from the frozen analysis outputs (no retyped numbers).
Usage: python3 paper/build_assets.py <analysis-final dir> <funnel.json> <out dir>
"""
import json, sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

CINST, FIRST = [], []
BLUE, ORANGE, INK, MUTED, GRID = '#2a78d6', '#eb6834', '#1f1f1e', '#6b6a64', '#d9d8d2'
ARMS = ['NONE', 'HISTORY', 'REFLECT', 'SIGNAL', 'BOTH']


def pct(x, d=1):
    return f'{100 * x:.{d}f}'


def pp(x, d=1):
    return f'{100 * x:+.{d}f}'


def tpp(x, d=1):
    """LaTeX percentage points with a true minus sign and an unsigned zero."""
    v = round(100 * x, d)
    if v == 0:
        return f'{0:.{d}f}'
    return (f'+{v:.{d}f}' if v > 0 else f'$-${-v:.{d}f}')


def style(ax):
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    for s in ('left', 'bottom'):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=INK, labelsize=8)
    ax.grid(axis='x', color=GRID, linewidth=0.6); ax.set_axisbelow(True)


def forest(a, out):
    rows = [(c['contrast'].replace('-', ' − '), c['difference'], c['ci95']) for c in a['primary_itt']]
    sec = next(c for c in a['secondary_itt'] if c['contrast'] == 'REFLECT-HISTORY')
    rows.append(('REFLECT − HISTORY', sec['difference'], sec['ci95']))
    fig, ax = plt.subplots(figsize=(4.6, 2.1))
    for y, (label, d, ci) in enumerate(reversed(rows)):
        ax.plot([100 * ci[0], 100 * ci[1]], [y, y], color=BLUE, linewidth=2, solid_capstyle='round')
        ax.plot(100 * d, y, 'o', color=BLUE, markersize=6, markeredgecolor='white', markeredgewidth=1.5)
        ax.text(100 * ci[1] + 0.8, y, f'{pp(d)} [{pp(ci[0])}, {pp(ci[1])}]', va='center', fontsize=7, color=INK)
    ax.axvline(0, color=MUTED, linewidth=1)
    ax.set_yticks(range(len(rows))); ax.set_yticklabels([r[0] for r in reversed(rows)], fontsize=8)
    ax.set_xlabel('Difference in retry resolution (percentage points)', fontsize=8, color=INK)
    ax.set_xlim(-12, 34); style(ax); ax.grid(axis='y', visible=False)
    fig.tight_layout(); fig.savefig(out / 'fig-contrasts.pdf'); plt.close(fig)


def lesson_bars(p, out):
    b = p['B_by_attempt_edits']
    groups = [('Attempt edited files\n(n=%d)' % b['attempt_edited_source']['n'], b['attempt_edited_source']),
              ('Attempt made no edit\n(n=%d)' % b['attempt_no_source_edits']['n'], b['attempt_no_source_edits'])]
    fig, ax = plt.subplots(figsize=(4.6, 2.3))
    w = 0.34
    for i, (label, g) in enumerate(groups):
        lv, av = g['lesson_file_hit']['rate'], g['attempt_file_hit']['rate']
        ax.barh(i + w / 2, 100 * lv, height=w - 0.04, color=BLUE, label='Lesson names it' if i == 0 else None)
        ax.barh(i - w / 2, 100 * av, height=w - 0.04, color=ORANGE, hatch='////', edgecolor='white', linewidth=0,
                label='Attempt edited it' if i == 0 else None)
        ax.text(100 * lv + 1, i + w / 2, pct(lv, 0) + '%', va='center', fontsize=7, color=INK)
        ax.text(100 * av + 1, i - w / 2, pct(av, 0) + '%' + (' (no edits by definition)' if av == 0 else ''), va='center', fontsize=7, color=INK)
    ax.set_yticks([0, 1]); ax.set_yticklabels([g[0] for g in groups], fontsize=8)
    ax.set_xlim(0, 112); ax.set_xlabel('Instances where the reference-fix file is ... (%)', fontsize=8, color=INK)
    ax.legend(fontsize=7, frameon=False, loc='lower center', bbox_to_anchor=(0.45, 1.0), ncol=2); style(ax); ax.grid(axis='y', visible=False)
    fig.tight_layout(); fig.savefig(out / 'fig-lessons.pdf'); plt.close(fig)


def tables(a, a46, p, funnel, out, routes):
    rates = a['resolution_rates']
    con = {c['contrast'].split('-')[0]: c for c in a['primary_itt']}
    cc = {c['contrast'].split('-')[0]: c for c in a['sensitivity_complete_case']}
    lines = [r'\setlength\tabcolsep{5pt}\begin{tabular}{lrrrcrr}', r'\toprule',
             r'Condition & Resolved & $n$ & $\Delta$ (pp) & 95\% CI & $p_{\mathrm{Holm}}$ & Task-weighted $\Delta$ \\', r'\midrule',
             rf"NONE & {pct(rates['NONE']['difference'])}\% & {rates['NONE']['n_instances']} & -- & -- & -- & -- \\"]
    for arm in ARMS[1:]:
        c = con[arm]
        lines.append(rf"{arm} & {pct(rates[arm]['difference'])}\% & {rates[arm]['n_instances']} & {tpp(c['difference'])} & "
                     rf"[{tpp(c['ci95'][0])}, {tpp(c['ci95'][1])}] & {c['p_holm']:.2f} & {tpp(c['task_weighted'])} \\")
    lines += [r'\bottomrule', r'\end{tabular}']
    (out / 'tab-primary.tex').write_text('\n'.join(lines) + '\n')
    sens = [r'\begin{tabular}{lrrrr}', r'\toprule', r'Analysis & HISTORY & REFLECT & SIGNAL & BOTH \\', r'\midrule']
    def row(name, vals):
        sens.append(name + ' & ' + ' & '.join(vals) + r' \\')
    row('Primary (75 tasks)', [tpp(con[x]['difference']) for x in ARMS[1:]])
    row('First look (46 tasks)$^{a}$', [tpp(c['difference']) for c in a46['primary_itt']])
    row('Complete case$^{b}$', [tpp(cc[x]['difference']) for x in ARMS[1:]])
    row('Excluding black$^{c}$', [tpp(p['excluding_black'][f'{x}-NONE']['difference']) for x in ARMS[1:]])
    lopo = p['leave_one_project_out']
    row('Leave one project out$^{c}$', [f"{tpp(min(v[f'{x}-NONE'] for v in lopo.values()))}--{tpp(max(v[f'{x}-NONE'] for v in lopo.values()))[1:]}" for x in ARMS[1:]])
    for label, key in (('Batch 1 (24 tasks)', 'T2'), ('Batch 2 (22 tasks)', 'T3'), ('Batch 3 (29 tasks)', 'T4')):
        r = routes[key]; row(label, [tpp(c['difference']) for c in r['primary_itt']])
    sens += [r'\bottomrule', r'\end{tabular}']
    (out / 'tab-sensitivity.tex').write_text('\n'.join(sens) + '\n')
    d = a['descriptive_lessons']; fa = p['B_functions_applicable']; be = p['B_by_attempt_edits']
    fn = [i for i in CINST if i['lesson']['reference_functions']]
    fr = lambda key, sub: sum(float(i[sub][key]) for i in fn) / len(fn)  # noqa: E731
    les = [r'\begin{tabular}{lrr}', r'\toprule', r'Location source & File hit & Function hit$^{d}$ \\', r'\midrule',
           rf"Lesson (any listed) & {pct(d['lesson_file_hit']['difference'])}\% & {pct(fa['lesson_function_hit']['rate'])}\% \\",
           rf"Lesson (first listed) & {pct(d['lesson_top1_file_hit']['difference'])}\% & {pct(fr('top1_function_hit', 'lesson'))}\% \\",
           rf"Attempt's own edits & {pct(d['baseline_attempt_file_hit']['difference'])}\% & {pct(fa['attempt_function_hit']['rate'])}\% \\",
           rf"Traceback frames & {pct(d['baseline_traceback_file_hit']['difference'])}\% & {pct(fr('function_hit', 'baseline_traceback'))}\% \\",
           r'\midrule',
           rf"\multicolumn{{3}}{{l}}{{Post hoc, attempt edited files (n={be['attempt_edited_source']['n']}): lesson {pct(be['attempt_edited_source']['lesson_file_hit']['rate'],0)}\% vs attempt {pct(be['attempt_edited_source']['attempt_file_hit']['rate'],0)}\% (file)}} \\",
           rf"\multicolumn{{3}}{{l}}{{Same subset: listed files {be['attempt_edited_source']['lesson_mean_files']:.1f} vs {be['attempt_edited_source']['attempt_mean_files']:.1f}; file precision {be['attempt_edited_source']['lesson_file_precision']['rate']:.2f} vs {be['attempt_edited_source']['attempt_file_precision']['rate']:.2f}}} \\",
           r'\bottomrule', r'\end{tabular}']
    (out / 'tab-lessons.tex').write_text('\n'.join(les) + '\n')
    sub = [r'\begin{tabular}{lrrrrrr}', r'\toprule', r'Terminal status (all attempts) & First & NONE & HISTORY & REFLECT & SIGNAL & BOTH \\', r'\midrule']
    a1 = {}
    for x in FIRST:  # all first attempts by terminal status (resolved and unresolved)
        k = 'provider_or_budget_stop' if x.get('outcome') == 'harness' else x['status']; a1[k] = a1.get(k, 0) + 1
    rs = a['retry_subtypes_by_arm']
    def cnt(arm, k):
        return sum(v for kk, v in rs[arm].items() if kk.split('/')[0] == k)
    labels = [('declared_done', 'declared done'), ('response_limit', 'eight responses used'), ('output_limit', 'output budget exhausted'),
              ('context_limit', 'input bound reached'), ('invalid_test_execution', 'invalid test run'), ('provider_or_budget_stop', 'provider error (missing)')]
    for k, lab in labels:
        sub.append(rf"{lab} & {a1.get(k, 0)} & " + ' & '.join(str(cnt(arm, k)) for arm in ARMS) + r' \\')
    sub += [r'\midrule', rf"of which resolved & {sum(1 for x in FIRST if x.get('outcome') == 'resolved')} & " + ' & '.join(str(sum(1 for i in CINST if i['arms'].get(arm, {}).get('outcome') == 'resolved')) for arm in ARMS) + r' \\', r'\bottomrule', r'\end{tabular}']
    (out / 'tab-subtypes.tex').write_text('\n'.join(sub) + '\n')
    rc = funnel['reason_counts']
    (out / 'funnel.tex').write_text(
        rf"From {funnel['totals']['bugsinpy']} BugsInPy bugs: native-extension projects {rc['native_extension_project']}, environment failures "
        rf"{rc['environment_controls_unexpected'] + rc['environment_error']}, sandbox control failures {rc['sandbox_controls_failed']}, "
        rf"no linked issue {rc['statement_no_linked_issue']}, solution leakage {rc['statement_solution_leakage']}, statement too long {rc['statement_too_long']}, "
        rf"development or previously exposed {rc['excluded_dev_or_exposed'] + rc['old_project_exposed_not_built']}, initial prompt above 32{{,}}000 bytes {rc['initial_prompt_over_32000']}; "
        rf"{funnel['totals']['final']} tasks remain." + '\n')
    proj = [r'\begin{tabular}{lrrrrrrr}', r'\toprule', r'Project & Tasks & Failures & NONE & HISTORY & REFLECT & SIGNAL & BOTH \\', r'\midrule']
    for name, v in p['per_project'].items():
        proj.append(rf"{name} & {v['tasks']} & {v['instances']} & " + ' & '.join(pct(v[x], 0) for x in ARMS) + r' \\')
    proj += [r'\bottomrule', r'\end{tabular}']
    (out / 'tab-projects.tex').write_text('\n'.join(proj) + '\n')


def main(adir, funnel_path, out):
    adir, out = Path(adir), Path(out); out.mkdir(parents=True, exist_ok=True)
    a = json.loads((adir / 'analysis-C.json').read_text()); p = json.loads((adir / 'posthoc-r1.json').read_text())
    global CINST, FIRST
    cdata = json.loads((adir / 'C.json').read_text()); CINST, FIRST = cdata['instances'], cdata['first_attempts']
    a46 = json.loads((adir.parent / 'analysis-46' / 'analysis-C.json').read_text())
    forest(a, out); lesson_bars(p, out); routes = {k: json.loads((adir / f'analysis-{k}.json').read_text()) for k in ('T2', 'T3', 'T4')}
    tables(a, a46, p, json.loads(Path(funnel_path).read_text()), out, routes)
    print('assets written to', out)


if __name__ == '__main__':
    main(*sys.argv[1:4])
