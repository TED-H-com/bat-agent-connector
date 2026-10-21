"""Desktop path filters cover real fixture imports, on PRs and main pushes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci_scope import native_builds, select_scope
from scripts.desktop_fixture_inputs import (
    command_inputs,
    dependency_closure,
    filters,
    missing_inputs,
    path_matches,
    referenced_files,
    render_filters,
    tracked_files,
    triggered,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def desktop_inputs():
    return dependency_closure(ROOT)


def test_desktop_filters_cover_real_fixture_imports(desktop_inputs):
    missing = missing_inputs(ROOT, desktop_inputs)
    assert not any(missing.values()), json.dumps(missing, indent=2)
    assert filters(ROOT)['pull_request'] == filters(ROOT)['push']
    assert (ROOT / '.github/workflows/desktop.yml').read_text() == render_filters(ROOT, desktop_inputs)
    # Anchors keep a broken parser/import probe from vacuously passing. These
    # are assertions about discovered paths, not the authority used to derive them.
    assert {'tests/conftest.py', 'tests/mockbat.py', 'tests/fakegithub.py',
            'tests/test_api_v1.py', 'tests/test_repository_sync.py'} <= set(desktop_inputs['loaded'])
    assert {'tests/upgrade_continuity_helpers.py', 'tests/test_platform_files.py',
            'tests/test_windows_central.py', 'scripts/ci_scope.py'} <= set(desktop_inputs['files'])


def test_computed_golden_read_is_not_silently_omitted(desktop_inputs):
    assert 'tests/fixtures/pm-version-facts.json' in desktop_inputs['files'], (
        'desktop/tests/version-projection.test.ts:8: computed golden path must be resolved or rejected')


@pytest.mark.parametrize('path,pattern,expected', [
    ('desktop/src/app.js', 'desktop/**', True),
    ('desktop/src/app.js', 'desktop/*', False),
    ('desktop/app.js', 'desktop/*', True),
    ('desktop/.hidden/file', 'desktop/**', True),
    ('desktop-other/app.js', 'desktop/**', False),
    ('.github/workflows/desktop.yml', '.github/workflows/desktop*.yml', True),
    ('.github/workflows/desktop-release.yml', '.github/workflows/desktop*.yml', True),
    ('.github/workflows/desktop/nested.yml', '.github/workflows/desktop*.yml', False),
    ('tests/test_api_v1.py', 'tests/test_api_v1.py', True),
    ('tests/test_api_v1_extra.py', 'tests/test_api_v1.py', False),
    ('README.md', '**/README.md', True),
    ('a/b/README.md', '**/README.md', True),
    ('a/b.js', '**.js', True),
    ('Desktop/app.js', 'desktop/**', False),
    ('docs/a.md', 'desktop/**', False),
    ('tests/中文 draft.py', 'tests/中文 draft.py', True),
])
def test_github_path_pattern_subset(path, pattern, expected):
    assert path_matches(path, pattern) is expected


@pytest.mark.parametrize('pattern', ['!tests/**', '*.jsx?', '[ab].py', 'x+', '{a,b}', r'tests\file.py'])
def test_unsupported_pattern_syntax_fails_closed(pattern):
    with pytest.raises(ValueError, match='Unsupported workflow path pattern'):
        triggered(['desktop/app.js'], ['desktop/**', pattern])


@pytest.mark.parametrize('path,starts,native,count', [
    ('tests/conftest.py', True, 'all', 4),
    ('tests/test_api_v1.py', True, 'all', 4),
    ('tests/fixtures/pm-version-facts.json', True, 'all', 4),
    ('tests/test_ci_scope.py', False, 'all', 0),
    ('desktop/src/app.js', True, 'none', 0),
    ('docs/design/workbench.md', False, 'none', 0),
])
def test_simulated_pr_diffs(path, starts, native, count):
    patterns = filters(ROOT)
    assert triggered([path], patterns['pull_request']) is starts
    assert triggered([path], patterns['push']) is starts
    selected = select_scope([path])[1]
    assert selected == native  # Scope is unchanged, even when workflow is filtered out.
    assert (len(native_builds(selected)) if starts else 0) == count
    push = select_scope([path], event_name='push')[1]
    assert (len(native_builds(push)) if starts else 0) == (4 if starts else 0)


def test_discovery_follows_new_npm_entrypoint_and_function_import(tmp_path):
    desktop = tmp_path / 'desktop'
    desktop.mkdir()
    workflow = tmp_path / '.github/workflows/desktop.yml'
    workflow.parent.mkdir(parents=True)
    workflow.write_text('jobs:\n  example:\n    defaults:\n      run:\n        working-directory: desktop\n'
                        '    steps:\n      - run: npm run test:example\n')
    (desktop / 'package.json').write_text(json.dumps({'scripts': {'test:example': 'node tests/entry.mjs'}}))
    entry = desktop / 'tests/entry.mjs'
    entry.parent.mkdir()
    entry.write_text("spawn(python, [resolve('tests/future_fixture.py')]);\n")
    fixture = tmp_path / 'tests/future_fixture.py'
    fixture.parent.mkdir()
    fixture.write_text('def deferred():\n    from tests.future_helper import value\n')
    helper = tmp_path / 'tests/future_helper.py'
    helper.write_text('value = 1\n')
    tracked = {'desktop/tests/entry.mjs', 'tests/future_fixture.py', 'tests/future_helper.py'}
    assert command_inputs(tmp_path, tracked)['example'] == {'desktop/tests/entry.mjs'}
    assert 'tests/future_fixture.py' in referenced_files(tmp_path, entry.read_text(), entry.parent, tracked)
    from scripts.desktop_fixture_inputs import local_imports
    assert local_imports(tmp_path, 'tests/future_fixture.py') == {'tests/future_helper.py'}


@pytest.mark.parametrize('name,source,line', [
    ('desktop/tests/read.ts', 'const name = process.env.FIXTURE;\n'
     'readFileSync(new URL(`../../tests/fixtures/${name}.json`, import.meta.url));\n', 2),
    ('desktop/tests/read.mjs', "const base = '../../tests/fixtures/';\nreadFileSync(base + name + '.json');\n", 2),
    ('desktop/tests/read.js', "readFileSync(join(root, 'tests', 'fixtures', name));\n", 1),
    ('desktop/tests/read.js', "readFileSync(join(root, 'scripts', name));\n", 1),
    ('desktop/tests/read.js', "readFileSync(`../../tests/${name}/../fixed.json`);\n", 1),
    ('desktop/tests/read.ts', "readFileSync(new URL(`../../${area}/${name}`, import.meta.url));\n", 1),
    ('desktop/tests/read.ts', "let base = '../../tests/fixtures/';\nbase = runtime;\nreadFileSync(base + name);\n", 1),
    ('tests/read.py', 'from pathlib import Path\n'
     'value = (Path(__file__).parent / "fixtures" / name).read_text()\n', 2),
    ('tests/read.py', 'from pathlib import Path\n'
     'value = Path(__file__).parent.joinpath("fixtures", name).read_bytes()\n', 2),
    ('tests/read.py', 'from os.path import join\nvalue = open(join(root, "tests", name)).read()\n', 2),
    ('tests/read.py', 'from pathlib import Path\n'
     'root = Path(__file__).parent.parent\nvalue = (root / area / name).read_text()\n', 3),
    ('tests/read.py', 'base = "tests/fixtures/"\nbase = runtime\nvalue = open(base + name).read()\n', 1),
])
def test_computed_path_detector_fails_closed_in_workflow_closure(tmp_path, name, source, line):
    import yaml

    from scripts.desktop_fixture_paths import fixture_paths

    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.write_text(source)
    workflow = tmp_path / '.github/workflows/desktop.yml'
    workflow.parent.mkdir(parents=True)
    workflow.write_text(yaml.safe_dump({'jobs': {'example': {'steps': [{'run': f'node {name}'}]}}}))
    package = tmp_path / 'desktop/package.json'
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text('{"scripts": {}}')
    tracked = {name, 'tests/fixtures/value.json'}
    entries = command_inputs(tmp_path, tracked)['example']
    assert entries == {name}
    with pytest.raises(AssertionError, match=rf'{name}:{line}: unresolved computed path'):
        fixture_paths(tmp_path, {file: (tmp_path / file).read_text() for file in entries}, tracked)


def test_literal_path_joins_and_data_basenames_are_resolved(tmp_path):
    from scripts.desktop_fixture_paths import fixture_paths

    sources = {
        'tests/read.py': 'from pathlib import Path\n'
        'value = (Path(__file__).parent / "fixtures" / "value.json").read_text()\n',
        'desktop/tests/read.ts': "readFileSync(join(root, 'tests', 'fixtures', 'value.json'));\n"
        "const file = '../../tests/fixtures/' + 'value' + '.json';\n",
    }
    tracked = {*sources, 'tests/fixtures/value.json', 'tests/fixtures/value.tsv', 'site/images/value.json'}
    resolved, audit = fixture_paths(tmp_path, sources, tracked)
    assert resolved == {'tests/fixtures/value.json'}
    assert {value['file'] for value in audit} == set(sources)
    assert referenced_files(tmp_path, '"value.json" "value.tsv"', tmp_path, tracked) == {
        'tests/fixtures/value.json', 'tests/fixtures/value.tsv', 'site/images/value.json'}


def test_path_parser_rejects_invalid_typescript_with_source_location(tmp_path):
    from scripts.desktop_fixture_paths import fixture_paths

    with pytest.raises(AssertionError, match='desktop/read.ts:1: cannot parse fixture paths'):
        fixture_paths(tmp_path, {'desktop/read.ts': 'const file = ;'}, set())


def test_filter_generation_preserves_jobs_and_is_idempotent(tmp_path):
    import yaml

    path = tmp_path / '.github/workflows/desktop.yml'
    path.parent.mkdir(parents=True)
    original = (ROOT / '.github/workflows/desktop.yml').read_text()
    path.write_text(original)
    closure = {'files': ['tests/future_helper.py', 'desktop/src/app.js']}
    generated = render_filters(tmp_path, closure)
    path.write_text(generated)
    assert render_filters(tmp_path, closure) == generated
    assert yaml.safe_load(generated)['jobs'] == yaml.safe_load(original)['jobs']
    assert filters(tmp_path)['pull_request'] == filters(tmp_path)['push']
    assert triggered(['tests/future_helper.py'], filters(tmp_path)['push'])


def test_probe_runs_seed_imports_and_records_dynamic_imports_and_reads(tmp_path):
    import subprocess
    import sys

    for name, content in {
        'pyproject.toml': '[project]\nname="probe"\n[tool.hatch.build.targets.wheel]\npackages=[]\n',
        'tests/runtime_fixture.py': "import importlib; importlib.import_module('tests.runtime_leaf')\n",
        'tests/runtime_leaf.py': "from pathlib import Path; Path(__file__).with_name('payload.json').read_text()\n",
        'tests/payload.json': '{}\n',
        'tests/中文 draft.py': '# A tracked path must survive Git quoting.\n',
        'desktop/tests/seed.py': "import os; assert os.environ['BATC_UPGRADE_SEED_MODE'] == '1'; import tests.runtime_fixture\n",
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    for args in (['git', 'init', '-q'], ['git', 'add', '.']):
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)
    assert 'tests/中文 draft.py' in tracked_files(tmp_path)
    child = subprocess.run([sys.executable, '-c',
        'import json, sys; from pathlib import Path; '
        'from scripts.desktop_fixture_inputs import probe; '
        'print(json.dumps(probe(Path(sys.argv[1]), ["desktop/tests/seed.py"])))', str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True, check=True)
    facts = json.loads(child.stdout)
    assert {'tests/runtime_fixture.py', 'tests/runtime_leaf.py'} <= set(facts['loaded'])
    assert 'tests/payload.json' in facts['read']
