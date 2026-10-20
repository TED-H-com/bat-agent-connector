"""Stage a published Connector in its permanent environment; never restart work."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from . import platform_files as files
from .update_release import REPOSITORY, require


def clean_env(home):
    return {"HOME": str(home), "PATH": str(home / ".local/bin") + ":/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0", "UV_PYTHON_DOWNLOADS": "never", "UV_NO_CONFIG": "true"}


def run(args, *, env, cwd=None, timeout=180):
    # The file bounds RAM even when a build emits excessive diagnostics. Its
    # contents are never published; only a stable failure code reaches receipts.
    logs = Path(env["HOME"]) / ".cache/batc-update"
    files.ensure_private_directory(logs)
    with tempfile.TemporaryFile(dir=logs) as output:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdout=output, stderr=output,  # noqa: S603
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                require(os.fstat(output.fileno()).st_size <= 4 * 1024 * 1024,
                        "update_command_output_limit")
                require(time.monotonic() < deadline, "update_command_timeout")
                try:
                    process.wait(timeout=min(0.25, max(0.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
            require(process.returncode == 0, "update_command_failed")
            require(output.tell() <= 4 * 1024 * 1024, "update_command_output_limit")
            output.seek(0)
            return output.read().decode("utf-8", errors="replace").strip()
        except BaseException:
            # An interrupted updater must stop its installer before releasing the
            # updater lease. The child has its own group, including build helpers.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            # A child can exit on TERM while its descendants ignore it.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise


def package_files(path, *, python_only=False):
    result = {}
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path)
        require(not item.is_symlink(), "package_symlink_refused")
        if "__pycache__" in relative.parts or item.is_dir() or relative.parts[0] == "skill":
            continue
        if python_only and item.suffix != ".py":
            continue
        require(item.is_file() and item.stat().st_size <= 20 * 1024 * 1024, "package_file_invalid")
        result[relative.as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
        require(len(result) <= 5000, "package_file_limit")
    require(result, "package_empty")
    return result


def digest_files(values):
    digest = hashlib.sha256()
    for name, value in sorted(values.items()):
        digest.update(name.encode() + b"\0" + value.encode() + b"\n")
    return digest.hexdigest()


def verify_source_package(source, package, commit):
    require(package_files(source) == package_files(package), "installed_source_mismatch")


def prepare_tool_paths(root):
    """A partial uv attempt cannot redirect the next install outside its root."""
    for name in ("tools", "bin"):
        files.ensure_private_directory(root / name)
    environment = root / "tools/bat-agent-connector"
    for entry in (root / "tools").iterdir():
        require(entry.name in {"bat-agent-connector", ".lock", ".gitignore"} and not entry.is_symlink(),
                "candidate_tool_path_changed")
    if environment.exists():
        descriptor = files._posix_dir(environment)
        os.close(descriptor)
        receipt = environment / "uv-receipt.toml"
        if receipt.exists() or receipt.is_symlink():
            info = receipt.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                    and info.st_size <= 1024 * 1024, "candidate_tool_receipt_changed")
            tool = tomllib.loads(receipt.read_text())["tool"]
            require(isinstance(tool, dict) and isinstance(tool.get("entrypoints", []), list),
                    "candidate_tool_receipt_changed")
            for entry in tool.get("entrypoints", []):
                require(isinstance(entry, dict), "candidate_tool_receipt_changed")
                name = entry.get("name")
                require(name in {"batc", "bat-agent-connector-mcp", "batc-update"}
                        and entry.get("install-path") == str(root / "bin" / name)
                        and entry.get("from") == "bat-agent-connector", "candidate_tool_receipt_changed")
    for entry in (root / "bin").iterdir():
        require(entry.name in {"batc", "bat-agent-connector-mcp", "batc-update"}
                and entry.is_symlink() and entry.resolve() == environment / "bin" / entry.name,
                "candidate_tool_entrypoint_changed")


def installed(root, release, *, home, command=run):
    files.check_private(root, directory=True)
    files.ensure_private_directory(root)
    environment = root / "tools/bat-agent-connector"
    packages = list(environment.glob("lib/python*/site-packages/bat_agent_connector"))
    require(len(packages) == 1, "installed_package_missing")
    package = packages[0]
    # Resolve the package's directories without traversing links. The interpreter
    # itself may be a uv-owned symlink outside this environment.
    require(package.resolve() == package.absolute(), "installed_package_linked")
    python = environment / "bin/python"
    distributions = list(package.parent.glob("bat_agent_connector-*.dist-info"))
    require(len(distributions) == 1, "installed_metadata_missing")
    distribution = distributions[0]
    require(distribution.resolve() == distribution.absolute(), "installed_metadata_linked")
    for name in ("direct_url.json", "METADATA"):
        require(not (distribution / name).is_symlink(), "installed_metadata_linked")
    provenance = json.loads((distribution / "direct_url.json").read_text())
    require(isinstance(provenance, dict) and isinstance(provenance.get("vcs_info"), dict),
            "installed_identity_mismatch")
    require(provenance.get("vcs_info", {}).get("commit_id") == release["commit"]
            and provenance.get("vcs_info", {}).get("vcs") == "git", "installed_identity_mismatch")
    require("\nVersion: " + release["version"] + "\n" in (distribution / "METADATA").read_text(),
            "installed_version_mismatch")
    declarations = [ast.literal_eval(node.value) for node in ast.parse((package / "__init__.py").read_text()).body
                    if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == "__version__"
                                                           for n in node.targets)]
    require(declarations == [release["version"]], "installed_version_mismatch")
    launchers = {}
    for name, module in (("batc", "cli"), ("bat-agent-connector-mcp", "mcp_server"), ("batc-update", "updater")):
        entry = root / "bin" / name
        require(entry.is_symlink() and entry.resolve() == (environment / "bin" / name).absolute(),
                "installed_entrypoint_mismatch")
        require(stat.S_ISREG(entry.stat().st_mode) and os.access(entry, os.X_OK), "installed_entrypoint_missing")
        script = entry.read_text()
        headers = (f"#!{python}\n", f"#!/bin/sh\n'''exec' '{python}' \"$0\" \"$@\"\n' '''\n")
        prefix = next((head for head in headers if script.startswith(head)), None)
        require(prefix is not None, "installed_launcher_interpreter_mismatch")
        body = script[len(prefix):]
        expected = ("# -*- coding: utf-8 -*-\nimport sys\nfrom bat_agent_connector." + module + " import main\n"
                    'if __name__ == "__main__":\n'
                    '    if sys.argv[0].endswith("-script.pyw"):\n        sys.argv[0] = sys.argv[0][:-11]\n'
                    '    elif sys.argv[0].endswith(".exe"):\n        sys.argv[0] = sys.argv[0][:-4]\n'
                    '    sys.exit(main())\n')
        require(body == expected, "installed_launcher_changed")
        launchers[name] = hashlib.sha256(script.encode()).hexdigest()
    skill = package / "skill/SKILL.md"
    require(not skill.is_symlink(), "installed_skill_linked")
    skill_digest = hashlib.sha256(skill.read_bytes()).hexdigest()
    require(skill_digest == release["skill_sha256"], "installed_skill_mismatch")
    return {"files": digest_files(package_files(package)), "python_files": digest_files(package_files(package, python_only=True)),
            "skill_sha256": skill_digest, "package": str(package), "launchers": launchers}


def stage(root, release, *, home, command=run):
    files.ensure_private_directory(root)
    marker = root / "candidate.json"
    expected = json.dumps(release, sort_keys=True).encode()
    if marker.exists():
        require(files.read_private(marker) == expected, "candidate_identity_changed")
    else:
        require(not any(root.iterdir()), "candidate_not_owned")
        files.atomic_write(marker, expected)
    ready = root / "ready.json"
    if ready.exists():
        proof = json.loads(files.read_private(ready))
        require(installed(root, release, home=home, command=command) == proof, "staged_package_changed")
        return proof
    prepare_tool_paths(root)
    env = clean_env(home)
    env.update(UV_TOOL_DIR=str(root / "tools"), UV_TOOL_BIN_DIR=str(root / "bin"), UV_PYTHON="3.13")
    git = shutil.which("git", path=env["PATH"])
    uv = shutil.which("uv", path=env["PATH"])
    require(git and uv, "update_tools_unavailable")
    scratch = root / "scratch"
    files.ensure_private_directory(scratch)
    with tempfile.TemporaryDirectory(prefix="source-", dir=scratch) as temporary:
        source = Path(temporary) / "repository"
        base = [git, "-c", "core.hooksPath=/dev/null", "-c", "credential.helper=", "-c", "protocol.allow=never",
                "-c", "protocol.https.allow=always"]
        command([*base, "init", "--quiet", str(source)], env=env)
        command([*base, "-C", str(source), "fetch", "--quiet", "--depth=1", REPOSITORY + ".git",
                 "refs/tags/" + release["tag"]], env=env)
        actual = command([*base, "-C", str(source), "rev-parse", "FETCH_HEAD^{commit}"], env=env)
        require(actual == release["commit"], "published_tag_commit_mismatch")
        command([*base, "-C", str(source), "checkout", "--quiet", "--detach", actual], env=env)
        canonical = source / "skills/bat-agent-connector/SKILL.md"
        require(hashlib.sha256(canonical.read_bytes()).hexdigest() == release["skill_sha256"],
                "source_skill_mismatch")
        constraints = Path(temporary) / "constraints.txt"
        command([uv, "export", "--locked", "--no-dev", "--no-emit-project", "--no-hashes",
                 "--output-file", str(constraints)], cwd=source, env=env)
        command([uv, "tool", "install", "--force", "--python", "3.13", "--constraints", str(constraints),
                 "git+" + source.as_uri() + "@" + actual], env=env)
        packages = list((root / "tools/bat-agent-connector").glob("lib/python*/site-packages/bat_agent_connector"))
        require(len(packages) == 1, "installed_package_missing")
        verify_source_package(source / "src/bat_agent_connector", packages[0], actual)
        proof = installed(root, release, home=home, command=command)
        files.atomic_write(root / "skill/SKILL.md", canonical.read_bytes())
        files.atomic_write(ready, json.dumps(proof, sort_keys=True).encode())
        return proof
