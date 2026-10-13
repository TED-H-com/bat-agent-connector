"""Conservative, bounded manifest observations. No resolver or repository code runs."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shlex
import sys

from .pm_dependency_paths import MAX_EDGES

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


def dependency_key(ecosystem, name):
    return stable("pmdepkey_", ecosystem, name)


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


def binaries(command):
    found = set()
    for words in _commands(command):
        if words[:2] == ["uv", "run"]:
            words = words[2:]
        if words[:2] in (["python", "-m"], ["python3", "-m"]):
            words = words[2:]
        elif words and words[0] == "npx":
            words = words[1:]
        if words and not words[0].startswith(("$", "-")):
            found.add(words[0])
    return found


def _scalar(value):
    value = value.strip()
    if value.startswith(("'", '"')) and value[-1:] == value[:1]:
        value = value[1:-1]
    return value


def workflow(content):
    """Small YAML line reader: jobs, literal run/uses and working-directory only.

    Anchors, aliases, expressions and flow mappings cannot authorize associations.
    Line numbers remain the original 1-based source positions.
    """
    rows, job, step, block, job_indent, in_jobs = [], None, None, None, None, False
    default_dir, job_dir, defaults_indent = ".", ".", None
    for number, line in enumerate(content.splitlines(), 1):
        if len(line) > 8192 or "\t" in line:
            raise ValueError("unsupported_workflow_yaml")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if block and indent > block[0]:
            step["command"] += "\n" + line.strip()
            continue
        block = None
        stripped = line.strip()
        if stripped == "jobs:" and indent == 0:
            in_jobs = True
            continue
        if in_jobs and indent == 0:
            in_jobs = False
        if in_jobs and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*:", stripped) and (job_indent is None or indent == job_indent):
            job, job_indent, step, job_dir = stripped[:-1], indent, None, default_dir
            continue
        if stripped == "defaults:":
            defaults_indent = indent
        if defaults_indent is not None and indent <= defaults_indent and stripped != "defaults:":
            defaults_indent = None
        if defaults_indent is not None and stripped.startswith("working-directory:"):
            directory = _scalar(stripped.partition(":")[2])
            if job:
                job_dir = directory
            else:
                default_dir = directory
        if not in_jobs or not job:
            continue
        key_indent = indent + 2 if stripped.startswith("- ") else indent
        if stripped.startswith("- "):
            step = {"job_id": job, "line": number, "command": "", "uses": None,
                    "working_directory": job_dir, "name": None}
            rows.append(step)
            stripped = stripped[2:].strip()
        if step is None:
            continue
        match = re.match(r"(run|uses|working-directory|name):\s*(.*)$", stripped)
        if not match:
            continue
        key, value = match.groups()
        if key == "run":
            step["line"] = number
            if value in {"|", "|-", "|+", ">", ">-", ">+"}:
                block = (key_indent,)
            else:
                step["command"] = _scalar(value)
        else:
            step[{"working-directory": "working_directory"}.get(key, key)] = _scalar(value)
    if not in_jobs and not rows:
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
        for path, value in list(self.parsed.items()):
            name = posixpath.basename(path)
            ecosystem = {"pyproject.toml": "python", "package.json": "npm", "Cargo.toml": "cargo", "go.mod": "go"}.get(name)
            if not ecosystem:
                continue
            try:
                package = value.get("project", {}) if ecosystem == "python" else value.get("package", {}) if ecosystem == "cargo" else value
                declared = package.get("name")
                if declared is None:
                    if ecosystem in {"python", "cargo"} and ("workspace" in value or "tool" in value or "build-system" in value):
                        self.reasons.add("manifest_without_package")
                        continue
                    raise ValueError
                declared = text(declared)
                component = {"component_id": stable("pmcomp_", self.repository, path, declared), "name": declared,
                    "ecosystem": ecosystem, "manifest_path": path, "repository": self.repository, "commit_sha": self.commit,
                    "published_name": None if value.get("private") is True or package.get("publish") is False or package.get("publish") == [] else declared,
                    "version": package.get("version") if isinstance(package.get("version"), str) else None,
                    "version_reason": None if isinstance(package.get("version"), str) else "not_declared",
                    "evidence_refs": [self.ref(path)], "maintenance_owner": None,
                    "maintenance_owner_reason": "not_named_in_visible_policy", "verification_hints": []}
                self.components.append(component)
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

    def resolution(self, component, name):
        eco, manifest = component["ecosystem"], component["manifest_path"]
        lockname = {"python": "uv.lock", "npm": "package-lock.json", "cargo": "Cargo.lock", "go": "go.sum"}[eco]
        path, lock = _nearest(manifest, lockname, self.parsed)
        if lock is None:
            return None, None, []
        try:
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
                    return None, None, []
                version = text(entry["version"])
                bins = list(entry.get("bin", {})) if isinstance(entry.get("bin"), dict) else []
            elif eco == "go":
                # go.sum is a checksum catalog, not a resolver's lockfile.
                return None, None, []
            else:
                versions = {text(p["version"]) for p in lock.get("package", []) if p.get("name") == name}
                if len(versions) != 1:
                    return None, None, []
                version, bins = next(iter(versions)), []
            return version, self.ref(path, package=name), bins
        except (TypeError, KeyError, AttributeError, ValueError):
            row = next(r for r in self.files if r["path"] == path)
            row.update(status="unparsable", reason="unsupported_lock_structure")
            return None, None, []

    def add(self, component, name, version, relation, *, basis, refs=None, kind=None, ecosystem=None, resolved=True):
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
        version_resolved, lock_ref, _ = self.resolution(component, name) if resolved else (None, None, [])
        if lock_ref:
            refs = [*refs, lock_ref]
        self.edges.append({"edge_id": stable("pmde_", component["component_id"], key, relation, basis, refs),
            "component_id": component["component_id"], "dependency_key": key, "direction": "component_to_dependency",
            "relation": relation, "kind": kind, "version": version, "resolved": version_resolved,
            "version_reason": "not_declared" if version is None else None,
            "resolution_reason": None if lock_ref else "no_unique_lock_resolution" if resolved else "go_sum_not_lockfile" if eco == "go" else "not_lockfile_dependency",
            "evidence_level": "verified" if lock_ref else "declared", "basis": basis, "evidence_refs": refs})

    def dev(self, component, name, version, basis, scripts=None):
        _, _, bins = self.resolution(component, name)
        bins = bins or BINARIES.get(name, [])
        evidence = []
        for script, command in (scripts or {}).items():
            if not isinstance(command, str):
                raise ValueError
            relation = "test" if re.match(r"^(?:test|check)(?::|$)", script) else "build" if re.match(r"^(?:build|prepare|prepack)(?::|$)", script) else None
            if relation and set(bins) & binaries(command):
                evidence.append((relation, self.ref(component["manifest_path"], script=script, command=command)))
        for path, step in self.steps(component):
            relation = self.step_relation(component, step)
            if relation and set(bins) & binaries(step["command"]):
                evidence.append((relation, self.ref(path, job_id=step["job_id"], line=step["line"], command=step["command"])))
        if evidence:
            for relation, ref in evidence:
                self.add(component, name, version, relation, basis=basis, refs=[self.ref(component["manifest_path"]), ref])
        else:
            self.add(component, name, version, "build", basis="dev_unclassified")

    def component(self, c, value):
        eco = c["ecosystem"]
        if eco == "python":
            project = value["project"]
            for raw in project.get("dependencies", []):
                name, version = requirement(raw)
                self.add(c, name, version, "runtime", basis="project.dependencies")
            for raw in value.get("build-system", {}).get("requires", []):
                name, version = requirement(raw)
                self.add(c, name, version, "build", basis="build-system.requires")
            if project.get("requires-python"):
                self.add(c, "python", project["requires-python"], "build", basis="requires-python", ecosystem="toolchain", resolved=False)
            groups = {**project.get("optional-dependencies", {}), **value.get("dependency-groups", {})}
            for group, values in groups.items():
                for raw in values:
                    if isinstance(raw, dict) and set(raw) == {"include-group"}:
                        continue  # The included group's own declarations are already present.
                    name, version = requirement(raw)
                    self.dev(c, name, version, "python_group:" + group)
            for path, values in self.parsed.items():
                if posixpath.basename(path).startswith("requirements") and posixpath.dirname(path) == posixpath.dirname(c["manifest_path"]):
                    for name, version in values:
                        self.add(c, name, version, "build", basis="dev_unclassified", refs=[self.ref(path)])
        elif eco == "npm":
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
                        if isinstance(requirement_value, dict) and requirement_value.get("workspace"):
                            wp, root = _nearest(c["manifest_path"], "Cargo.toml", {p: v for p, v in self.parsed.items() if isinstance(v, dict) and "workspace" in v})
                            requirement_value = root["workspace"]["dependencies"][alias]
                            refs.append(self.ref(wp))
                        name = requirement_value.get("package", alias) if isinstance(requirement_value, dict) else alias
                        version = requirement_value.get("version") or requirement_value.get("git") or requirement_value.get("path") or "*" if isinstance(requirement_value, dict) else requirement_value
                        self.add(c, name, version, relation, basis=prefix + group, refs=refs)
            if value["package"].get("rust-version"):
                self.add(c, "rust", value["package"]["rust-version"], "build", basis="rust-version", ecosystem="toolchain", resolved=False)
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

    def step_relation(self, component, step):
        for words in _commands(step["command"]):
            if words[:2] in (["uv", "build"], ["cargo", "build"], ["go", "build"]):
                return "build"
            if words[:2] in (["cargo", "test"], ["cargo", "check"], ["go", "test"]):
                return "test"
            if words[:2] == ["npm", "test"]:
                return "test"
            if words[:2] == ["npm", "run"] and len(words) >= 3:
                if re.match(r"^(?:test|check)(?::|$)", words[2]):
                    return "test"
                if re.match(r"^(?:build|prepare|prepack)(?::|$)", words[2]):
                    return "build"
            if words[:2] == ["uv", "run"]:
                words = words[2:]
            if words[:2] in (["python", "-m"], ["python3", "-m"]):
                words = words[2:]
            if words and words[0] in {"pytest", "tox", "ruff", "mypy"}:
                return "test"
            if words and words[0] in {"vite", "tsc"}:
                return "build"
            if words and words[0] in {"vitest", "playwright"}:
                return "test"
        return None

    def ci(self):
        for c in self.components:
            eco = c["ecosystem"]
            for path, step in self.steps(c):
                command = step["command"]
                groups = _commands(command)
                tools = {"python": {"uv", "python", "python3", "pytest", "ruff", "tox"}, "npm": {"npm", "npx"}, "cargo": {"cargo"}, "go": {"go"}}[eco]
                if eco == "npm":
                    for name in self.parsed[c["manifest_path"]].get("devDependencies", {}):
                        _, _, bins = self.resolution(c, name)
                        tools.update(bins or BINARIES.get(name, []))
                calls = [words for words in groups if words and words[0] in tools]
                if not calls:
                    continue
                relation = self.step_relation(c, step)
                if not relation:
                    continue
                ref = self.ref(path, job_id=step["job_id"], line=step["line"], command=command)
                hint = {"hint_id": stable("pmhint_", c["component_id"], ref), "component_id": c["component_id"], "relation": relation, **ref}
                c["verification_hints"].append(hint)
                self.hints.append(hint)
                for tool in sorted({words[0] for words in calls}):
                    self.add(c, tool, None, relation, basis="workflow_step", refs=[ref], ecosystem="toolchain", resolved=False)
                steps = self.parsed[path]
                for action in (s for s in steps if s["job_id"] == step["job_id"] and s["uses"]):
                    name, sep, version = action["uses"].rpartition("@")
                    if sep and name and "$" not in action["uses"]:
                        self.add(c, name, version, relation, basis="workflow_uses", refs=[self.ref(path, job_id=action["job_id"], line=action["line"], uses=action["uses"])], ecosystem="github_action", resolved=False)

    def toolchains(self):
        for c in self.components:
            eco = c["ecosystem"]
            names = {"python": [".python-version", ".tool-versions"], "npm": [".nvmrc", ".tool-versions"], "cargo": ["rust-toolchain", "rust-toolchain.toml", ".tool-versions"], "go": [".tool-versions"]}[eco]
            for name in names:
                path, value = _nearest(c["manifest_path"], name, self.parsed)
                if path is None:
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
