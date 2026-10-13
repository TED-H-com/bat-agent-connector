"""Conservative, bounded manifest observations. No resolver or repository code runs."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import posixpath
import re
import shlex
import sys

from .pm_dependency_budget import MAX_COMMAND_BYTES
from .pm_dependency_paths import MAX_EDGES, safe_path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib

KINDS = ("runtime_library", "api_schema", "build_test_toolchain", "external_service")
RELATIONS = ("runtime", "build", "test", "contract", "reference")
BINARIES = {"typescript": ["tsc"], "vite": ["vite"], "vitest": ["vitest"], "@playwright/test": ["playwright"],
            "eslint": ["eslint"], "pytest": ["pytest"], "ruff": ["ruff"], "mypy": ["mypy"], "tox": ["tox"]}


def stable(prefix, *parts):
    return prefix + hashlib.sha256(json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()[:32]


def package_name(ecosystem, name):
    return re.sub(r"[-_.]+", "-", name).lower() if ecosystem == "python" else name


def dependency_key(ecosystem, name):
    return stable("pmdepkey_", ecosystem, package_name(ecosystem, name))


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError("unsupported_value")
    value.encode("utf-8")
    return value


def requirement(value):
    value = text(value)
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9,._-]+\])?\s*(.*)", value)
    if not match:
        raise ValueError("unsupported_requirement")
    return match[1], match[2] or "*"


def _commands(command):
    """Literal command words only; quoted mentions and shell substitutions aren't calls."""
    if "\n" in command:
        return [words for line in command.splitlines() for words in _commands(line)]
    try:
        words = shlex.split(command, comments=True)
    except ValueError:
        return []
    groups, current = [], []
    for word in words:
        if word in {"&&", ";", "||", "|"}:
            if current:
                groups.append(current)
            current = []
        else:
            current.append(word)
    if current:
        groups.append(current)
    return groups


def unwrapped(words):
    while words[:2] in (["uv", "run"], ["poetry", "run"], ["pnpm", "exec"], ["python", "-m"], ["python3", "-m"]):
        words = words[2:]
    if words and words[0] == "npx":
        words = words[1:]
    return words


def binaries(command):
    found = set()
    for words in _commands(command):
        words = unwrapped(words)
        if words and not words[0].startswith(("$", "-")):
            found.add(words[0])
    return found


def _scalar(value):
    value = value.strip()
    if value.startswith(("'", '"')) and value[-1:] == value[:1]:
        value = value[1:-1]
    return value


def workflow(content):
    """Literal YAML subset; indentation confines defaults and step fields."""
    rows, job, step, block = [], None, None, None
    default_dir, job_dir, stack = ".", ".", []
    seen_jobs = False
    for number, line in enumerate(content.splitlines(), 1):
        if len(line) > 8192 or "\t" in line:
            raise ValueError("unsupported_workflow_yaml")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if block is not None and indent > block:
            step["command"] += "\n" + line.strip()
            continue
        block = None
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parents = [key for _, key in stack]
        stripped = line.strip()
        if stripped == "jobs:" and not parents:
            seen_jobs, job, step = True, None, None
        elif parents == ["jobs"] and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*:", stripped):
            job, step, job_dir = stripped[:-1], None, default_dir
        if stripped.startswith("working-directory:"):
            directory = _scalar(stripped.partition(":")[2])
            if parents == ["defaults", "run"]:
                default_dir = directory
            elif parents == ["jobs", job, "defaults", "run"]:
                job_dir = directory
        if parents == ["jobs", job, "steps"] and stripped.startswith("- "):
            step = {"job_id": job, "line": number, "command": "", "uses": None,
                    "working_directory": job_dir, "name": None}
            rows.append(step)
            stack.append((indent, "step"))
            stripped = stripped[2:].strip()
            key_indent = indent + 2
        elif parents == ["jobs", job, "steps", "step"]:
            key_indent = indent
        else:
            if stripped.endswith(":"):
                stack.append((indent, stripped[:-1]))
            continue
        match = re.fullmatch(r"(run|uses|working-directory|name):\s*(.*)", stripped)
        if not match:
            if stripped.endswith(":"):
                stack.append((key_indent, stripped[:-1]))
            continue
        key, value = match.groups()
        if key == "run":
            step["line"] = number
            if value in {"|", "|-", "|+", ">", ">-", ">+"}:
                block = key_indent
            else:
                step["command"] = _scalar(value)
        else:
            step[{"working-directory": "working_directory"}.get(key, key)] = _scalar(value)
    if not seen_jobs:
        raise ValueError("unsupported_workflow_yaml")
    return rows


def _nearest(path, name, parsed):
    folder = posixpath.dirname(path)
    while True:
        candidate = posixpath.join(folder, name)
        if candidate in parsed:
            return candidate, parsed[candidate]
        if not folder:
            return None, None
        folder = posixpath.dirname(folder)


def member_path(relative, patterns):
    def matches(parts, pattern):
        if not pattern:
            return not parts
        if pattern[0] == "**":
            return matches(parts, pattern[1:]) or bool(parts) and matches(parts[1:], pattern)
        return bool(parts) and fnmatch.fnmatchcase(parts[0], pattern[0]) and matches(parts[1:], pattern[1:])
    return isinstance(patterns, list) and any(isinstance(p, str) and len(p) <= 800 and len(p.split("/")) <= 16
        and matches(relative.split("/"), p.rstrip("/").split("/")) for p in patterns)


def _go(content, sums=False):
    if sums:
        rows = []
        for line in content.splitlines():
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) != 3 or not fields[2].startswith("h1:"):
                raise ValueError("unsupported_go_sum")
            rows.append((fields[0], fields[1].removesuffix("/go.mod")))
        return rows
    name, dependencies, toolchain, grouped = None, [], [], False
    for line in content.splitlines():
        line = line.partition("//")[0].strip()
        if line.startswith("module "):
            name = text(line[7:].strip().strip('"'))
        elif line.startswith("go ") or line.startswith("toolchain "):
            toolchain.append(tuple(line.split(maxsplit=1)))
        elif line == "require (":
            grouped = True
        elif grouped and line == ")":
            grouped = False
        elif line.startswith("require ") or grouped and line:
            fields = (line[8:] if line.startswith("require ") else line).split()
            if len(fields) != 2:
                raise ValueError("unsupported_go_require")
            dependencies.append((text(fields[0]), text(fields[1])))
    if not name:
        raise ValueError("missing_module_name")
    return {"name": name, "dependencies": dependencies, "toolchain": toolchain}


class Inventory:
    def __init__(self, repository, commit, files, contents):
        self.repository, self.commit, self.files = repository, commit, files
        self.contents, self.parsed = contents, {}
        self.components, self.dependencies, self.edges, self.hints = [], {}, [], []
        self.reasons = set()

    def ref(self, path, **extra):
        row = next(row for row in self.files if row["path"] == path)
        if "command" in extra:
            raw = extra["command"].encode("utf-8")
            if len(raw) > MAX_COMMAND_BYTES:
                extra.update(command=raw[:MAX_COMMAND_BYTES].decode("utf-8", errors="ignore"),
                             command_truncated=True, command_sha256=hashlib.sha256(raw).hexdigest())
                self.reasons.add("evidence_command_truncated")
        return {"repository": self.repository, "path": path, "commit_sha": self.commit, "blob_sha": row["blob_sha"], **extra}

    def parse(self):
        for row in self.files:
            path, content = row["path"], self.contents[row["path"]]
            if content is None:
                continue
            name = posixpath.basename(path)
            try:
                if name.endswith(".toml") or name in {"uv.lock", "Cargo.lock"} or name == "rust-toolchain" and content.lstrip().startswith("["):
                    value = tomllib.loads(content)
                elif name.endswith(".json"):
                    value = json.loads(content)
                    if not isinstance(value, dict):
                        raise ValueError("expected_object")
                elif path.startswith(".github/workflows/"):
                    value = workflow(content)
                elif name in {"go.mod", "go.sum"}:
                    value = _go(content, name == "go.sum")
                elif name.startswith("requirements"):
                    value = []
                    for line in content.splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            value.append(requirement(line.split(" #", 1)[0]))
                else:
                    value = content
                self.parsed[path] = value
            except (ValueError, TypeError, RecursionError):
                row.update(status="unparsable", reason="unsupported_or_invalid_format")
        self.catalog = {path: self.parsed.get(path) for path in self.contents}
        for path, value in list(self.parsed.items()):
            name = posixpath.basename(path)
            ecosystem = {"pyproject.toml": "python", "package.json": "npm", "Cargo.toml": "cargo", "go.mod": "go"}.get(name)
            if not ecosystem:
                continue
            try:
                package = value.get("project", {}) if ecosystem == "python" else value.get("package", {}) if ecosystem == "cargo" else value
                declared = package.get("name")
                unnamed = ecosystem == "npm" and declared is None
                if unnamed:
                    declared = path
                    self.reasons.add("npm_package_name_not_declared")
                if declared is None:
                    if ecosystem in {"python", "cargo"} and ("workspace" in value or "tool" in value or "build-system" in value):
                        self.reasons.add("manifest_without_package")
                        continue
                    raise ValueError
                declared = text(declared)
                component = {"component_id": stable("pmcomp_", self.repository, path, declared), "name": declared,
                    "ecosystem": ecosystem, "manifest_path": path, "repository": self.repository, "commit_sha": self.commit,
                    "name_reason": "package_name_not_declared" if unnamed else None,
                    "published_name": None if unnamed or value.get("private") is True or package.get("publish") is False or package.get("publish") == [] else declared,
                    "version": package.get("version") if isinstance(package.get("version"), str) else None,
                    "version_reason": None if isinstance(package.get("version"), str) else "not_declared",
                    "evidence_refs": [self.ref(path)], "maintenance_owner": None,
                    "maintenance_owner_reason": "not_named_in_visible_policy", "verification_hints": []}
                self.components.append(component)
                if ecosystem == "cargo" and isinstance(package.get("version"), dict):
                    version, refs, reason = self.cargo_inherit(component, "package", "version", package["version"])
                    component.update(version=version, version_reason=reason)
                    component["evidence_refs"].extend(refs)
                self.component(component, value)
            except (ValueError, TypeError, KeyError, AttributeError):
                row = next(r for r in self.files if r["path"] == path)
                row.update(status="unparsable", reason="unsupported_manifest_structure")
                # Never publish a prefix of a malformed manifest as its whole inventory.
                ids = {c["component_id"] for c in self.components if c["manifest_path"] == path}
                self.components = [c for c in self.components if c["manifest_path"] != path]
                self.edges = [e for e in self.edges if e["component_id"] not in ids]
        self.toolchains()
        self.ci()
        merged = {}
        for edge in self.edges:
            key = (edge["component_id"], edge["dependency_key"], edge["relation"], edge["version"])
            if key not in merged:
                merged[key] = {**edge, "edge_id": stable("pmde_", *key), "bases": [edge["basis"]]}
                continue
            saved = merged[key]
            if edge["basis"] not in saved["bases"]:
                saved["bases"].append(edge["basis"])
            for ref in edge["evidence_refs"]:
                if ref not in saved["evidence_refs"]:
                    saved["evidence_refs"].append(ref)
        self.edges = list(merged.values())
        invalid_files = {r["path"] for r in self.files if r["status"] != "read"}
        for edge in self.edges:
            if any(ref["path"] in invalid_files for ref in edge["evidence_refs"]):
                edge.update(resolved=None, evidence_level="declared", resolution_reason="lockfile_unparsable")
        used = {e["dependency_key"] for e in self.edges}
        for key in used:
            self.dependencies[key]["kinds"] = sorted({e["kind"] for e in self.edges if e["dependency_key"] == key})
        return {"components": self.components, "dependencies": [d for k, d in sorted(self.dependencies.items()) if k in used],
                "edges": self.edges, "verification_hints": self.hints, "parser_reasons": sorted(self.reasons)}

    def cargo_workspace(self, component):
        path, root = _nearest(component["manifest_path"], "Cargo.toml", {
            p: v for p, v in self.parsed.items() if posixpath.basename(p) == "Cargo.toml" and isinstance(v, dict) and "workspace" in v})
        if path is None:
            return None, None
        relative = posixpath.relpath(posixpath.dirname(component["manifest_path"]) or ".", posixpath.dirname(path) or ".")
        workspace = root["workspace"]
        if relative == "." or (member_path(relative, workspace.get("members", [])) and not member_path(relative, workspace.get("exclude", []))):
            return path, workspace
        return None, None

    def cargo_inherit(self, component, section, name, value):
        if not isinstance(value, dict) or value.get("workspace") is not True:
            return value, [], None
        path, workspace = self.cargo_workspace(component)
        inherited = workspace.get(section, {}).get(name) if workspace else None
        if inherited is None:
            self.reasons.add("cargo_workspace_inheritance_unavailable")
            return None, [], "workspace_inheritance_unavailable"
        return inherited, [self.ref(path, workspace_field=section + "." + name)], None

    def lock_coverage(self, component, path, lock):
        eco = component["ecosystem"]
        relative = posixpath.relpath(posixpath.dirname(component["manifest_path"]) or ".", posixpath.dirname(path) or ".")
        if relative == ".":
            return True, []
        if eco == "npm":
            return isinstance(lock.get("packages", {}).get(relative), dict), []
        if eco == "cargo":
            root, _ = self.cargo_workspace(component)
            if root and posixpath.dirname(root) == posixpath.dirname(path):
                return True, [self.ref(root, basis="workspace_membership")]
            return any(isinstance(component["version"], str) and p.get("name") == component["name"] and p.get("version") == component["version"] and "source" not in p
                       for p in lock.get("package", [])), []
        if eco == "python":
            root = posixpath.join(posixpath.dirname(path), "pyproject.toml")
            workspace = self.parsed.get(root, {}).get("tool", {}).get("uv", {}).get("workspace", {})
            if member_path(relative, workspace.get("members", [])) and not member_path(relative, workspace.get("exclude", [])):
                return True, [self.ref(root, basis="workspace_membership")]
            name = package_name(eco, component["name"])
            if name in {package_name(eco, n) for n in lock.get("manifest", {}).get("members", [])}:
                return True, []
            for entry in lock.get("package", []):
                if package_name(eco, entry.get("name", "")) == name and any(
                        entry.get("source", {}).get(kind) == relative for kind in ("editable", "virtual")):
                    return True, []
        return False, []

    def resolution(self, component, name):
        eco, manifest = component["ecosystem"], component["manifest_path"]
        lockname = {"python": "uv.lock", "npm": "package-lock.json", "cargo": "Cargo.lock", "go": "go.sum"}[eco]
        path, lock = _nearest(manifest, lockname, self.catalog)
        if lock is None:
            return None, None, [], "no_unique_lock_resolution"
        try:
            covered, membership_refs = self.lock_coverage(component, path, lock)
            if not covered:
                return None, None, [], "lockfile_does_not_cover_component"
            if eco == "npm":
                relative = posixpath.relpath(posixpath.dirname(manifest) or ".", posixpath.dirname(path) or ".")
                prefixes = [relative] if relative != "." else [""]
                while prefixes[-1]:
                    prefixes.append(posixpath.dirname(prefixes[-1]))
                entries = lock.get("packages", {})
                entry = next((entries[posixpath.join(p, "node_modules", name)] for p in prefixes
                              if posixpath.join(p, "node_modules", name) in entries), None)
                if entry is None and lock.get("lockfileVersion") == 1 and manifest == posixpath.join(posixpath.dirname(path), "package.json"):
                    entry = lock.get("dependencies", {}).get(name)
                if not isinstance(entry, dict) or entry.get("link"):
                    return None, None, [], "no_unique_lock_resolution"
                version = text(entry["version"])
                bins = list(entry.get("bin", {})) if isinstance(entry.get("bin"), dict) else []
            elif eco == "go":
                # go.sum is a checksum catalog, not a resolver's lockfile.
                return None, None, [], "go_sum_not_lockfile"
            else:
                versions = {text(p["version"]) for p in lock.get("package", []) if package_name(eco, p.get("name", "")) == package_name(eco, name)}
                if len(versions) != 1:
                    return None, None, [], "no_unique_lock_resolution"
                version, bins = next(iter(versions)), []
            return version, [self.ref(path, package=name), *membership_refs], bins, None
        except (TypeError, KeyError, AttributeError, ValueError):
            row = next(r for r in self.files if r["path"] == path)
            row.update(status="unparsable", reason="unsupported_lock_structure")
            return None, None, [], "lockfile_unparsable"

    def add(self, component, name, version, relation, *, basis, refs=None, kind=None, ecosystem=None, resolved=True, version_reason=None):
        name = text(name)
        if version is not None:
            version = text(version)
        if len(self.edges) >= MAX_EDGES:
            self.reasons.add("edge_limit")
            return
        eco = ecosystem or component["ecosystem"]
        kind = kind or ("runtime_library" if relation == "runtime" else "build_test_toolchain")
        key = dependency_key(eco, name)
        dep = self.dependencies.setdefault(key, {"key": key, "name": name, "ecosystem": eco, "kinds": []})
        if kind not in dep["kinds"]:
            dep["kinds"].append(kind)
        refs = refs or [self.ref(component["manifest_path"])]
        version_resolved, lock_ref, _, reason = self.resolution(component, name) if resolved else (None, None, [], "go_sum_not_lockfile" if eco == "go" else "not_lockfile_dependency")
        if lock_ref:
            refs = [*refs, *lock_ref]
        self.edges.append({"edge_id": stable("pmde_", component["component_id"], key, relation, basis, refs),
            "component_id": component["component_id"], "dependency_key": key, "direction": "component_to_dependency",
            "relation": relation, "kind": kind, "version": version, "resolved": version_resolved,
            "version_reason": version_reason or ("not_declared" if version is None else None),
            "resolution_reason": reason,
            "evidence_level": "verified" if lock_ref else "declared", "basis": basis, "evidence_refs": refs})

    def dev(self, component, name, version, basis, scripts=None, group=None):
        _, _, bins, _ = self.resolution(component, name)
        bins = bins or BINARIES.get(package_name(component["ecosystem"], name), [])
        manifest_ref = self.ref(component["manifest_path"], **({"declaration_group": group} if group else {}))
        evidence = []
        for script, command in (scripts or {}).items():
            if not isinstance(command, str):
                raise ValueError
            relation = "test" if re.match(r"^(?:test|check)(?::|$)", script) else "build" if re.match(r"^(?:build|prepare|prepack)(?::|$)", script) else None
            if relation and set(bins) & binaries(command):
                evidence.append((relation, self.ref(component["manifest_path"], script=script, command=command)))
        for path, step in self.steps(component):
            for words in _commands(step["command"]):
                command = shlex.join(words)
                if set(bins) & binaries(command):
                    for relation in self.step_relations(component, {"command": command}):
                        evidence.append((relation, self.ref(path, job_id=step["job_id"], line=step["line"], command=command)))
        if evidence:
            for relation, ref in evidence:
                self.add(component, name, version, relation, basis=basis, refs=[manifest_ref, ref])
        else:
            self.add(component, name, version, "build", basis="dev_unclassified", refs=[manifest_ref])

    def component(self, c, value):
        eco = c["ecosystem"]
        if eco == "python":
            project = value["project"]
            dynamic = project.get("dynamic", [])
            if not isinstance(dynamic, list):
                raise ValueError("dynamic_list_required")
            c.update(runtime_dependencies_status="declared", runtime_dependencies_reason=None)
            if "dependencies" in dynamic:
                self.reasons.add("python_dynamic_dependencies")
                c.update(runtime_dependencies_status="unknown", runtime_dependencies_reason="dynamic_source_not_explicit")
                source = value.get("tool", {}).get("setuptools", {}).get("dynamic", {}).get("dependencies", {})
                paths = source.get("file", []) if isinstance(source, dict) else []
                paths = [paths] if isinstance(paths, str) else paths
                if isinstance(paths, list) and paths:
                    complete = True
                    for relative in paths:
                        if not safe_path(relative):
                            complete = False
                            continue
                        path = posixpath.join(posixpath.dirname(c["manifest_path"]), relative)
                        requirements = self.parsed.get(path)
                        if not posixpath.basename(path).startswith("requirements") or not isinstance(requirements, list):
                            complete = False
                            continue
                        for name, version in requirements:
                            self.add(c, name, version, "runtime", basis="setuptools.dynamic.dependencies",
                                     refs=[self.ref(c["manifest_path"]), self.ref(path)])
                    c.update(runtime_dependencies_status="declared_dynamic_files" if complete else "partial",
                             runtime_dependencies_reason=None if complete else "dynamic_source_unavailable")
            if not isinstance(project.get("dependencies", []), list) or not isinstance(value.get("build-system", {}).get("requires", []), list):
                raise ValueError("dependency_list_required")
            for raw in project.get("dependencies", []):
                name, version = requirement(raw)
                self.add(c, name, version, "runtime", basis="project.dependencies")
            for raw in value.get("build-system", {}).get("requires", []):
                name, version = requirement(raw)
                self.add(c, name, version, "build", basis="build-system.requires")
            if project.get("requires-python"):
                self.add(c, "python", project["requires-python"], "build", basis="requires-python", ecosystem="toolchain", resolved=False)
            if "optional-dependencies" in dynamic:
                self.reasons.add("python_dynamic_optional_dependencies")
            for identity, groups in (("extra", project.get("optional-dependencies", {})), ("group", value.get("dependency-groups", {}))):
                for group, values in groups.items():
                    if not isinstance(values, list):
                        raise ValueError("dependency_list_required")
                    for raw in values:
                        if isinstance(raw, dict) and set(raw) == {"include-group"}:
                            if raw["include-group"] not in groups:
                                self.reasons.add("python_dependency_group_unavailable")
                            continue
                        name, version = requirement(raw)
                        self.dev(c, name, version, "python_" + identity + ":" + group, group=identity + ":" + group)
            for path, values in self.parsed.items():
                if "dependencies" not in dynamic and posixpath.basename(path).startswith("requirements") and posixpath.dirname(path) == posixpath.dirname(c["manifest_path"]):
                    for name, version in values:
                        self.add(c, name, version, "build", basis="dev_unclassified", refs=[self.ref(path)])
        elif eco == "npm":
            for field, reason in (("peerDependencies", "npm_peer_dependencies_not_captured"), ("optionalDependencies", "npm_optional_dependencies_not_captured")):
                if value.get(field):
                    self.reasons.add(reason)
            for name, version in value.get("dependencies", {}).items():
                self.add(c, name, version, "runtime", basis="dependencies")
            for name, version in value.get("devDependencies", {}).items():
                self.dev(c, name, version, "npm_script_or_ci", value.get("scripts", {}))
            if value.get("engines", {}).get("node"):
                self.add(c, "node", value["engines"]["node"], "build", basis="engines.node", ecosystem="toolchain", resolved=False)
        elif eco == "cargo":
            sections = [(value, "")]
            sections.extend((section, "target:" + target + ":") for target, section in value.get("target", {}).items())
            for section, prefix in sections:
                for group, relation in (("dependencies", "runtime"), ("build-dependencies", "build"), ("dev-dependencies", "test")):
                    for alias, requirement_value in section.get(group, {}).items():
                        refs = [self.ref(c["manifest_path"])]
                        requirement_value, inherited_refs, reason = self.cargo_inherit(c, "dependencies", alias, requirement_value)
                        refs.extend(inherited_refs)
                        name = requirement_value.get("package", alias) if isinstance(requirement_value, dict) else alias
                        version = requirement_value.get("version") or requirement_value.get("git") or requirement_value.get("path") or "*" if isinstance(requirement_value, dict) else requirement_value
                        self.add(c, name, version, relation, basis=prefix + group, refs=refs, version_reason=reason)
            if value["package"].get("rust-version"):
                version, inherited_refs, reason = self.cargo_inherit(c, "package", "rust-version", value["package"]["rust-version"])
                self.add(c, "rust", version, "build", basis="rust-version", ecosystem="toolchain", resolved=False,
                         refs=[self.ref(c["manifest_path"]), *inherited_refs], version_reason=reason)
        elif eco == "go":
            for basis, version in value["toolchain"]:
                self.add(c, "go", version, "build", basis=basis, ecosystem="toolchain", resolved=False)
            for name, version in value["dependencies"]:
                self.add(c, name, version, "runtime", basis="go.require", resolved=False)

    def steps(self, component):
        directory = posixpath.dirname(component["manifest_path"]) or "."
        for path, steps in self.parsed.items():
            if not path.startswith(".github/workflows/"):
                continue
            for step in steps:
                cwd = step["working_directory"]
                commands = _commands(step["command"])
                if commands and len(commands[0]) == 2 and commands[0][0] == "cd":
                    cwd = posixpath.join(cwd, commands[0][1])
                if "$" not in cwd and posixpath.normpath(cwd) == directory:
                    yield path, step

    def step_relations(self, component, step):
        relations = set()
        eco = component["ecosystem"]
        for raw in _commands(step["command"]):
            words = unwrapped(raw)
            if not words:
                continue
            if eco == "python":
                if words[:2] == ["uv", "build"] or words[0] == "build":
                    relations.add("build")
                if words[0] in {"pytest", "tox", "ruff", "mypy"}:
                    relations.add("test")
            elif eco in {"cargo", "go"}:
                if words[:2] == [eco, "build"]:
                    relations.add("build")
                if len(words) > 1 and words[0] == eco and words[1] in {"test", "check"}:
                    relations.add("test")
            elif eco == "npm":
                if words[:2] in (["npm", "test"], ["pnpm", "test"]) or words[0] in {"vitest", "playwright"}:
                    relations.add("test")
                if words[0] in {"vite", "tsc"}:
                    relations.add("build")
                if words[:2] in (["npm", "run"], ["pnpm", "run"]) and len(words) >= 3:
                    if re.match(r"^(?:test|check)(?::|$)", words[2]):
                        relations.add("test")
                    if re.match(r"^(?:build|prepare|prepack)(?::|$)", words[2]):
                        relations.add("build")
        return sorted(relations)

    def ci(self):
        for c in self.components:
            for path, step in self.steps(c):
                for relation in self.step_relations(c, step):
                    command = step["command"]
                    ref = self.ref(path, job_id=step["job_id"], line=step["line"], command=command)
                    hint = {"hint_id": stable("pmhint_", c["component_id"], relation, ref),
                            "component_id": c["component_id"], "relation": relation, **ref}
                    c["verification_hints"].append(hint)
                    self.hints.append(hint)
                    for raw in _commands(command):
                        if relation in self.step_relations(c, {"command": shlex.join(raw)}):
                            self.add(c, unwrapped(raw)[0], None, relation, basis="workflow_step", refs=[ref], ecosystem="toolchain", resolved=False)
                    # Sharing a CI job is not evidence of a component dependency on an action.

    def toolchains(self):
        for c in self.components:
            eco = c["ecosystem"]
            names = {"python": [".python-version", ".tool-versions"], "npm": [".nvmrc", ".tool-versions"], "cargo": ["rust-toolchain", "rust-toolchain.toml", ".tool-versions"], "go": [".tool-versions"]}[eco]
            for name in names:
                path, value = _nearest(c["manifest_path"], name, self.catalog)
                if path is None or value is None:
                    continue
                try:
                    tool = {"python": "python", "npm": "node", "cargo": "rust", "go": "go"}[eco]
                    if name == ".tool-versions":
                        for line in value.splitlines():
                            fields = line.split()
                            if fields and fields[0] in {tool, "nodejs" if tool == "node" else tool} and len(fields) >= 2:
                                self.add(c, tool, " ".join(fields[1:]), "build", basis=name, refs=[self.ref(path)], ecosystem="toolchain", resolved=False)
                        continue
                    version = value["toolchain"]["channel"] if isinstance(value, dict) else value.strip()
                    self.add(c, tool, version, "build", basis=name, refs=[self.ref(path)], ecosystem="toolchain", resolved=False)
                except (KeyError, TypeError, ValueError, AttributeError):
                    row = next(r for r in self.files if r["path"] == path)
                    row.update(status="unparsable", reason="unsupported_toolchain")
