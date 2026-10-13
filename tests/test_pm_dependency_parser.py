"""Conservative parsing: supported observations, never invented resolution or relationships."""
import json

from bat_agent_connector.pm_dependency_parser import Inventory, workflow
from bat_agent_connector.pm_dependency_paths import allowed_path


def inventory(contents):
    files = [{"path": path, "blob_sha": "a" * 40, "size": len(content.encode()), "sha256": "b" * 64,
              "status": "read", "reason": None} for path, content in contents.items()]
    result = Inventory("fixture/repo", "c" * 40, files, contents).parse()
    return {**result, "files": files}


def test_go_require_checksum_catalog_and_toolchain_are_not_false_lock_verification():
    result = inventory({"go.mod": "module example.com/component\n\ngo 1.22\ntoolchain go1.22.1\nrequire (\n example.com/lib v1.2.3 // indirect\n)\n",
        "go.sum": "example.com/lib v1.2.3 h1:fixture\nexample.com/lib v1.2.3/go.mod h1:fixture\n"})
    assert result["components"][0]["name"] == "example.com/component"
    runtime = [edge for edge in result["edges"] if edge["relation"] == "runtime"]
    assert len(runtime) == 1 and runtime[0]["version"] == "v1.2.3"
    assert runtime[0]["resolved"] is None and runtime[0]["evidence_level"] == "declared"
    assert {e["version"] for e in result["edges"] if e["relation"] == "build"} == {"1.22", "go1.22.1"}


def test_ambiguous_uv_versions_remain_declared_and_bad_structures_are_visible():
    result = inventory({"pyproject.toml": '[project]\nname="sample"\ndependencies=["library>=1"]\n',
        "uv.lock": '[[package]]\nname="library"\nversion="1.0"\n[[package]]\nname="library"\nversion="2.0"\n',
        "other/package.json": '{"name":"invalid","dependencies":[]}', "Cargo.toml": '[package]\nname=42\n'})
    assert len(result["components"]) == 1
    assert result["edges"][0]["resolved"] is None and result["edges"][0]["evidence_level"] == "declared"
    assert {f["path"] for f in result["files"] if f["status"] == "unparsable"} == {"other/package.json", "Cargo.toml"}


def test_cargo_workspace_dependencies_require_explicit_inheritance():
    result = inventory({"Cargo.toml": '[workspace]\nmembers=["crate"]\n[workspace.dependencies]\nserde="1"\n',
        "crate/Cargo.toml": '[package]\nname="crate"\nversion="1.0.0"\npublish=false\n[dependencies]\nserde={workspace=true}\n',
        "Cargo.lock": '[[package]]\nname="serde"\nversion="1.0.200"\n'})
    assert result["components"][0]["published_name"] is None
    edge = result["edges"][0]
    assert edge["relation"] == "runtime" and edge["version"] == "1" and edge["resolved"] == "1.0.200"
    assert {r["path"] for r in edge["evidence_refs"]} == {"Cargo.toml", "crate/Cargo.toml", "Cargo.lock"}


def test_workflow_hints_bind_exact_working_directory_and_commands_not_mentions():
    result = inventory({"web/package.json": json.dumps({"name": "web", "devDependencies": {"vite": "^6", "vitest": "^3"},
        "scripts": {"test": "echo vitest", "build": "echo vite"}}),
        ".github/workflows/ci.yml": '''name: CI
jobs:
  app:
    defaults:
      run:
        working-directory: web
    steps:
      - run: |
          echo 'vitest test'
          vite build
      - run: vitest run
  wrong:
    steps:
      - run: npm test
        working-directory: unrelated
'''})
    names = {d["key"]: d["name"] for d in result["dependencies"]}
    edge = [e for e in result["edges"] if names[e["dependency_key"]] == "vite"][0]
    assert edge["relation"] == "build"
    assert edge["evidence_refs"][1]["job_id"] == "app"
    assert not any(e["basis"] == "npm_script_or_ci" and any("script" in r for r in e["evidence_refs"]) for e in result["edges"])
    assert {h["relation"] for h in result["verification_hints"]} == {"build", "test"}
    assert all(h["job_id"] != "wrong" for h in result["verification_hints"])


def test_lockfile_bins_are_evidence_for_custom_npm_tools():
    result = inventory({"package.json": json.dumps({"name": "web", "devDependencies": {"fixture-tool": "^2"}, "scripts": {"test:ui": "fixture-check --all"}}),
        "package-lock.json": json.dumps({"lockfileVersion": 3, "packages": {"node_modules/fixture-tool": {"version": "2.1.0", "bin": {"fixture-check": "bin/check.js"}}}})})
    edge = result["edges"][0]
    assert edge["relation"] == "test" and edge["resolved"] == "2.1.0"
    assert edge["evidence_refs"][1]["script"] == "test:ui"


def test_node_lock_resolution_uses_component_path_before_hoisted_root():
    result = inventory({"package-lock.json": json.dumps({"lockfileVersion": 3, "packages": {
        "app": {"name": "app"},
        "node_modules/lib": {"version": "1.0.0"}, "app/node_modules/lib": {"version": "2.0.0"}}}),
        "app/package.json": '{"name":"app","dependencies":{"lib":"^2"}}'})
    assert result["edges"][0]["resolved"] == "2.0.0"


def test_fixed_allowlist_and_multiline_yaml_locations():
    assert allowed_path("desktop/src-tauri/Cargo.toml")
    assert allowed_path("requirements-dev.txt") and allowed_path(".github/workflows/test.yaml")
    for path in ("../package.json", "node_modules/a/package.json", "x/.venv/pyproject.toml", "a/../../go.mod", "a/.github/workflows/test.yml", "package.json\0"):
        assert not allowed_path(path)
    steps = workflow("jobs:\n  test:\n    steps:\n      - run: |\n          cargo test\n          cargo check\n        working-directory: crate\n")
    assert steps[0]["line"] == 4 and steps[0]["working_directory"] == "crate"
    assert "cargo test" in steps[0]["command"] and "cargo check" in steps[0]["command"]


def test_legacy_named_rust_toolchain_can_contain_toml():
    result = inventory({"Cargo.toml": '[package]\nname="crate"\nversion="1"\n', "rust-toolchain": '[toolchain]\nchannel="1.80"\n'})
    assert result["edges"][0]["version"] == "1.80" and result["edges"][0]["relation"] == "build"


def test_policy_controller_is_not_an_inferred_maintenance_owner(monkeypatch):
    from bat_agent_connector import pm_dependency_reads
    monkeypatch.setattr(pm_dependency_reads.pm_maintenance_policy, "current_policy", lambda *args: {
        "policy": {"policy_id": "policy", "document": {"controller": {"actor": "adopting-owner"}, "policy": {}}}})
    assert pm_dependency_reads._owner(None, None, "workspace") is None


def test_ci_python_module_invocation_is_actual_test_evidence():
    result = inventory({"pyproject.toml": '[project]\nname="sample"\n[dependency-groups]\nchecks=["pytest>=8"]\n',
        ".github/workflows/test.yml": 'jobs:\n  checks:\n    steps:\n      - run: uv run python -m pytest -q\n'})
    assert any(e["relation"] == "test" and e["basis"] == "python_group:checks" for e in result["edges"])


def test_repeated_build_evidence_is_one_relation_with_both_script_references():
    result = inventory({"package.json": json.dumps({"name": "web", "devDependencies": {"vite": "^6"},
        "scripts": {"build": "vite build", "build:browser": "vite build --mode browser"}})})
    assert len(result["edges"]) == 1
    assert {r["script"] for r in result["edges"][0]["evidence_refs"] if "script" in r} == {"build", "build:browser"}


def test_unversioned_workflow_tool_is_null_with_a_reason():
    result = inventory({"pyproject.toml": '[project]\nname="sample"\n',
        ".github/workflows/test.yml": 'jobs:\n  checks:\n    steps:\n      - run: uv run pytest -q\n'})
    edge = next(e for e in result["edges"] if e["basis"] == "workflow_step")
    assert edge["version"] is None and edge["version_reason"] == "not_declared"
    assert edge["resolved"] is None and edge["resolution_reason"] == "not_lockfile_dependency"


def test_unreadable_nearest_lock_does_not_borrow_an_ancestor_resolution():
    result = inventory({"package-lock.json": json.dumps({"lockfileVersion": 3, "packages": {"node_modules/lib": {"version": "1.0.0"}}}),
        "app/package.json": '{"name":"app","dependencies":{"lib":"^2"}}', "app/package-lock.json": '{invalid'})
    assert result["edges"][0]["resolved"] is None
    assert result["edges"][0]["evidence_level"] == "declared"
    assert next(f for f in result["files"] if f["path"] == "app/package-lock.json")["status"] == "unparsable"


def test_python_requirement_fields_must_be_lists_not_strings():
    for fragment in ('dependencies="bad"', '[build-system]\nrequires="bad"',
                     '[project.optional-dependencies]\ntest="bad"', '[dependency-groups]\ndev="bad"'):
        result = inventory({"pyproject.toml": '[project]\nname="sample"\n' + fragment})
        assert result["components"] == result["edges"] == result["dependencies"] == []
        assert result["files"][0]["status"] == "unparsable"


def test_dynamic_python_dependencies_are_unknown_without_explicit_source():
    result = inventory({'pyproject.toml': '[project]\nname="app"\ndynamic=["dependencies"]\n',
                        'requirements.txt': 'requests==2.31.0\n'})
    assert 'python_dynamic_dependencies' in result['parser_reasons']
    assert result['components'][0]['runtime_dependencies_status'] == 'unknown'
    assert not result['edges']


def test_dynamic_python_dependencies_follow_only_explicit_setuptools_files():
    result = inventory({'pyproject.toml': '[project]\nname="app"\ndynamic=["dependencies"]\n'
        '[tool.setuptools.dynamic]\ndependencies={file=["requirements.txt"]}\n',
        'requirements.txt': 'requests==2.31.0\n', 'requirements-dev.txt': 'unrelated==1\n'})
    assert 'python_dynamic_dependencies' in result['parser_reasons']
    assert len(result['edges']) == 1 and result['edges'][0]['relation'] == 'runtime'
    assert {ref['path'] for ref in result['edges'][0]['evidence_refs']} == {'pyproject.toml', 'requirements.txt'}


def test_python_extras_and_groups_with_same_name_preserve_both_identities():
    result = inventory({'pyproject.toml': '[project]\nname="app"\n[project.optional-dependencies]\n'
        'dev=["pytest>=8"]\n[dependency-groups]\ndev=["ruff>=0.9"]\n'})
    assert {d['name'] for d in result['dependencies']} == {'pytest', 'ruff'}
    assert {ref['declaration_group'] for e in result['edges'] for ref in e['evidence_refs'] if 'declaration_group' in ref} == {'extra:dev', 'group:dev'}


def test_npm_peer_and_optional_dependencies_make_coverage_partial():
    result = inventory({'package.json': json.dumps({'name': 'app', 'peerDependencies': {'react': '^19'},
                                                   'optionalDependencies': {'fsevents': '^2'}})})
    assert {'npm_peer_dependencies_not_captured', 'npm_optional_dependencies_not_captured'} <= set(result['parser_reasons'])


def test_ancestor_lockfiles_do_not_verify_unrelated_nested_packages():
    for contents in (
        {'tools/x/pyproject.toml': '[project]\nname="outside"\ndependencies=["requests>=2"]\n',
         'uv.lock': '[[package]]\nname="requests"\nversion="2.31.0"\n'},
        {'tools/x/package.json': '{"name":"outside","dependencies":{"requests":"^2"}}',
         'package-lock.json': '{"lockfileVersion":3,"packages":{"node_modules/requests":{"version":"2.31.0"}}}'},
        {'tools/x/Cargo.toml': '[package]\nname="outside"\nversion="1"\n[dependencies]\nrequests="2"\n',
         'Cargo.lock': '[[package]]\nname="requests"\nversion="2.31.0"\nsource="registry+example"\n'},
    ):
        edge = inventory(contents)['edges'][0]
        assert edge['resolved'] is None and edge['evidence_level'] == 'declared'
        assert edge['resolution_reason'] == 'lockfile_does_not_cover_component'


def test_python_names_normalize_for_lock_keys_and_cross_repository_links():
    from bat_agent_connector.pm_dependency_parser import dependency_key
    from bat_agent_connector.pm_dependency_reads import _links
    consumer = inventory({'pyproject.toml': '[project]\nname="app"\ndependencies=["PyYAML>=6", "pyyaml>=6", "typing_extensions>=4", "foo_bar>=1"]\n',
        'uv.lock': '[[package]]\nname="pyyaml"\nversion="6.0.2"\n[[package]]\nname="typing-extensions"\nversion="4.12.0"\n'})
    assert len(consumer['dependencies']) == 3
    assert dependency_key('python', 'PyYAML') == dependency_key('python', 'pyyaml')
    assert {e['resolved'] for e in consumer['edges']} >= {'6.0.2', '4.12.0'}
    provider = inventory({'lib/pyproject.toml': '[project]\nname="foo-bar"\nversion="1"\n'})
    assert _links([consumer, provider])[0]['target_component_id'] == provider['components'][0]['component_id']


def test_workflow_defaults_do_not_leak_into_step_or_next_job():
    steps = workflow('''defaults:
  run:
    shell: bash
jobs:
  mixed:
    steps:
      - run: npm ci
        working-directory: desktop
      - run: uv run pytest
  other:
    defaults:
      run:
        working-directory: web
    steps:
      - run: npm test
        working-directory: alternate
      - run: npm run build
  last:
    steps:
      - run: cargo test
''')
    assert [s['working_directory'] for s in steps] == ['desktop', '.', 'alternate', 'web', '.']
    steps = workflow('defaults:\n  unrelated:\n    working-directory: wrong\njobs:\n  test:\n    steps:\n      - run: uv run pytest\n')
    assert steps[0]['working_directory'] == '.'


def test_cargo_inherited_package_fields_and_dependencies_keep_the_crate():
    result = inventory({'Cargo.toml': '[workspace]\nmembers=["crates/*"]\n[workspace.package]\nversion="1.2.3"\nrust-version="1.80"\n'
        '[workspace.dependencies]\nserde="1"\n',
        'crates/app/Cargo.toml': '[package]\nname="app"\nversion.workspace=true\nrust-version.workspace=true\n[dependencies]\nserde.workspace=true\n'})
    assert len(result['components']) == 1 and result['components'][0]['version'] == '1.2.3'
    assert {e['version'] for e in result['edges']} == {'1', '1.80'}
    assert all(f['status'] == 'read' for f in result['files'])
    missing = inventory({'crates/app/Cargo.toml': '[package]\nname="app"\nversion.workspace=true\nrust-version.workspace=true\n[dependencies]\nserde.workspace=true\n'})
    assert missing['components'][0]['version'] is None
    assert missing['components'][0]['version_reason'] == 'workspace_inheritance_unavailable'
    assert len(missing['edges']) == 2 and all(e['version'] is None for e in missing['edges'])
    assert 'cargo_workspace_inheritance_unavailable' in missing['parser_reasons']


def test_workflow_actions_are_not_declared_component_dependencies_from_job_proximity():
    result = inventory({'pyproject.toml': '[project]\nname="api"\n', 'web/package.json': '{"name":"web"}',
        '.github/workflows/ci.yml': 'jobs:\n  mixed:\n    steps:\n      - uses: actions/setup-node@v4\n'
        '      - uses: astral-sh/setup-uv@v6\n      - run: uv run pytest\n      - run: npm test\n        working-directory: web\n'})
    assert not any(e['basis'] == 'workflow_uses' and e['evidence_level'] == 'declared' for e in result['edges'])


def test_workflow_multi_command_and_wrapped_tools_keep_all_relations():
    for manifest, command, expected in (
        ('Cargo.toml', 'cargo build && cargo test', {'build', 'test'}),
        ('package.json', 'npx vitest run', {'test'}),
        ('package.json', 'pnpm exec vitest run', {'test'}),
        ('pyproject.toml', 'poetry run pytest', {'test'}),
        ('pyproject.toml', 'uv run pytest', {'test'}),
    ):
        content = {'Cargo.toml': '[package]\nname="app"\n', 'package.json': '{"name":"app"}',
                   'pyproject.toml': '[project]\nname="app"\n'}[manifest]
        result = inventory({manifest: content, '.github/workflows/ci.yml': 'jobs:\n  checks:\n    steps:\n      - run: ' + command + '\n'})
        assert {h['relation'] for h in result['verification_hints']} == expected, command


def test_unnamed_npm_app_keeps_dependencies_without_claiming_package_identity():
    result = inventory({'web/package.json': '{"private":true,"dependencies":{"lib":"^1"}}'})
    assert result['files'][0]['status'] == 'read'
    assert result['components'][0]['name'] == 'web/package.json'
    assert result['components'][0]['published_name'] is None
    assert result['components'][0]['name_reason'] == 'package_name_not_declared'
    assert result['edges'][0]['version'] == '^1'


def test_uv_ancestor_resolution_requires_recorded_workspace_coverage():
    manifest = '[project]\nname="app"\ndependencies=["lib>=1"]\n'
    lock = '[[package]]\nname="lib"\nversion="1.2.0"\n'
    for proof in (
        {'pyproject.toml': '[tool.uv.workspace]\nmembers=["packages/*"]\n'},
        {'uv.lock': lock + '[[package]]\nname="app"\nsource={editable="packages/app"}\n'},
        {'uv.lock': lock + '[[package]]\nname="app"\nsource={virtual="packages/app"}\n'},
        {'uv.lock': '[manifest]\nmembers=["app"]\n' + lock},
    ):
        result = inventory({'packages/app/pyproject.toml': manifest, 'uv.lock': lock, **proof})
        assert result['edges'][0]['resolved'] == '1.2.0'
    excluded = inventory({'packages/app/pyproject.toml': manifest, 'uv.lock': lock,
        'pyproject.toml': '[tool.uv.workspace]\nmembers=["packages/*"]\nexclude=["packages/app"]\n'})
    assert excluded['edges'][0]['resolution_reason'] == 'lockfile_does_not_cover_component'


def test_evidence_commands_are_capped_with_explicit_truncation():
    command = 'vite build ' + 'x' * 6000
    result = inventory({'package.json': json.dumps({'name': 'app', 'devDependencies': {'vite': '^6'}, 'scripts': {'build': command}})})
    ref = next(r for r in result['edges'][0]['evidence_refs'] if 'command' in r)
    assert len(ref['command'].encode()) <= 512
    assert ref['command_truncated'] and len(ref['command_sha256']) == 64
    assert 'evidence_command_truncated' in result['parser_reasons']


def test_mixed_ci_commands_classify_each_dev_binary_by_its_own_call():
    result = inventory({'package.json': '{"name":"app","devDependencies":{"vite":"^6","vitest":"^3"}}',
        '.github/workflows/test.yml': 'jobs:\n  checks:\n    steps:\n      - run: vite build && npx vitest run\n'})
    names = {d['key']: d['name'] for d in result['dependencies']}
    assert {(names[e['dependency_key']], e['relation']) for e in result['edges'] if e['basis'] == 'npm_script_or_ci'} == {('vite', 'build'), ('vitest', 'test')}
