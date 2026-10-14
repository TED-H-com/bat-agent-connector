"""Unsigned macOS DMG packaging retries once; other platforms and signed releases do not."""

import subprocess
from pathlib import Path

from scripts.ci_scope import NATIVE_BUILDS

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "desktop/scripts/package-unsigned-macos.sh"
DESKTOP_WORKFLOW = ROOT / ".github/workflows/desktop.yml"
SIGNED_WORKFLOWS = (
    ROOT / ".github/workflows/desktop-signed.yml",
    ROOT / ".github/workflows/desktop-macos-signed.yml",
)
PACKAGE_STEP = "      - name: Package app (unsigned validation artifact)\n"
LINUX_WINDOWS_COMMAND = "run: npm run tauri -- build --bundles ${{ matrix.bundle }} -- --locked\n"


def run_bash(function, args, stdin=""):
    completed = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; func="$2"; shift 2; "$func" "$@"',
            "bash",
            str(SCRIPT),
            function,
            *args,
        ],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def device_line(dev, hint, mount=None):
    if mount is None:
        return f"{dev}\t{hint}"
    return f"{dev}\t{hint}\t{mount}"


def image_block(path, devices):
    lines = [
        "================================================",
        f"image-path      : {path}",
        "image-alias     : " + path,
        "system-image    : false",
    ]
    lines.extend(devices)
    return "\n".join(lines)


def test_linux_and_windows_package_command_stays_exact_and_signed_workflows_do_not_retry():
    workflow = DESKTOP_WORKFLOW.read_text()
    parts = workflow.split(PACKAGE_STEP)
    assert len(parts) == 3
    non_mac, mac = parts[1], parts[2]
    assert non_mac.startswith("        if: runner.os != 'macOS'\n")
    assert LINUX_WINDOWS_COMMAND in non_mac
    assert "package-unsigned-macos.sh" not in non_mac
    assert "--verbose" not in non_mac
    assert mac.startswith("        if: runner.os == 'macOS'\n")
    assert 'run: bash scripts/package-unsigned-macos.sh "${{ matrix.bundle }}"\n' in mac
    assert "APPLE_SIGNING_IDENTITY: ${{ runner.os == 'macOS' && '-' || '' }}" in non_mac
    assert "APPLE_SIGNING_IDENTITY: ${{ runner.os == 'macOS' && '-' || '' }}" in mac
    assert {
        "os": "macos-15", "bundle": "app,dmg", "artifact": "desktop-macOS-arm64-validation",
    } in NATIVE_BUILDS
    assert {
        "os": "macos-15-intel", "bundle": "app,dmg", "artifact": "desktop-macOS-x64-validation",
    } in NATIVE_BUILDS
    assert "desktop/src-tauri/target/release/bundle/dmg/*.dmg" in workflow
    assert "Installed macOS DMG, WebView, Dock reopen and normal Quit fixture" in mac
    for path in SIGNED_WORKFLOWS:
        signed = path.read_text()
        assert "package-unsigned-macos.sh" not in signed
        assert "package-unsigned-macos" not in signed


def test_verbose_build_command_matches_the_unsigned_package_step():
    raw = run_bash("package_build_command", ["app,dmg"])
    assert raw.endswith("\0")
    assert raw.split("\0")[:-1] == [
        "npm", "run", "tauri", "--", "build", "--bundles", "app,dmg", "--verbose", "--", "--locked",
    ]


def test_script_refuses_to_package_off_macos():
    completed = subprocess.run(
        ["bash", str(SCRIPT), "app,dmg"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "only on macOS" in completed.stderr
    assert completed.stdout == ""


def test_only_this_bundles_volumes_under_volumes_are_detached(tmp_path):
    bundle = tmp_path / "bundle"
    (bundle / "macos").mkdir(parents=True)
    (bundle / "dmg").mkdir()
    rw_image = bundle / "macos" / "rw.4242.Better Agent Dashboard_0.1.1_x64.dmg"
    final_image = bundle / "dmg" / "Better Agent Dashboard_0.1.1_x64.dmg"
    rw_image.write_bytes(b"rw")
    final_image.write_bytes(b"dmg")
    outside = tmp_path / "bundle-evil" / "macos"
    outside.mkdir(parents=True)
    foreign = outside / "rw.1.Other.dmg"
    foreign.write_bytes(b"no")
    missing = bundle / "macos" / "rw.99.Better Agent Dashboard_0.1.1_x64.dmg"
    info = "\n".join([
        "framework       : 650",
        image_block(str(foreign), [
            device_line("/dev/disk2", "GUID_partition_scheme"),
            device_line("/dev/disk2s1", "Apple_HFS", "/Volumes/Other Product"),
        ]),
        image_block(str(rw_image), [
            device_line("/dev/disk4", "GUID_partition_scheme"),
            device_line("/dev/disk4s1", "Apple_HFS", "/Volumes/dmg.ABCDE"),
            device_line("/dev/disk4s1", "Apple_HFS", "/Volumes/dmg.ABCDE"),
        ]),
        image_block(str(final_image), [
            device_line("/dev/disk5s1", "Apple_HFS", "/private/tmp/custom-mount"),
            device_line("/dev/disk5s2", "Apple_HFS", "/Volumes/Better Agent Dashboard"),
            device_line("/dev/disk5s3", "Apple_HFS", "/Volumes/dmg.NESTED/child"),
            device_line("/dev/disk5s4", "Apple_HFS", "/Volumes/.."),
        ]),
        image_block(str(missing), [
            device_line("/dev/disk6s1", "Apple_HFS", "/Volumes/dmg.MISSING"),
        ]),
        image_block("/Library/Apple/System/Library/Images/System.dmg", [
            device_line("/dev/disk0s1", "Apple_APFS", "/Volumes/Macintosh HD"),
        ]),
        image_block("<none>", [
            device_line("/dev/disk1s1", "Apple_HFS", "/Volumes/dmg.NONE"),
        ]),
    ]) + "\n"
    mounts = run_bash("list_mounts_to_detach", [str(bundle)], info).splitlines()
    assert mounts == [
        "/Volumes/dmg.ABCDE",
        "/Volumes/Better Agent Dashboard",
        "/Volumes/dmg.MISSING",
    ]


def test_partial_dmg_cleanup_leaves_the_app_and_unrelated_files(tmp_path):
    bundle = tmp_path / "bundle"
    app = bundle / "macos" / "Better Agent Dashboard.app" / "Contents"
    app.mkdir(parents=True)
    (bundle / "dmg").mkdir()
    (bundle / "share").mkdir()
    keep = [
        app / "MacOS" / "better-agent-dashboard",
        app / "rw.inside-app.dmg",
        bundle / "dmg" / "bundle_dmg.sh",
        bundle / "dmg" / "icon.icns",
        bundle / "dmg" / "nested" / "partial.dmg",
        bundle / "macos" / "Better Agent Dashboard_0.1.1_x64.dmg",
        bundle / "share" / "create-dmg" / "rw.too-deep.dmg",
    ]
    remove = [
        bundle / "dmg" / "Better Agent Dashboard_0.1.1_x64.dmg",
        bundle / "dmg" / "rw.7.Better Agent Dashboard_0.1.1_x64.dmg",
        bundle / "macos" / "rw.4242.Better Agent Dashboard_0.1.1_x64.dmg",
    ]
    for path in keep + remove:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    listed = {path for path in run_bash("collect_partial_dmg_paths", [str(bundle)]).split("\0") if path}
    assert listed == {str(path) for path in remove}
    output = run_bash("remove_partial_dmgs", [str(bundle)])
    for path in remove:
        assert not path.exists()
        assert str(path) in output
    for path in keep:
        assert path.is_file()
    output = run_bash("remove_partial_dmgs", [str(bundle)])
    assert "No partial DMG files to remove." in output
