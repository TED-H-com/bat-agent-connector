"""Conservative parsing: supported observations, never invented resolution or relationships."""
import json
import time

import pytest

from bat_agent_connector.pm_dependency_parser import Inventory, dependency_key, member_path, workflow
from bat_agent_connector.pm_dependency_paths import allowed_path
from bat_agent_connector.pm_release_baselines import _size


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
    assert any(e["relation"] == "test" and e["basis"] == "python_group" and e["evidence_refs"][0]["declaration_group"] == "group:checks" for e in result["edges"])


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
    assert not result['parser_reasons']
    assert 'python_dynamic_dependencies' in result['parser_notes']
    assert len(result['edges']) == 2 and result['edges'][0]['relation'] == 'runtime'
    assert result['edges'][1]['basis'] == 'dev_unclassified'
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
    assert steps[0]['working_directory'] is None
    assert steps[0]['working_directory_reason'] == 'workflow_directory_unknown'


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
    ):
        result = inventory({'packages/app/pyproject.toml': manifest, 'uv.lock': lock, **proof})
        assert result['edges'][0]['resolved'] == '1.2.0'
    name_only = inventory({'packages/app/pyproject.toml': manifest, 'uv.lock': '[manifest]\nmembers=["app"]\n' + lock})
    assert name_only['edges'][0]['resolution_reason'] == 'lockfile_does_not_cover_component'
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


def comp(result, path):
    return next(c for c in result["components"] if c["manifest_path"] == path)


def edges(result, eco, name, path=None, relation=None):
    key = dependency_key(eco, name)
    cid = comp(result, path)["component_id"] if path else None
    return [e for e in result["edges"] if e["dependency_key"] == key
            and (cid is None or e["component_id"] == cid) and (relation is None or e["relation"] == relation)]


def hints(result, path):
    cid = comp(result, path)["component_id"]
    return [h for h in result["verification_hints"] if h["component_id"] == cid]


def test_regression_python_optional_extras_are_not_presented_as_build_toolchain():
    # [project.optional-dependencies] are optional *runtime* extras, not dev tooling.
    r = inventory({"pyproject.toml": '[project]\nname="app"\n[project.optional-dependencies]\npostgres=["psycopg>=3"]\n'})
    e = r["edges"][0]
    assert (e["relation"], e["kind"]) != ("build", "build_test_toolchain"), e


def test_regression_tool_specific_dev_dependency_tables_are_not_silently_dropped():
    for table in ('[tool.uv]\ndev-dependencies=["pytest>=8"]\n',
                  '[tool.poetry.group.dev.dependencies]\npytest="^8"\n',
                  '[tool.pdm.dev-dependencies]\ntest=["pytest>=8"]\n'):
        r = inventory({"pyproject.toml": '[project]\nname="app"\n' + table})
        assert r["edges"] or r["parser_reasons"], table


def test_regression_dynamic_capture_drops_unreferenced_requirements_files():
    base = {"pyproject.toml": '[project]\nname="app"\n', "requirements-dev.txt": "pytest==8\n"}
    static = inventory(base)
    assert edges(static, "python", "pytest")            # static: build/dev_unclassified edge
    dynamic = inventory({**base, "pyproject.toml": '[project]\nname="app"\ndynamic=["dependencies"]\n'
                         '[tool.setuptools.dynamic]\ndependencies={file=["requirements.txt"]}\n',
                         "requirements.txt": "requests==2\n"})
    # requirements-dev.txt is not the dynamic source; it should keep the same unclassified edge.
    assert edges(dynamic, "python", "pytest")


def test_regression_uv_manifest_members_name_only_verifies_a_namesake_non_member():
    lock = ('version=1\n[manifest]\nmembers=["app"]\n'
            '[[package]]\nname="app"\nversion="0.1.0"\nsource={editable="packages/app"}\n'
            '[[package]]\nname="lib"\nversion="1.2.0"\n')
    r = inventory({"pyproject.toml": '[tool.uv.workspace]\nmembers=["packages/*"]\n',
                   "packages/app/pyproject.toml": '[project]\nname="app"\ndependencies=["lib>=1"]\n',
                   "examples/app/pyproject.toml": '[project]\nname="app"\ndependencies=["lib>=1"]\n',
                   "uv.lock": lock})
    assert edges(r, "python", "lib", "packages/app/pyproject.toml")[0]["evidence_level"] == "verified"
    example = edges(r, "python", "lib", "examples/app/pyproject.toml")[0]
    assert example["evidence_level"] == "declared", example


def test_regression_cargo_local_name_version_match_verifies_an_excluded_namesake():
    lock = ('version=3\n[[package]]\nname="app"\nversion="0.1.0"\ndependencies=["serde"]\n'
            '[[package]]\nname="serde"\nversion="1.0.200"\nsource="registry+https://github.com/rust-lang/crates.io-index"\n')
    r = inventory({"Cargo.toml": '[workspace]\nmembers=["crates/*"]\nexclude=["examples/app"]\n',
                   "crates/app/Cargo.toml": '[package]\nname="app"\nversion="0.1.0"\n[dependencies]\nserde="1"\n',
                   "examples/app/Cargo.toml": '[package]\nname="app"\nversion="0.1.0"\n[dependencies]\nserde="1"\n',
                   "Cargo.lock": lock})
    example = edges(r, "cargo", "serde", "examples/app/Cargo.toml")[0]
    assert example["evidence_level"] == "declared", example


def test_regression_workflow_defaults_after_jobs_are_ignored():
    # GitHub applies workflow-level defaults regardless of key order.
    steps = workflow('jobs:\n  t:\n    steps:\n      - run: npm test\ndefaults:\n  run:\n    working-directory: web\n')
    assert steps[0]["working_directory"] == "web", steps


def test_regression_job_defaults_after_steps_are_ignored():
    steps = workflow('jobs:\n  t:\n    steps:\n      - run: npm test\n    defaults:\n      run:\n        working-directory: web\n')
    assert steps[0]["working_directory"] == "web", steps


def test_regression_unparsed_defaults_forms_attribute_steps_to_repository_root():
    for defaults in ('defaults:\n  run: {working-directory: web}\n',
                     'defaults:  # shared\n  run:\n    working-directory: web\n',
                     'defaults:\n  run:  # shared\n    working-directory: web\n'):
        r = inventory({"package.json": '{"name":"root","devDependencies":{"vitest":"^3"}}',
                       "web/package.json": '{"name":"web","devDependencies":{"vitest":"^3"}}',
                       ".github/workflows/ci.yml": defaults + 'jobs:\n  t:\n    steps:\n      - run: npx vitest run\n'})
        # The step runs in web/. Unknown directory must mean "no association", not the root package.
        assert not hints(r, "package.json"), defaults


def test_regression_mid_command_cd_is_attributed_to_the_step_directory():
    r = inventory({"package.json": '{"name":"root"}', "web/package.json": '{"name":"web"}',
                   ".github/workflows/ci.yml": 'jobs:\n  t:\n    steps:\n      - run: npm ci && cd web && npm test\n'})
    assert not hints(r, "package.json")


def test_regression_cargo_publish_workspace_inheritance_claims_publishable():
    r = inventory({"Cargo.toml": '[workspace]\nmembers=["crates/*"]\n[workspace.package]\npublish=false\n',
                   "crates/app/Cargo.toml": '[package]\nname="app"\nversion="1.0.0"\npublish.workspace=true\n'})
    assert comp(r, "crates/app/Cargo.toml")["published_name"] is None


def test_regression_other_evidence_strings_are_uncapped():
    script = "test:" + "x" * 200_000
    r = inventory({"package.json": json.dumps({"name": "app", "devDependencies": {"vitest": "^3"},
                                               "scripts": {script: "vitest run"}})})
    biggest = max(_size(ref) for e in r["edges"] for ref in e["evidence_refs"])
    assert biggest <= 4096, biggest


def test_regression_member_glob_backtracking_is_unbounded():
    pattern = "/".join(["**"] * 15 + ["nomatch"])
    start = time.perf_counter()
    member_path("a/b/c/d/e/f/g/h/i", [pattern])
    elapsed = time.perf_counter() - start
    print("one member_path call", elapsed)
    assert elapsed < 0.05, elapsed


def test_regression_lone_surrogate_script_name_breaks_capture_sizing():
    r = inventory({"package.json": '{"name":"app","devDependencies":{"vitest":"^3"},"scripts":{"test:\\ud800":"vitest run"}}'})
    _size(r)


@pytest.mark.parametrize('form', ['*settings', '{shell: bash, working-directory: ${{ matrix.dir }}}', '[web]'])
def test_unknown_workflow_defaults_never_mean_repository_root(form):
    result = inventory({'package.json': '{"name":"root"}', '.github/workflows/ci.yml':
        'defaults:\n  run: ' + form + '\njobs:\n  t:\n    steps:\n      - run: npm test\n'})
    assert not result['verification_hints']
    assert 'workflow_directory_unknown' in result['parser_reasons']


@pytest.mark.parametrize('target', ['$DIR', '-', '(web)', '$(pwd)'])
def test_unknown_cd_never_attributes_subsequent_commands_to_root(target):
    result = inventory({'package.json': '{"name":"root"}', '.github/workflows/ci.yml':
        'jobs:\n  t:\n    steps:\n      - run: npm ci && cd ' + target + ' && npm test\n'})
    assert not result['verification_hints']
    assert 'workflow_directory_unknown' in result['parser_reasons']


def test_cd_tracks_each_command_and_resets_for_next_step():
    result = inventory({'package.json': '{"name":"root"}', 'web/package.json': '{"name":"web"}',
        '.github/workflows/ci.yml': 'jobs:\n  t:\n    steps:\n      - run: npm ci && cd web && npm test\n      - run: npm run build\n'})
    assert {h['relation'] for h in hints(result, 'web/package.json')} == {'test'}
    assert {h['relation'] for h in hints(result, 'package.json')} == {'build'}


def test_optional_extras_are_runtime_and_keep_group_identity():
    result = inventory({'pyproject.toml': '[project]\nname="app"\n[project.optional-dependencies]\npostgres=["psycopg>=3"]\n'})
    edge = result['edges'][0]
    assert (edge['relation'], edge['kind'], edge['optional']) == ('runtime', 'runtime_library', True)
    assert edge['extras'] == ['postgres']
    assert edge['evidence_refs'][0]['declaration_group'] == 'extra:postgres'


@pytest.mark.parametrize('table', ['[tool.uv]\ndev-dependencies=["pytest>=8"]',
    '[tool.pdm.dev-dependencies]\ntest=["pytest>=8"]', '[tool.poetry.group.test.dependencies]\npytest="^8"',
    '[tool.poetry.dev-dependencies]\npytest="^8"'])
def test_tool_dev_groups_use_ci_evidence(table):
    result = inventory({'pyproject.toml': '[project]\nname="app"\n' + table,
        '.github/workflows/ci.yml': 'jobs:\n  t:\n    steps:\n      - run: uv run pytest\n'})
    assert edges(result, 'python', 'pytest', relation='test')
    assert not result['parser_reasons']


@pytest.mark.parametrize('table,field', [('[tool.uv]\ndev-dependencies={pytest="8"}', 'tool.uv.dev-dependencies'),
    ('[tool.pdm]\ndev-dependencies=["pytest"]', 'tool.pdm.dev-dependencies'),
    ('[tool.poetry.group.test]\ndependencies=["pytest"]', 'tool.poetry.group.test.dependencies'),
    ('[tool.poetry.dev-dependencies]\npytest=[{version="8"}]', 'tool.poetry.dev-dependencies')])
def test_unsupported_tool_dev_fields_are_partial_and_named(table, field):
    result = inventory({'pyproject.toml': '[project]\nname="app"\n' + table})
    assert 'unsupported_field' in result['parser_reasons']
    assert any(r['field'] == field for r in result['unsupported_fields'])
    assert result['files'][0]['status'] == 'read'


def test_member_globs_are_cached_for_two_thousand_dependencies():
    count = 2000
    pattern = '/'.join(['**'] * 15 + ['nomatch'])
    contents = {'pyproject.toml': '[tool.uv.workspace]\nmembers=["' + pattern + '"]\n',
        'a/b/c/d/e/f/g/h/pyproject.toml': '[project]\nname="app"\ndependencies=' + json.dumps([f'lib{i}>=1' for i in range(count)]),
        'uv.lock': ''.join(f'[[package]]\nname="lib{i}"\nversion="1"\n' for i in range(count))}
    start = time.perf_counter()
    result = inventory(contents)
    assert time.perf_counter() - start < 1
    assert result['edges'] and all(e['evidence_level'] == 'declared' for e in result['edges'])


@pytest.mark.parametrize('field', ['script', 'job_id', 'declaration_group'])
def test_all_evidence_identifiers_are_bounded(field):
    name = 'test:' + 'x' * 600
    if field == 'script':
        contents = {'package.json': json.dumps({'name': 'app', 'devDependencies': {'vitest': '3'}, 'scripts': {name: 'vitest run'}})}
    elif field == 'job_id':
        name = 'test_' + 'x' * 600
        contents = {'package.json': '{"name":"app"}', '.github/workflows/ci.yml': f'jobs:\n  {name}:\n    steps:\n      - run: npm test\n'}
    else:
        contents = {'pyproject.toml': '[project]\nname="app"\n[dependency-groups]\n"' + name + '"=["pytest>=8"]'}
    result = inventory(contents)
    refs = [r for e in result['edges'] for r in e['evidence_refs'] if field in r]
    assert refs and all(len(r[field].encode()) <= 512 and r[field + '_truncated'] for r in refs)


@pytest.mark.parametrize('contents', [
    {'Cargo.toml': '[workspace]\nmembers=["crate"]', 'crate/Cargo.toml': '[package]\nname="crate"'},
    {'package.json': '{"private":true,"dependencies":{"lib":"1"}}'},
    {'pyproject.toml': '[project]\nname="app"\ndynamic=["dependencies"]\n[tool.setuptools.dynamic]\ndependencies={file="requirements.txt"}',
     'requirements.txt': 'requests==2'}])
def test_complete_supported_manifests_do_not_claim_missing_coverage(contents):
    result = inventory(contents)
    assert not result['parser_reasons']
    assert all(f['status'] == 'read' for f in result['files'])


@pytest.mark.parametrize('field', ['name', 'version', 'script', 'dependency'])
def test_lone_surrogates_never_escape_into_inventory_json(field):
    manifest = {'name': 'app', 'version': '1', 'dependencies': {'lib': '1'},
                'devDependencies': {'vitest': '3'}, 'scripts': {'test': 'vitest run'}}
    if field in {'name', 'version'}:
        manifest[field] = '\ud800'
    elif field == 'script':
        manifest['scripts'] = {'test:\ud800': 'vitest run'}
    else:
        manifest['dependencies'] = {'\ud800': '1'}
    result = inventory({'package.json': json.dumps(manifest)})
    assert result['files'][0]['status'] == 'unparsable'
    assert result['components'] == result['edges'] == []
    _size(result)


def test_job_inline_shell_default_inherits_workflow_directory():
    steps = workflow('defaults:\n  run:\n    working-directory: web\njobs:\n  test:\n'
                     '    defaults:\n      run: {shell: bash}\n    steps:\n      - run: npm test\n')
    assert steps[0]['working_directory'] == 'web'


def test_unsupported_merged_defaults_have_unknown_directory():
    result = inventory({'package.json': '{"name":"app"}', '.github/workflows/ci.yml':
        'defaults:\n  run:\n    <<: *common\njobs:\n  test:\n    steps:\n      - run: npm test\n'})
    assert not result['verification_hints']
    assert 'workflow_directory_unknown' in result['parser_reasons']


@pytest.mark.parametrize('excluded', [False, True])
def test_cargo_namesake_outside_workspace_never_borrows_its_lock(excluded):
    result = inventory({'Cargo.toml': '[workspace]\nmembers=["crates/*"]\n' + ('exclude=["examples/app"]\n' if excluded else ''),
        'crates/app/Cargo.toml': '[package]\nname="app"\nversion="1"\n[dependencies]\nlib="1"',
        'examples/app/Cargo.toml': '[package]\nname="app"\nversion="1"\n[dependencies]\nlib="1"',
        'Cargo.lock': '[[package]]\nname="app"\nversion="1"\n[[package]]\nname="lib"\nversion="1"'})
    assert edges(result, 'cargo', 'lib', 'crates/app/Cargo.toml')[0]['evidence_level'] == 'verified'
    assert edges(result, 'cargo', 'lib', 'examples/app/Cargo.toml')[0]['resolution_reason'] == 'lockfile_does_not_cover_component'


def test_cargo_path_lock_proof_requires_unique_manifest_and_lock_identity():
    base = {'lib/Cargo.toml': '[package]\nname="app"\nversion="1"\n[dependencies]\nlib="1"',
        'Cargo.lock': '[[package]]\nname="app"\nversion="1"\n[[package]]\nname="lib"\nversion="1"'}
    assert edges(inventory(base), 'cargo', 'lib')[0]['evidence_level'] == 'verified'
    duplicate = {**base, 'other/Cargo.toml': '[package]\nname="app"\nversion="1"'}
    assert edges(inventory(duplicate), 'cargo', 'lib')[0]['evidence_level'] == 'declared'
    duplicate_lock = {**base, 'Cargo.lock': base['Cargo.lock'] + '\n[[package]]\nname="app"\nversion="1"'}
    assert edges(inventory(duplicate_lock), 'cargo', 'lib')[0]['evidence_level'] == 'declared'


def test_unavailable_inherited_publish_is_unknown_and_not_publishable():
    result = inventory({'lib/Cargo.toml': '[package]\nname="app"\npublish.workspace=true'})
    component = result['components'][0]
    assert component['published_name'] is None
    assert component['published_name_reason'] == 'workspace_inheritance_unavailable'
    assert 'cargo_workspace_inheritance_unavailable' in result['parser_reasons']


@pytest.mark.parametrize('command', ['npm ci&&cd web&&npm test', 'npm ci;cd web;npm test'])
def test_compact_shell_separators_preserve_step_directory_tracking(command):
    result = inventory({'package.json': '{"name":"root"}', 'web/package.json': '{"name":"web"}',
        '.github/workflows/ci.yml': 'jobs:\n  t:\n    steps:\n      - run: ' + command + '\n'})
    assert not hints(result, 'package.json')
    assert {h['relation'] for h in hints(result, 'web/package.json')} == {'test'}


@pytest.mark.parametrize('command', ["echo '&&' vitest", "echo ';' vitest", 'echo "|" vitest'])
def test_quoted_shell_separators_do_not_create_tool_invocations(command):
    result = inventory({'package.json': '{"name":"app"}',
        '.github/workflows/ci.yml': 'jobs:\n  t:\n    steps:\n      - run: ' + command + '\n'})
    assert not result['verification_hints']
