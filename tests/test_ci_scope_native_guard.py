"""Keep native build steps and their import closure independent of allowlisted TS."""

from __future__ import annotations

import ast
import glob
import json
import re
from pathlib import Path

import pytest
import yaml

from scripts.desktop_fixture_inputs import workflow_steps

ROOT = Path(__file__).resolve().parents[1]
REMOVE_ALLOWLIST = "Remove desktop/tests/**/*.ts from the native allowlist."
SCRIPT_PATH = re.compile(r'''[^\s"'`;|&()<>]+\.(?:mjs|cjs|js|py|ts|swift|ps1)\b''')
NPM_RUN = re.compile(r"\bnpm\s+run\s+([\w:-]+)")
JS_IMPORT = re.compile(r'''\b(?:import|export)\s+(?:[^;]*?\s+from\s+)?["'](\.[^"']+)["']''')
JS_DYNAMIC = re.compile(r"\b(?:import|require)\s*\(\s*")
JS_LITERAL_ARGUMENT = re.compile(r'''(["'`])([^"'`]+)\1\s*\)''')


def build_scripts(root: Path) -> set[Path]:
    job = yaml.safe_load((root / ".github/workflows/desktop.yml").read_text())["jobs"]["build"]
    tests = (root / "desktop/tests").resolve()
    scripts: set[Path] = set()
    npm_seen: set[tuple[Path, str]] = set()

    def collect(command: str, cwd: Path) -> None:
        for match in SCRIPT_PATH.finditer(command):
            path = (cwd / match.group()).resolve()
            if not path.is_relative_to(tests):
                continue
            assert path.suffix != ".ts", f"{REMOVE_ALLOWLIST} Direct step: {match.group()}"
            matches = [Path(value) for value in glob.glob(str(path), recursive=True)]
            assert matches, f"Cannot trace native build script: {match.group()}"
            scripts.update(matches)
        for script in NPM_RUN.findall(command):
            key = (cwd, script)
            if key not in npm_seen:
                npm_seen.add(key)
                package = json.loads((cwd / "package.json").read_text())
                collect(package["scripts"][script], cwd)

    for step, cwd, _ in workflow_steps(root, job):
        if "run" in step:
            collect(step["run"], cwd)
    return scripts


def resolve_import(path: Path) -> Path:
    # Explicit extensions are normal ESM usage; also catch extensionless require.
    assert path.suffix != ".ts", f"{REMOVE_ALLOWLIST} Imported file: {path.name}"
    candidates = [path] if path.suffix else [
        path, *(path.with_suffix(suffix) for suffix in (".mjs", ".js", ".cjs", ".json", ".py", ".ts")),
        *(path / ("index" + suffix) for suffix in (".mjs", ".js", ".cjs", ".ts")),
    ]
    resolved = next((candidate for candidate in candidates if candidate.is_file()), None)
    assert resolved is not None, f"Cannot trace relative native import: {path.name}"
    assert resolved.suffix != ".ts", f"{REMOVE_ALLOWLIST} Imported file: {resolved.name}"
    return resolved


def relative_imports(script: Path) -> set[Path]:
    source = script.read_text(encoding="utf-8")
    imports = set()
    if script.suffix in {".mjs", ".js", ".cjs"}:
        specifiers = JS_IMPORT.findall(source)
        for call in JS_DYNAMIC.finditer(source):
            argument = JS_LITERAL_ARGUMENT.match(source, call.end())
            # Computed imports cannot prove independence from allowlisted TS.
            assert argument and "${" not in argument[2], (
                f"Cannot trace computed native import in {script.name}. {REMOVE_ALLOWLIST}"
            )
            if argument[2].startswith("."):
                specifiers.append(argument[2])
        for specifier in specifiers:
            specifier = specifier.split("?", 1)[0].split("#", 1)[0]
            imports.add(resolve_import((script.parent / specifier).resolve()))
    elif script.suffix == ".py":
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.level:
                directory = script.parent
                for _ in range(node.level - 1):
                    directory = directory.parent
                modules = [node.module] if node.module else [alias.name for alias in node.names]
                for module in modules:
                    imports.add(resolve_import(directory.joinpath(*module.split("."))))
    return imports


def native_dependency_closure(root: Path) -> set[Path]:
    pending = list(build_scripts(root))
    visited: set[Path] = set()
    while pending:
        script = pending.pop()
        assert script.suffix != ".ts", f"{REMOVE_ALLOWLIST} Script: {script.name}"
        if script not in visited:
            visited.add(script)
            pending.extend(relative_imports(script) - visited)
    return visited


def test_native_build_steps_and_transitive_imports_do_not_use_typescript():
    visited = native_dependency_closure(ROOT)
    # Prevent a broken entrypoint parser from making the guard vacuously pass.
    assert {
        "macos-keychain.mjs", "macos-installed-smoke.mjs", "windows-installed-smoke.mjs",
        "native-smoke.mjs", "managed-setup-browser-integration.mjs", "windows-central-fixture.py",
        "managed-runtime-fixture.py", "managed-upgrade-fixture.py", "native-files-integration.mjs",
        "macos-bundle.mjs", "managed-installed-probe.mjs", "windows-bundle.mjs",
    } <= {path.name for path in visited}


def fixture_repository(tmp_path, command, *, root_cwd=False):
    workflow = tmp_path / ".github/workflows/desktop.yml"
    workflow.parent.mkdir(parents=True)
    tests = tmp_path / "desktop/tests"
    tests.mkdir(parents=True)
    step = {"run": command}
    if root_cwd:
        step["working-directory"] = "."
    workflow.write_text(yaml.safe_dump({"jobs": {"build": {
        "defaults": {"run": {"working-directory": "desktop"}}, "steps": [step],
    }}}))
    (tmp_path / "desktop/package.json").write_text(json.dumps({"scripts": {
        "test:native": "npm run test:helper", "test:helper": "node tests/helper.ts",
    }}))
    (tests / "helper.ts").write_text("export const helper = 1;\n")
    return tests


@pytest.mark.parametrize("command,root_cwd", [
    ("node tests/helper.ts", False),
    ('node "desktop/tests/helper.ts"', True),
    ("npm run test:native", False),
])
def test_guard_rejects_direct_typescript_steps_and_npm_scripts(tmp_path, command, root_cwd):
    fixture_repository(tmp_path, command, root_cwd=root_cwd)
    with pytest.raises(AssertionError, match=re.escape(REMOVE_ALLOWLIST)):
        native_dependency_closure(tmp_path)


def test_guard_rejects_typescript_in_local_composite_action(tmp_path):
    fixture_repository(tmp_path, 'echo setup')
    workflow = tmp_path / '.github/workflows/desktop.yml'
    document = yaml.safe_load(workflow.read_text())
    document['jobs']['build']['steps'] = [{'uses': './.github/actions/native'}]
    workflow.write_text(yaml.safe_dump(document))
    action = tmp_path / '.github/actions/native/action.yml'
    action.parent.mkdir(parents=True)
    action.write_text(yaml.safe_dump({'runs': {'using': 'composite', 'steps': [
        {'shell': 'bash', 'run': 'node desktop/tests/helper.ts'},
    ]}}))
    with pytest.raises(AssertionError, match=re.escape(REMOVE_ALLOWLIST)):
        native_dependency_closure(tmp_path)


@pytest.mark.parametrize("import_statement", [
    'import {helper} from "./helper.ts";',
    'import "./helper.ts";',
    'export {helper} from "./helper.ts";',
    'await import("./helper.ts");',
    'await import(`./helper.ts`);',
    'require("./helper.ts");',
    'require(`./helper.ts`);',
    'require("./helper");',
])
def test_guard_rejects_typescript_through_transitive_imports(tmp_path, import_statement):
    tests = fixture_repository(tmp_path, "node tests/entry.mjs")
    (tests / "entry.mjs").write_text('import "./middle.mjs";\n')
    (tests / "middle.mjs").write_text(import_statement + "\n")
    with pytest.raises(AssertionError, match=re.escape(REMOVE_ALLOWLIST)):
        native_dependency_closure(tmp_path)


@pytest.mark.parametrize("import_statement", [
    'await import(`./${name}.ts`);',
    'require("./helper" + extension);',
    'await import(modulePath);',
])
def test_guard_fails_closed_for_untraceable_dynamic_imports(tmp_path, import_statement):
    tests = fixture_repository(tmp_path, "node tests/entry.mjs")
    (tests / "entry.mjs").write_text(import_statement + "\n")
    with pytest.raises(AssertionError, match=re.escape(REMOVE_ALLOWLIST)):
        native_dependency_closure(tmp_path)


def test_guard_traces_relative_python_imports_and_terminates_cycles(tmp_path):
    tests = fixture_repository(tmp_path, "python desktop/tests/entry.py", root_cwd=True)
    (tests / "entry.py").write_text("from . import middle\n")
    (tests / "middle.py").write_text("from .entry import example\n")
    assert {path.name for path in native_dependency_closure(tmp_path)} == {"entry.py", "middle.py"}
