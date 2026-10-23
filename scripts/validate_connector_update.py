#!/usr/bin/env python3
"""Real isolated updater install from a local fixture tag, with no live BAT.

Use a temporary directory on disk; all fixture repositories and environments are
deleted on exit. Only the release transport is substituted. uv installs the real
locked package and the production source/entrypoint/metadata verifier reads it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import selectors
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 test coverage
    import tomli as tomllib

from bat_agent_connector import __version__
from bat_agent_connector import platform_files as files
from bat_agent_connector.update_install import clean_env, installed, run, stage
from bat_agent_connector.update_release import REPOSITORY
from bat_agent_connector.updater import Updater

ROOT = Path(__file__).resolve().parents[1]


def fixture_build_inputs(root):
    """Project metadata is the authority for the package's declared inputs."""
    document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = document["project"]
    build = document["tool"]["hatch"]["build"]
    names = {"pyproject.toml", "uv.lock", "skills/bat-agent-connector/SKILL.md"}
    # Hatch also consults VCS exclusions when selecting sdist contents.
    if (root / ".gitignore").is_file():
        names.add(".gitignore")
    readme = project.get("readme")
    if isinstance(readme, str):
        names.add(readme)
    elif isinstance(readme, dict) and "file" in readme:
        names.add(readme["file"])
    license = project.get("license")
    if isinstance(license, dict) and "file" in license:
        names.add(license["file"])
    for pattern in project.get("license-files", []):
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ValueError("Fixture license input must stay within the source tree")
        matches = [path.relative_to(root).as_posix() for path in root.glob(pattern) if path.is_file()]
        if not matches:
            raise FileNotFoundError(f"No files match declared license input: {pattern}")
        names.update(matches)
    targets = build.get("targets", {})
    names.update(targets.get("wheel", {}).get("packages", []))
    for config in (build, *targets.values()):
        names.update(config.get("force-include", {}))
        custom = config.get("hooks", {}).get("custom")
        if custom is not None:
            names.add(custom.get("path", "hatch_build.py"))
    for name in names:
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Fixture build input must stay within the source tree")
        (root / name).resolve().relative_to(root.resolve())
    return sorted(names)


def copy_fixture_source(root, source):
    for name in fixture_build_inputs(root):
        origin, destination = root / name, source / name
        if origin.is_dir():
            shutil.copytree(origin, destination, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)


def identity_line(process):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(timeout=30), "Fixture interpreter did not return its identity"
        return json.loads(process.stdout.readline())


def installed_build_provenance(root, release, *, env):
    python = root / "tools/bat-agent-connector/bin/python"
    facts = json.loads(run([str(python), "-I", "-B", "-c", """
import json
from importlib import metadata
from pathlib import Path
from bat_agent_connector import build_identity
package = Path(build_identity.__file__).parent
direct = json.loads(metadata.distribution('bat-agent-connector').read_text('direct_url.json'))
print(json.dumps({'identity': build_identity.build_identity(),
                  'embedded_commit': json.loads((package / 'build-info.json').read_text())['commit'],
                  'vcs_commit': direct['vcs_info']['commit_id']}))
"""], env=env))
    assert facts["identity"] == {"version": release["version"], "commit": release["commit"], "source": "vcs"}
    assert facts["embedded_commit"] == facts["vcs_commit"] == release["commit"]
    # This Git fixture has no PKG-INFO and clean_env strips build overrides. The
    # real hook emits HEAD only after proving tracked inputs clean. The installed
    # metadata therefore carries that proof without uv's disposable cache layout.
    return facts


def validate(parent):
    files.ensure_private_directory(parent)
    with tempfile.TemporaryDirectory(prefix="connector-update-fixture-", dir=parent) as temporary:
        scratch = Path(temporary)
        home = scratch / "home"
        files.ensure_private_directory(home / ".local/bin")
        uv = shutil.which("uv")
        assert uv and sys.version_info[:2] == (3, 13), "Use the Python 3.13 fixture environment with uv"
        (home / ".local/bin/uv").symlink_to(uv)
        (home / ".local/bin/python3.13").symlink_to(Path(sys.executable).resolve())
        source = scratch / "source"
        source.mkdir(mode=0o700)
        copy_fixture_source(ROOT, source)
        env = clean_env(home)
        git = shutil.which("git", path=env["PATH"])
        run([git, "init", "--quiet", str(source)], env=env)
        run([git, "-C", str(source), "add", "."], env=env)
        run([git, "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "isolated updater fixture"], env=env)
        sha = run([git, "-C", str(source), "rev-parse", "HEAD"], env=env)
        tag = "v" + __version__ + "-preview.fixture"
        run([git, "-C", str(source), "tag", tag], env=env)
        candidate = {"commit": sha, "version": __version__, "tag": tag, "epoch": 1, "prerelease": True,
                     "skill_sha256": hashlib.sha256((source / "skills/bat-agent-connector/SKILL.md").read_bytes()).hexdigest()}
        root = scratch / "installed"
        fetches = []
        releases = {sha: candidate}
        build_identities = []
        def command(args, **kwargs):
            # The production runner intentionally sanitizes the environment.
            # Keep build-backend archive temporaries on this fixture's disk too.
            kwargs["env"] = {**kwargs["env"], "TMPDIR": str(scratch)}
            if "fetch" in args:
                assert REPOSITORY + ".git" in args
                args = [value if value != REPOSITORY + ".git" else source.as_uri() for value in args]
                args = [args[0], "-c", "protocol.file.allow=always", *args[1:]]
                fetches.append(sha)
            result = run(args, **kwargs)
            if args[1:3] == ["tool", "install"]:
                release = releases[args[-1].rsplit("@", 1)[1]]
                target = Path(kwargs["env"]["UV_TOOL_DIR"]).parent
                identity = installed_build_provenance(target, release, env=kwargs["env"])
                build_identities.append(identity)
                print("Fixture build identity: " + json.dumps(identity), flush=True)
            return result
        proof = stage(root, candidate, home=home, command=command)
        assert installed(root, candidate, home=home) == proof
        assert stage(root, candidate, home=home, command=command) == proof
        assert fetches == [sha]
        version_text = run([str(root / "bin/batc"), "--version"], env=env)
        assert __version__ in version_text
        help_text = run([str(root / "bin/batc-update"), "--help"], env=env)
        assert "rollback" in help_text and "schedule" in help_text

        # Use the real installed bootstrap verifier and receipt producer too.
        # A second hand-written source comparison/digest would hide regressions.
        bootstrap = json.loads(run([
            str(root / "tools/bat-agent-connector/bin/python"), "-I", "-B", "-m",
            "bat_agent_connector.installer", str(root), json.dumps(candidate), "--source", str(source),
        ], env={**env, "TMPDIR": str(scratch)}))
        assert bootstrap["build_identity"] == build_identities[0]["identity"]
        receipt = Path(bootstrap["receipt"])
        feed = [candidate]
        def install(path, release, *, home):
            return stage(path, release, home=home, command=command)
        updater = Updater(home, find_release=lambda channel, observation=None: feed[0], install=install)
        updater.enroll(receipt, "preview")
        sentinel = home / "existing-journal"
        sentinel.write_bytes(b"fixed operation key and unknown sent effect")

        # Build a second real, independently committed fixture package. This is
        # never a public release or a relabelled production binary.
        parts = list(map(int, __version__.split(".")))
        parts[2] += 1
        next_version = ".".join(map(str, parts))
        for name, before, after in (
            ("pyproject.toml", f'version = "{__version__}"', f'version = "{next_version}"'),
            ("uv.lock", f'name = "bat-agent-connector"\nversion = "{__version__}"',
             f'name = "bat-agent-connector"\nversion = "{next_version}"'),
            ("src/bat_agent_connector/__init__.py", f'__version__ = "{__version__}"', f'__version__ = "{next_version}"'),
            ("skills/bat-agent-connector/SKILL.md", f"version: {__version__}\n", f"version: {next_version}\n"),
        ):
            path = source / name
            text = path.read_text()
            assert before in text
            path.write_text(text.replace(before, after, 1))
        run([git, "-C", str(source), "add", "."], env=env)
        run([git, "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "second isolated package"], env=env)
        next_sha = run([git, "-C", str(source), "rev-parse", "HEAD"], env=env)
        next_tag = "v" + next_version + "-preview.fixture"
        run([git, "-C", str(source), "tag", next_tag], env=env)
        feed[0] = {**candidate, "commit": next_sha, "tag": next_tag, "version": next_version,
                   "skill_sha256": hashlib.sha256((source / "skills/bat-agent-connector/SKILL.md").read_bytes()).hexdigest()}
        releases[next_sha] = feed[0]

        reader = subprocess.Popen(  # noqa: S603
            [str(root / "tools/bat-agent-connector/bin/python"), "-I", "-u", "-c",
             "import json;from bat_agent_connector.build_identity import build_identity;"
             "print(json.dumps(build_identity()));input();print(json.dumps(build_identity()))"],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True)
        try:
            original_identity = identity_line(reader)
            assert original_identity["commit"] == sha
            result = updater.apply()
            assert result["installed"]["commit"] == next_sha
            assert next_version in run([str(home / ".local/bin/batc"), "--version"], env=env)
            reader.stdin.write("continue\n")
            reader.stdin.flush()
            assert identity_line(reader) == original_identity
            assert reader.wait(timeout=10) == 0
        finally:
            if reader.poll() is None:
                reader.terminate()
                try:
                    reader.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    reader.kill()
                    reader.wait()
            for stream in (reader.stdin, reader.stdout, reader.stderr):
                stream.close()
        result = updater.rollback()
        assert result["installed"]["commit"] == sha and not result["enabled"]
        assert __version__ in run([str(home / ".local/bin/batc"), "--version"], env=env)
        assert (home / ".agents/skills/bat-agent-connector/SKILL.md").read_bytes() == (
            root / "skill/SKILL.md").read_bytes()
        assert sentinel.read_bytes() == b"fixed operation key and unknown sent effect"
        assert updater.apply()["installed"]["commit"] == sha
        assert len(fetches) == 2
        return {"status": "passed", "fixture_commit": sha, "package_version": __version__,
                "source_files_sha256": proof["files"], "canonical_skill_sha256": proof["skill_sha256"],
                "repeat_stage_reused_verified_environment": True, "fixture_resources_removed": True,
                "upgrade_fixture_commit": next_sha, "upgrade_fixture_version": next_version,
                "real_upgrade_and_rollback": True, "loaded_interpreter_unchanged": True,
                "journal_unchanged": True, "build_identities": build_identities,
                "receipt_from_installed_verifier": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent", type=Path, default=Path.home() / "agent-work/tmp/connector-update")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    files.ensure_private_directory(args.receipt.parent)
    try:
        result = validate(args.temp_parent)
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError):
        result = {"status": "failed", "fixture_resources_removed": True}
        files.atomic_write(args.receipt, (json.dumps(result, indent=2) + "\n").encode())
        raise
    files.atomic_write(args.receipt, (json.dumps(result, indent=2) + "\n").encode())
    print(json.dumps(result))


if __name__ == "__main__":
    main()
