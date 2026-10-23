"""Lesson accuracy against the reference fix. Evaluation-only; never shown to the model.

Reference locations: changed non-protected .py files, and the functions/methods
(fixed and buggy versions) whose line spans contain changed lines.
"""
import ast, difflib
from pathlib import Path


def _spans(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    spans = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                end = getattr(child, 'end_lineno', None) or max(getattr(n, 'lineno', child.lineno) for n in ast.walk(child))
                if not isinstance(child, ast.ClassDef):
                    spans.append((name, child.lineno, end))
                visit(child, name + '.')
    visit(tree, '')
    return spans


def reference_locations(buggy_root, fixed_root, changed):
    files, functions = [], set()
    for rel in changed:
        if not rel.endswith('.py'):
            continue
        files.append(rel)
        old_path, new_path = Path(buggy_root) / rel, Path(fixed_root) / rel
        old = old_path.read_text(errors='replace') if old_path.exists() else ''
        new = new_path.read_text(errors='replace') if new_path.exists() else ''
        old_lines, new_lines = set(), set()
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old.splitlines(), new.splitlines()).get_opcodes():
            if tag != 'equal':
                old_lines.update(range(i1 + 1, max(i2, i1 + 1) + 1)); new_lines.update(range(j1 + 1, max(j2, j1 + 1) + 1))
        for source, lines in ((old, old_lines), (new, new_lines)):
            for name, start, end in _spans(source):
                if any(start <= l <= end for l in lines):
                    functions.add(name)
    # Keep the innermost names: drop an enclosing function when a nested one matched.
    inner = {f for f in functions if not any(g != f and g.startswith(f + '.') for g in functions)}
    return {'files': sorted(files), 'functions': sorted(inner)}


def _norm_file(path):
    path = path.strip().strip('`').replace('\\', '/')
    for prefix in ('/work/', './', 'work/'):
        if path.startswith(prefix):
            path = path[len(prefix):]
    return path


def _short(name):
    name = name.strip().strip('`').split('(')[0]
    return name.replace('::', '.').split('.')[-1]


def _qualified(name):
    return name.strip().strip('`').split('(')[0].replace('::', '.').strip('.')


def score_lesson(lesson, reference):
    """Lenient (suffix path / last name component) and strict (exact path / qualified name) hits."""
    files = {_norm_file(f) for f in lesson['suspected_files']}
    ref_files = set(reference['files'])
    lenient_files = {f for f in files if f in ref_files or any(r.endswith('/' + f) for r in ref_files)}
    strict_files = files & ref_files
    names = {_short(f) for f in lesson['suspected_functions']}
    ref_names = {_short(f) for f in reference['functions']}
    qualified = {_qualified(f) for f in lesson['suspected_functions']}
    ref_qualified = set(reference['functions'])
    # Strict: exact qualified name, or a dotted (Class.method) name equal to a reference suffix.
    strict_functions = {q for q in qualified if q in ref_qualified or ('.' in q and any(r.endswith('.' + q) for r in ref_qualified))}
    first_file = _norm_file(lesson['suspected_files'][0]) if lesson['suspected_files'] else None
    first_fn = _short(lesson['suspected_functions'][0]) if lesson['suspected_functions'] else None
    return {'top1_file_hit': bool(first_file) and (first_file in ref_files or any(r.endswith('/' + first_file) for r in ref_files)),
            'top1_function_hit': bool(first_fn) and first_fn in ref_names,
            'file_hit': bool(lenient_files), 'file_hit_strict': bool(strict_files),
            'file_precision': len(lenient_files) / len(files) if files else None,
            'function_hit': bool(names & ref_names), 'function_hit_strict': bool(strict_functions),
            'function_precision': len(names & ref_names) / len(names) if names else None,
            'n_suspected_files': len(files), 'n_suspected_functions': len(names),
            'reference_files': sorted(ref_files), 'reference_functions': reference['functions']}


# --- Main-design additions: deterministic baselines (validity V3) --------------------------



def traceback_locations(final_raw, protected, work_root=None):
    """Source files/functions from the final test output: pytest 'path:line' frame lines (long and
    short formats). Functions come from 'in func' when present, else from the enclosing def at that
    line in the attempt's own final workspace (work_root). Test files (protected paths) excluded."""
    import re as _re
    files, functions = set(), set()
    for m in _re.finditer(r'^([\w./-]+\.py):(\d+)(?::| in )(?:\s*in (\w+))?', final_raw, _re.M):
        path = m.group(1).lstrip('./'); line = int(m.group(2))
        if any(path == p or path.startswith(p.rstrip('/') + '/') for p in protected):
            continue
        files.add(path)
        if m.group(3) and m.group(3) != '<module>':
            functions.add(m.group(3))
        elif work_root is not None and (Path(work_root) / path).is_file():
            spans = _spans((Path(work_root) / path).read_text(errors='replace'))
            inner = [s for s in spans if s[1] <= line <= s[2]]
            if inner:
                functions.add(max(inner, key=lambda s: s[1])[0])
    return {'files': sorted(files), 'functions': sorted(functions)}


def attempt_locations(buggy_root, attempt_root, modified, protected):
    """Files/functions the failed attempt itself changed (non-test .py sources)."""
    changed = [f for f in modified if f.endswith('.py') and
               not any(f == p or f.startswith(p.rstrip('/') + '/') for p in protected)]
    return reference_locations(buggy_root, attempt_root, changed)


def score_locations(files, functions, reference):
    """Same hit rules as score_lesson for a deterministic location guess."""
    pseudo = {'suspected_files': list(files), 'suspected_functions': list(functions),
              'root_cause': '-', 'what_failed': '-', 'next_steps': []}
    return score_lesson(pseudo, reference)
