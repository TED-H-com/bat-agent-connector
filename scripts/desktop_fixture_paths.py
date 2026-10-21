"""Reject unresolved repository fixture paths, including reads inside test bodies.

These are syntax checks, not execution/branch coverage. Literal joins and unique
constant aliases are resolved; a repository path with a computed tail is rejected
even if today's test inputs happen not to execute it. Unknown leading directories
may over-include a literal tests/scripts suffix, never hide that suffix.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

from tree_sitter import Language, Parser
from tree_sitter_typescript import language_typescript


def javascript_expressions(file, source):
    tree = Parser(Language(language_typescript())).parse(source.encode())
    nodes, pending = [], [tree.root_node]
    while pending:
        node = pending.pop()
        nodes.append(node)
        pending.extend(reversed(node.named_children))
    error = next((node for node in nodes if node.is_error or node.is_missing), None)
    assert not tree.root_node.has_error, f'{file}:{error.start_point.row + 1 if error else 1}: cannot parse fixture paths'
    bindings, initializers = {}, []
    for node in nodes:
        if node.type == 'variable_declarator':
            name, value = node.child_by_field_name('name'), node.child_by_field_name('value')
            if name.type == 'identifier':
                key = name.text.decode()
                initializers.append((key, node, value))
                bindings[key] = None if key in bindings else value
    # A reassigned or shadowed name is not a constant alias.
    for node in nodes:
        if node.type in {'assignment_expression', 'augmented_assignment_expression', 'update_expression'}:
            name = node.child_by_field_name('left') or node.child_by_field_name('argument')
            if name and name.type == 'identifier':
                bindings[name.text.decode()] = None
        if node.type in {'arrow_function', 'function_expression', 'function_declaration'}:
            params = node.child_by_field_name('parameter') or node.child_by_field_name('parameters')
            pending = [params] if params else []
            while pending:
                param = pending.pop()
                if param.type == 'identifier':
                    bindings[param.text.decode()] = None
                pending.extend(param.named_children)

    def called_name(node):
        if node.type == 'member_expression':
            node = node.child_by_field_name('property')
        return node.text.decode() if node else ''

    def parts(node, seen=frozenset()):
        if node is None:
            return [None]
        if node.type == 'string':
            try:
                return [ast.literal_eval(node.text.decode())]
            except (SyntaxError, ValueError):
                # Preserve the root prefix when an unsupported JS escape stops
                # us decoding a filename, so it cannot silently disappear.
                return [node.text.decode()[1:-1], None]
        if node.type == 'identifier':
            key = node.text.decode()
            if bindings.get(key) is not None and key not in seen:
                return parts(bindings[key], seen | {key})
        if node.type in {'parenthesized_expression', 'as_expression', 'non_null_expression', 'template_substitution'}:
            return parts(node.named_children[0], seen)
        if node.type == 'template_string':
            return [part for child in node.named_children for part in (
                [child.text.decode()] if child.type == 'string_fragment' else parts(child, seen))]
        if node.type == 'binary_expression' and node.child_by_field_name('operator').text == b'+':
            return parts(node.child_by_field_name('left'), seen) + parts(node.child_by_field_name('right'), seen)
        if node.type in {'call_expression', 'new_expression'}:
            called = node.child_by_field_name('function') or node.child_by_field_name('constructor')
            name = called_name(called)
            args = node.child_by_field_name('arguments').named_children
            if name == 'URL':
                return parts(args[0], seen) if args else [None]
            if name in {'join', 'resolve'}:
                return [part for index, arg in enumerate(args) for part in (['/'] if index else []) + parts(arg, seen)]
            if called.type == 'member_expression' and name in {'concat', 'replace', 'replaceAll'}:
                base = parts(called.child_by_field_name('object'), seen)
                return base + ([part for arg in args for part in parts(arg, seen)] if name == 'concat' else [None])
        return [None]

    result = []
    for node in nodes:
        computed = node.type in {'string', 'template_string', 'binary_expression'}
        called = node.child_by_field_name('function') or node.child_by_field_name('constructor')
        constructor = node.type in {'call_expression', 'new_expression'} and called_name(called) in {
            'join', 'resolve', 'URL', 'concat', 'replace', 'replaceAll'}
        if computed or constructor:
            result.append({'line': node.start_point.row + 1, 'expression': node.text.decode(), 'parts': parts(node)})
    for key, node, value in initializers:
        if value is not None and bindings[key] is None:
            result.append({'line': node.start_point.row + 1,
                           'expression': f'mutable or shadowed path alias {key}: {value.text.decode()}',
                           'parts': parts(value) + [None]})
    return result


def python_expressions(file, source):
    tree = ast.parse(source)
    bindings, initializers = {}, []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            initializers.append((name, node, node.value))
            bindings[name] = None if name in bindings else node.value

    for node in ast.walk(tree):
        if isinstance(node, (ast.AugAssign, ast.AnnAssign)) and isinstance(node.target, ast.Name):
            bindings[node.target.id] = None
        if isinstance(node, ast.arg):
            bindings[node.arg] = None

    def joined(values):
        return [part for index, value in enumerate(values) for part in (['/'] if index else []) + value]

    def parts(node, seen=frozenset()):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Name):
            if node.id == '__file__':
                return [str(file)]
            if bindings.get(node.id) is not None and node.id not in seen:
                return parts(bindings[node.id], seen | {node.id})
        if isinstance(node, ast.JoinedStr):
            return [part for value in node.values for part in parts(
                value.value if isinstance(value, ast.FormattedValue) else value, seen)]
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
            left, right = parts(node.left, seen), parts(node.right, seen)
            return joined([left, right]) if isinstance(node.op, ast.Div) else left + right
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            return parts(node.left, seen) + [None]
        if isinstance(node, ast.Attribute) and node.attr == 'parent':
            value = parts(node.value, seen)
            if None not in value:
                return [str(Path(''.join(value)).parent)]
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == 'parents':
            value = parts(node.value.value, seen)
            if None not in value and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
                return [str(Path(''.join(value)).parents[node.slice.value])]
        if isinstance(node, ast.Call):
            called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
            if called in {'Path', 'join', 'joinpath'}:
                values = [parts(arg, seen) for arg in node.args]
                if called == 'joinpath':
                    values.insert(0, parts(node.func.value, seen))
                return joined(values)
            if called == 'resolve' and isinstance(node.func, ast.Attribute):
                return parts(node.func.value, seen)
            if called == 'with_name' and isinstance(node.func, ast.Attribute):
                base = parts(ast.Attribute(value=node.func.value, attr='parent'), seen)
                return joined([base, parts(node.args[0], seen)])
            if called in {'format', 'format_map', 'replace', 'with_suffix', 'with_stem'} and isinstance(node.func, ast.Attribute):
                return parts(node.func.value, seen) + [None]
        return [None]

    expressions = []
    for node in ast.walk(tree):
        computed = isinstance(node, (ast.JoinedStr, ast.BinOp, ast.Constant))
        constructor = isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name) and node.func.id in {'Path', 'join'} or
            isinstance(node.func, ast.Attribute) and node.func.attr in {
                'join', 'joinpath', 'with_name', 'format', 'format_map', 'replace', 'with_suffix', 'with_stem'})
        if computed or constructor:
            expressions.append({'line': node.lineno, 'expression': ast.unparse(node), 'parts': parts(node)})
    for name, node, value in initializers:
        if bindings[name] is None:
            expressions.append({'line': node.lineno,
                                'expression': f'mutable or shadowed path alias {name}: {ast.unparse(value)}',
                                'parts': parts(value) + [None]})
    return expressions


def fixture_paths(root, sources, tracked):
    """Return resolved paths and audit facts; reject each unbounded fixture tail.

    sources is the *whole* current closure, including Python files imported only
    at runtime. The TypeScript grammar parses JS/TS/MJS without running them.
    """
    expressions = {name: javascript_expressions(name, source) for name, source in sources.items()
                   if Path(name).suffix in {'.js', '.ts', '.mjs', '.cjs'}}
    expressions.update({name: python_expressions(root / name, source)
                        for name, source in sources.items() if name.endswith('.py')})
    resolved, audit, errors = set(), [], set()
    for name, values in expressions.items():
        for value in values:
            pieces = value['parts']
            # Retain unknowns as separators: joining around them would falsely
            # turn "tests/" + name + ".json" into a known file.
            pattern = ''.join('\0' if part is None else part for part in pieces)
            known = ''.join(part for part in pieces if part is not None)
            if '/' not in known:
                continue
            suffix = pattern.lstrip('\0').lstrip('/') if pattern.startswith('\0') else pattern
            # A temporary/worktree path or an HTTP URL is not relative to the
            # source module. Only an explicit repository prefix (or __file__,
            # resolved above) establishes a root for a computed expression.
            explicit = re.match(r'^(?:\.\.?/)*(tests|scripts)/', suffix)
            local_fixtures = name.startswith(('tests/', 'scripts/')) and suffix.startswith('fixtures/')
            if '\0' in pattern and not (suffix.startswith(str(root) + '/') or explicit or local_fixtures
                                        or suffix.startswith(('../', './'))):
                continue
            candidates = []
            # Reject before normpath: an unknown component must not disappear
            # through "name/.." normalization.
            if '\0' in suffix and (explicit or local_fixtures or any(
                    suffix.startswith(str(root / area) + '/') for area in ('tests', 'scripts'))):
                candidates.append(suffix)
            for base in ((root / name).parent, root, root / 'desktop'):
                path = os.path.normpath(str(base / suffix))
                if path.startswith(str(root) + '/'):
                    tail = path[len(str(root)) + 1:]
                    if '\0' in tail.split('/')[0]:
                        candidates.append(tail)  # The unknown directory may be tests or scripts.
                for area in ('tests', 'scripts'):
                    prefix = str(root / area) + '/'
                    if path.startswith(prefix):
                        candidates.append(path[len(str(root)) + 1:])
            if not candidates:
                # An unknown base before a literal root path is safe to
                # over-include (join(base, "tests", "fixture.json")).
                match = explicit
                if match:
                    candidates.append(suffix[match.start(1):])
            for candidate in set(candidates):
                if '\0' in candidate:
                    errors.add(f'{name}:{value["line"]}: unresolved computed path into tests/scripts: '
                               f'{value["expression"]}; use literal file names or a resolvable literal join')
                else:
                    if candidate in tracked and candidate != name:
                        resolved.add(candidate)
                        audit.append({'file': name, 'line': value['line'], 'expression': value['expression'],
                                      'resolved': candidate})
    assert not errors, '\n'.join(sorted(errors))
    return resolved, sorted(audit, key=lambda value: (value['file'], value['line'], value['expression']))
