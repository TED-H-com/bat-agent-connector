#!/usr/bin/env python3
"""Standard-library bootstrap; fetch this script at the same pinned release tag."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

REPOSITORY = "https://github.com/TED-H-com/bat-agent-connector"
API = "https://api.github.com/repos/TED-H-com/bat-agent-connector/releases/tags/"


class InstallError(ValueError):
    pass


def require(value, code):
    if not value:
        raise InstallError(code)


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(allowed(newurl), "download_redirect_refused")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def allowed(url):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == "https" and parsed.port in (None, 443)
            and not parsed.username and not parsed.password
            and parsed.hostname in {"api.github.com", "github.com", "release-assets.githubusercontent.com",
                                    "objects.githubusercontent.com"})


def fetch(url):
    require(allowed(url), "download_origin_refused")
    headers = {"User-Agent": "batc-installer/1", "Accept": "application/json"}
    # Deliberately do not inspect or report the credential. Never forward it to assets.
    if url.startswith(API) and os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirect())
    with opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response:  # noqa: S310
        data = response.read(1024 * 1024 + 1)
    require(len(data) <= 1024 * 1024, "download_size_limit")
    return data


def resolve(tag, channel, download=fetch):
    require(re.fullmatch(r"v[0-9][A-Za-z0-9.+-]{0,95}", tag), "invalid_release_tag")
    item = json.loads(download(API + urllib.parse.quote(tag, safe="")))
    require(item["tag_name"] == tag and not item.get("draft") and item.get("published_at"), "release_unpublished")
    require(channel != "stable" or not item.get("prerelease"), "release_channel_mismatch")
    base = REPOSITORY + "/releases/download/" + urllib.parse.quote(tag, safe="") + "/"
    raw = download(base + "release-manifest.json")
    sums = download(base + "SHA256SUMS").decode("utf-8")
    assets = [a for a in item["assets"] if a["name"] == "release-manifest.json"]
    require(len(assets) == 1 and assets[0]["size"] == len(raw), "manifest_asset_mismatch")
    digest = hashlib.sha256(raw).hexdigest()
    if assets[0].get("digest") is not None:
        require(assets[0]["digest"] == "sha256:" + digest, "manifest_asset_digest_mismatch")
    matches = [line.split()[0] for line in sums.splitlines()
               if len(line.split()) == 2 and line.split()[1].lstrip("*") == "release-manifest.json"]
    require(matches == [digest], "manifest_checksum_mismatch")
    doc = json.loads(raw)
    require(doc["format"] == 1 and doc["repository"] == REPOSITORY and doc["release"] == tag,
            "manifest_identity_mismatch")
    require(re.fullmatch(r"[0-9a-f]{40}", doc["source_commit"]), "invalid_source_commit")
    ver = doc["versions"]["connector"]
    require(re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", ver), "invalid_version")
    require(tuple(map(int, ver.split("."))) >= (0, 2, 7), "updater_release_required")
    require(tag == "v" + ver or tag.startswith("v" + ver + "-"), "manifest_version_mismatch")
    update = doc["connector_update"]
    require(update["protocol"] == 1 and update["compatibility_epoch"] == 1 and update["python"] == "3.13",
            "manifest_update_incompatible")
    require(re.fullmatch(r"[0-9a-f]{64}", update["canonical_skill_sha256"]), "invalid_skill_digest")
    require(doc["validation"]["status"] == "passed", "release_not_validated")
    return {"commit": doc["source_commit"], "version": ver, "tag": tag, "epoch": 1,
            "skill_sha256": update["canonical_skill_sha256"], "manifest_sha256": digest,
            "prerelease": bool(item.get("prerelease"))}


def command(args, *, env, cwd=None):
    # Do not echo child diagnostics: Git/uv errors may contain local configuration.
    with tempfile.TemporaryFile(dir=env.get("TMPDIR")) as output:
        result = subprocess.run(args, env=env, cwd=cwd, stdout=output, stderr=output,  # noqa: S603
                                stdin=subprocess.DEVNULL, timeout=900)
        stage = ("verification" if "bat_agent_connector.installer" in args else
                 "uv_install" if "tool" in args else "dependency_lock" if "export" in args else
                 "source_checkout" if "git" in Path(args[0]).name else "update_enrollment")
        require(result.returncode == 0, stage + "_command_failed")
        require(output.tell() <= 4 * 1024 * 1024, "command_output_limit")
        output.seek(0)
        return output.read().decode().strip()


def readonly_command(args, *, env):
    result = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,  # noqa: S603
                            stdin=subprocess.DEVNULL, timeout=180)
    require(result.returncode == 0, "verification_command_failed")
    require(len(result.stdout) <= 1024 * 1024, "verification_output_limit")
    return result.stdout.decode().strip()


def private_directory(path):
    require(path.is_absolute(), "absolute_root_required")
    for parent in reversed((path, *path.parents)):
        require(not parent.is_symlink(), "directory_symlink_refused")
        if not parent.exists():
            parent.mkdir(mode=0o700)
    require(path.stat().st_uid == os.getuid() and not path.stat().st_mode & 0o077, "directory_not_private")


def install(args, *, home=None, download=fetch, run=command):
    home = Path.home() if home is None else Path(home)
    require(sys.platform.startswith("linux") or sys.platform == "darwin", "unsupported_platform")
    require(not args.enable_updates or sys.platform.startswith("linux"), "automatic_updates_require_linux")
    require(os.getuid() != 0 or args.allow_root, "root_requires_allow_root")
    state = home / ".local/share/bat-agent-connector/update/state.json"
    require(args.verify_only or not (state.exists() or state.is_symlink()), "already_update_managed")
    release = resolve(args.tag, args.channel_check, download)
    root = (args.root or home / ".local/share/bat-agent-connector/releases" / release["commit"]).absolute()
    if args.dry_run:
        return {"status": "verified_release", "release": release, "writes": False}
    uv = shutil.which("uv")
    git = shutil.which("git")
    require(uv and git, "uv_and_git_required")
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
           "UV_NO_CONFIG": "true", "UV_TOOL_DIR": str(root / "tools"), "UV_TOOL_BIN_DIR": str(root / "bin")}
    python = root / "tools/bat-agent-connector/bin/python"
    if args.verify_only:
        # The installed verifier reads only; no uv invocation, checkout or receipt write.
        require(python.exists(), "installed_python_missing")
        verify = readonly_command if run is command else run
        return json.loads(verify([str(python), "-I", "-B", "-m", "bat_agent_connector.installer", "--verify-only",
                                  str(root), json.dumps(release)], env=env))
    require(not root.exists() and not root.is_symlink(), "installation_root_exists")
    private_directory(root)
    # All subprocess temporaries live on disk inside the installation, not /tmpfs.
    scratch = root / "scratch"
    private_directory(scratch)
    env["TMPDIR"] = str(scratch)
    with tempfile.TemporaryDirectory(dir=scratch) as temporary:
        source = Path(temporary) / "source"
        base = [git, "-c", "core.hooksPath=/dev/null", "-c", "credential.helper=", "-c", "protocol.allow=never",
                "-c", "protocol.https.allow=always"]
        run([*base, "init", "--quiet", str(source)], env=env)
        run([*base, "-C", str(source), "fetch", "--quiet", "--depth=1", REPOSITORY + ".git",
             "refs/tags/" + release["tag"]], env=env)
        require(run([*base, "-C", str(source), "rev-parse", "FETCH_HEAD^{commit}"], env=env) == release["commit"],
                "published_tag_commit_mismatch")
        run([*base, "-C", str(source), "checkout", "--quiet", "--detach", release["commit"]], env=env)
        constraints = Path(temporary) / "constraints.txt"
        run([uv, "export", "--locked", "--no-dev", "--no-emit-project", "--no-hashes",
             "--output-file", str(constraints)], env=env, cwd=source)
        run([uv, "tool", "install", "--python", "3.13", "--constraints", str(constraints),
             "git+" + REPOSITORY + "@" + release["commit"]], env=env)
        result = json.loads(run([str(python), "-I", "-m", "bat_agent_connector.installer", str(root),
                                 json.dumps(release), "--source", str(source)], env=env))
    receipt = root / "rollout-receipt.json"
    if args.enable_updates:
        run([str(root / "bin/batc-update"), "enable", "--channel", args.channel_check,
             "--receipt", str(receipt)], env=env)
        run([str(root / "bin/batc-update"), "schedule"], env=env)
        result["automatic_updates"] = "enrolled_and_scheduled"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--channel-check", choices=("stable", "preview"), default="preview")
    parser.add_argument("--root", type=Path, help="New private version directory; does not relocate user configuration")
    parser.add_argument("--dry-run", action="store_true", help="Verify release metadata only; no writes")
    parser.add_argument("--verify-only", action="store_true", help="Verify existing installation; no writes")
    parser.add_argument("--enable-updates", action="store_true")
    parser.add_argument("--allow-root", action="store_true")
    args = parser.parse_args(argv)
    try:
        require(sys.version_info >= (3, 10), "python_310_required")
        require(not (args.dry_run and args.verify_only), "choose_one_verification_mode")
        result = install(args)
        print(json.dumps(result, sort_keys=True))
        if result.get("receipt") and not args.verify_only:
            import shlex
            print("Add ~/.local/bin to PATH, then on Linux run:")
            print("batc-update enable --channel " + args.channel_check + " --receipt " + shlex.quote(result["receipt"]))
            print("batc-update schedule")
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # Stable stages, never exception text containing a URL, credential or local config.
        code = str(exc) if isinstance(exc, InstallError) else "release_or_installation_failed"
        print("Installer failed: " + code, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
