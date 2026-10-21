"""Derive desktop workflow inputs from its commands and their local dependencies.

Run with the dev environment. --write updates both generated path-filter blocks;
ordinary pytest checks their coverage. Imports run in a fresh process, never the
baseline producer or a fixture body, and do not start services/builds.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import json
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = '.github/workflows/desktop.yml'
CODE = {'.py', '.mjs', '.cjs', '.js', '.ts', '.sh', '.rs', '.toml', '.ps1', '.swift', '.cs'}
FILE = re.compile(r'''[\w.*?/-]+\.(?:py|mjs|cjs|js|ts|sh|rs|json|tsv|swift|ps1|cs)\b''')
NPM = re.compile(r'\bnpm\s+run\s+([\w:-]+)|\bnpm\s+(test)\b')
BEGIN = '      # BEGIN generated fixture inputs'
END = '      # END generated fixture inputs'


def workflow(root):
    return yaml.safe_load((root / WORKFLOW).read_text(encoding='utf-8'))


def path_matches(path, pattern):
    """GitHub's root-anchored *, ** subset; reject unsupported syntax.

    https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#filter-pattern-cheat-sheet
    Unlike fnmatch, * cannot cross /. **/ also matches zero directories.
    """
    if any(character in pattern for character in '?+[]!\\{}'):
        raise ValueError(f'Unsupported workflow path pattern: {pattern}')
    parts = []
    index = 0
    while index < len(pattern):
        if pattern[index:index + 3] == '**/':
            parts.append('(?:.*/)?')
            index += 3
        elif pattern[index:index + 2] == '**':
            parts.append('.*')
            index += 2
        elif pattern[index] == '*':
            parts.append('[^/]*')
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.fullmatch(''.join(parts), path, flags=re.DOTALL) is not None


def triggered(paths, patterns):
    # Validate every pattern even if an earlier one matches.
    matches = [path_matches(path, pattern) for path in paths for pattern in patterns]
    return any(matches)


def filters(root):
    document = workflow(root)
    events = document.get('on', document.get(True))  # PyYAML's YAML 1.1 boolean spelling.
    return {event: events[event]['paths'] for event in ('pull_request', 'push')}


def tracked_files(root):
    return set(subprocess.check_output(  # noqa: S607 - fixed read-only Git command
        ['git', 'ls-files'], cwd=root, text=True).splitlines())  # noqa: S607


def referenced_files(root, source, directory, tracked, *, command=False):
    """Resolve executable/path literals, including subprocess and runpy inputs.

    Bare names cover Path(...)/'part'/'helper.py' expressions. Only actual tracked
    files can enter the closure; generated output and mock paths cannot.
    """
    result = set()
    by_name = {}
    for name in tracked:
        by_name.setdefault(Path(name).name, set()).add(name)
    for token in FILE.findall(source):
        if '*' in token and not command:
            # A fixture's src.glob("*.py") is not a glob of the tests directory.
            # Only shell/npm commands expand globs relative to their known cwd.
            continue
        for base in (directory, root, root / 'desktop'):
            path = Path(os.path.normpath(str(base / token)))
            if not path.is_relative_to(root):
                continue
            relative = path.relative_to(root).as_posix()
            if '*' in relative:
                result.update(name for name in tracked if path_matches(name, relative))
            elif relative in tracked:
                result.add(relative)
        if '/' not in token and '*' not in token and '?' not in token and Path(token).suffix in CODE - {'.js', '.ts'}:
            result.update(by_name.get(token, ()))
    return result


def command_inputs(root, tracked):
    jobs = workflow(root)['jobs']
    result = {}
    package = json.loads((root / 'desktop/package.json').read_text())['scripts']
    for name, job in jobs.items():
        found = set()
        seen = set()

        def collect(command, directory, found=found, seen=seen):
            key = (command, directory)
            if key in seen:
                return
            seen.add(key)
            found.update(referenced_files(root, command, directory, tracked, command=True))
            scripts = {next(value for value in match if value) for match in NPM.findall(command)}
            # ci-central-ui.sh forwards the named test:* argument to npm run.
            if 'ci-central-ui.sh' in command:
                scripts.update(re.findall(r'\btest:[\w:-]+', command))
            for script in scripts:
                assert script in package, f'Unknown desktop npm script: {script}'
                collect(package[script], root / 'desktop')
            if 'playwright test' in command:
                found.update(path for path in tracked if path.startswith('desktop/tests/') and path.endswith('.spec.ts'))

        default = root / job.get('defaults', {}).get('run', {}).get('working-directory', '.')
        for step in job['steps']:
            if 'run' in step:
                collect(step['run'], root / step['working-directory'] if 'working-directory' in step else default)
        # Cargo discovers in-crate tests/helpers without listing each in shell.
        if any('cargo ' in step.get('run', '') for step in job['steps']):
            found.update(path for path in tracked if path.startswith('desktop/') and path.endswith('.rs'))
        result[name] = found
    return result


def local_imports(root, path):
    result = set()
    for node in ast.walk(ast.parse((root / path).read_text(encoding='utf-8'))):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module, *(node.module + '.' + alias.name for alias in node.names)]
        for name in names:
            if name.startswith(('tests.', 'scripts.')):
                candidate = name.replace('.', '/') + '.py'
                if (root / candidate).is_file():
                    result.add(candidate)
    return result


def probe(root, paths):
    """Import real test modules, including seed-mode imports, without running tests."""
    sys.path[:0] = [str(root / 'src'), str(root), str(root / 'desktop/tests')]
    read = set()
    tracked = tracked_files(root)

    def audit(event, args):
        if event == 'open' and isinstance(args[0], str):
            path = Path(args[0]).resolve()
            if path.is_relative_to(root):
                relative = path.relative_to(root).as_posix()
                if relative in tracked:
                    read.add(relative)

    sys.addaudithook(audit)
    for path in sorted(paths):
        if path.endswith('.py') and path.startswith(('tests/', 'scripts/')) and path != 'scripts/desktop_fixture_inputs.py':
            importlib.import_module(path[:-3].replace('/', '.'))
        elif path.endswith('.py') and 'BATC_UPGRADE_SEED_MODE' in (root / path).read_text():
            os.environ['BATC_UPGRADE_SEED_MODE'] = '1'
            runpy.run_path(str(root / path), run_name='fixture_input_probe')
    # uv sync/build executes the metadata-declared hook too. Reuse the existing
    # build-input authority rather than introducing another package file list.
    from scripts.validate_connector_update import fixture_build_inputs
    for name in fixture_build_inputs(root):
        path = root / name
        if path.is_file():
            read.add(name)
    loaded = set()
    for module in tuple(sys.modules.values()):
        file = getattr(module, '__file__', None)
        if file:
            path = Path(file).resolve()
            if path.is_relative_to(root):
                relative = path.relative_to(root).as_posix()
                if relative in tracked:
                    loaded.add(relative)
    return {'loaded': sorted(loaded), 'read': sorted(read)}


def dependency_closure(root):
    root = root.resolve()
    tracked = tracked_files(root)
    entries = command_inputs(root, tracked)
    pending = set().union(*entries.values())
    visited = set()
    runtime = set()
    reads = set()
    while True:
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            if Path(path).suffix in CODE and not path.startswith('src/'):
                source = (root / path).read_text(encoding='utf-8')
                if path.endswith('.py'):
                    # File names in comments are documentation, not dependencies.
                    source = '\n'.join(node.value for node in ast.walk(ast.parse(source))
                                       if isinstance(node, ast.Constant) and isinstance(node.value, str))
                pending.update(referenced_files(root, source, (root / path).parent, tracked) - visited)
                if path.endswith('.py'):
                    pending.update(local_imports(root, path) - visited)
        child = subprocess.run(  # noqa: S603 - fresh, repository-owned import probe
            [sys.executable, str(root / 'scripts/desktop_fixture_inputs.py'), '--probe'],
                               input=json.dumps(sorted(visited)), cwd=root, text=True, capture_output=True)
        assert child.returncode == 0, child.stdout + child.stderr
        facts = json.loads(child.stdout.split('DESKTOP_INPUTS=')[-1])
        runtime.update(facts['loaded'])
        reads.update(facts['read'])
        pending = (runtime | reads) - visited
        if not pending:
            break
    return {'entries': {job: sorted(paths) for job, paths in entries.items()},
            'files': sorted(visited), 'loaded': sorted(runtime), 'read': sorted(reads)}


def missing_inputs(root, closure):
    return {event: [path for path in closure['files'] if not triggered([path], patterns)]
            for event, patterns in filters(root).items()}


def render_filters(root, closure):
    source = (root / WORKFLOW).read_text(encoding='utf-8')
    current = filters(root)
    assert current['pull_request'] == current['push'], 'PR/push filters differ'
    # Existing filters stay outside the generated block. This preserves their
    # coverage while making new fixture inputs reproducible, not a second list.
    plain = re.sub('^' + re.escape(BEGIN) + r'\n.*?^' + re.escape(END) + r'\n?',
                   '', source, flags=re.MULTILINE | re.DOTALL)
    document = yaml.safe_load(plain)
    events = document.get('on', document.get(True))
    base = events['push']['paths']
    generated = sorted(path for path in closure['files'] if not triggered([path], base))
    value = '\n'.join('      - ' + json.dumps(pattern) for pattern in base)
    value += '\n' + BEGIN + '\n' + '\n'.join('      - ' + json.dumps(name) for name in generated) + '\n' + END
    prefix, marker, rest = source.partition('\nenv:')
    assert marker, 'Workflow env boundary missing'
    prefix, count = re.subn(r'^    paths:.*?(?=^  \w+:|\Z)',
                            lambda _: '    paths:\n' + value + '\n', prefix,
                            flags=re.MULTILINE | re.DOTALL)
    assert count == 2, 'Expected PR and push path filters'
    return prefix.rstrip() + '\n\n' + 'env:' + rest


def write_filters(root, closure):
    (root / WORKFLOW).write_text(render_filters(root, closure), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    if args.probe:
        print('DESKTOP_INPUTS=' + json.dumps(probe(ROOT, json.load(sys.stdin))))
    else:
        closure = dependency_closure(ROOT)
        if args.write:
            write_filters(ROOT, closure)
        print(json.dumps({**closure, 'missing': missing_inputs(ROOT, closure)}, indent=2))


if __name__ == '__main__':
    main()
