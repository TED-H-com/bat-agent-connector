"""Check static assets, fragment targets and repository documentation links offline."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
PREFIX = "/bat-agent-connector/"
REPO = "https://github.com/TED-H-com/bat-agent-connector/blob/main/"
LOCKFILE = "desktop/package-lock.json"
LOCKFILE_DIGEST = "without_root_version"
# This checker is authoritative for pinned capture inputs; only tooling comes from the checkout.
# The recorded capture script's early guard covers a subset until the next recapture.
PINNED_PATHS = (
    "desktop/src/",
    "desktop/tests/demo/",
    "desktop/tests/conversation-fixture.ts",
    "src/bat_agent_connector/dashboard/",
)
WORKING_TREE_INPUTS = ("scripts/capture_site.mjs", LOCKFILE)


def lockfile_digest(data: bytes | str) -> str:
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed lockfile JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Malformed lockfile JSON: root must be an object")
    packages = value.get("packages")
    if packages is not None and not isinstance(packages, dict):
        raise ValueError("Malformed lockfile JSON: packages must be an object")
    root_package = packages.get("") if isinstance(packages, dict) else None
    if root_package is not None and not isinstance(root_package, dict):
        raise ValueError("Malformed lockfile JSON: root package must be an object")
    for holder, label in ((value, "root"), (root_package, "root package")):
        if isinstance(holder, dict) and "version" in holder:
            if not isinstance(holder["version"], str):
                raise ValueError(f"Malformed lockfile JSON: {label} version must be a string")
            holder.pop("version")
    normalized = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def source_entry(file: str, data: bytes) -> dict[str, str]:
    if file == LOCKFILE:
        return {"file": file, "digest": LOCKFILE_DIGEST, "sha256": lockfile_digest(data)}
    return {"file": file, "sha256": hashlib.sha256(data).hexdigest()}


class Page(HTMLParser):
    def __init__(self, path):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.links = []
        self.errors = []
        self.feed(path.read_text())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            if attrs["id"] in self.ids:
                self.errors.append("duplicate id: " + attrs["id"])
            self.ids.add(attrs["id"])
        if tag == "img" and "alt" not in attrs:
            self.errors.append("image without alt")
        if tag in {"a", "link", "script", "img"}:
            target = attrs.get("href") or attrs.get("src")
            if target:
                self.links.append(target)


def markdown_ids(path):
    # GitHub's plain-heading slug rules, including duplicate headings.
    ids = set()
    counts = {}
    in_code = False
    for line in path.read_text().splitlines():
        if line.startswith("```"):
            in_code = not in_code
        if in_code or not re.match(r"^#{1,6} ", line):
            continue
        title = re.sub(r"^#+\s+", "", line).strip().lower()
        title = re.sub(r"[^\w\-\s]", "", title)
        slug = title.replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        ids.add(slug if count == 0 else f"{slug}-{count}")
    return ids


def check_link(source, value, errors):
    url = urlsplit(value)
    if value.startswith(REPO):
        relative = unquote(urlsplit(value[len(REPO) :]).path)
        target = ROOT / relative
    elif url.scheme or url.netloc:
        return
    else:
        path = unquote(url.path)
        if path.startswith(PREFIX):
            target = SITE / path[len(PREFIX) :]
        elif path.startswith("/"):
            errors.append(f"{source.relative_to(ROOT)}: wrong Pages base: {value}")
            return
        else:
            target = source.parent / path if path else source
    target = target.resolve()
    if not target.is_relative_to(ROOT):
        errors.append(f"{source.relative_to(ROOT)}: out-of-repo link: {value}")
        return
    if target.is_dir():
        target = target / "index.html"
    if not target.exists():
        errors.append(f"{source.relative_to(ROOT)}: missing target: {value}")
        return
    if url.fragment and target.suffix in {".md", ".html"}:
        ids = markdown_ids(target) if target.suffix == ".md" else Page(target).ids
        if unquote(url.fragment) not in ids:
            errors.append(f"{source.relative_to(ROOT)}: missing fragment: {value}")


def read_screenshot_manifest(path):
    """Shared capture-manifest reader; never infer a missing provenance claim."""
    with path.open("rb") as stream:
        data = stream.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Screenshot manifest exceeds its size bound")
    document = json.loads(data)
    if not isinstance(document, dict) or document.get("schema") != "public_ui_screenshots_v1":
        raise ValueError("Unsupported screenshot manifest")
    for field in ("images", "source_files"):
        if not isinstance(document.get(field), list) or not all(isinstance(row, dict) for row in document[field]):
            raise ValueError("Malformed screenshot entries")
        if not document[field]:
            raise ValueError("Missing screenshot evidence")
        for row in document[field]:
            name = row.get("file")
            if (not isinstance(name, str) or not name or Path(name).is_absolute()
                    or ".." in Path(name).parts or "\\" in name or "\0" in name):
                raise ValueError("Capture path must stay inside its source/artifact scope")
            if field == "images" and Path(name).name != name:
                raise ValueError("Screenshot file must be a PNG basename")
    return document


def screenshot_errors(manifest, directory):
    """Existing capture/content checks, shared with candidate validation."""
    errors = []
    expected_images = {
        f"{view}-{lang}.png"
        for view in ("overview", "discussions", "requirements", "issues", "schedule", "dependencies", "board", "session")
        for lang in ("en", "zh")
    }
    if {entry["file"] for entry in manifest["images"]} != expected_images:
        errors.append("Screenshot manifest must describe all sixteen public images.")
    if len(manifest["images"]) != len(expected_images):
        errors.append("Screenshot manifest contains missing or duplicate images.")
    for field, expected in {"synthetic_data": True, "native_or_live_acceptance": False,
                            "dom_content_modified": False, "timezone": "UTC"}.items():
        if manifest.get(field) != expected:
            errors.append(f"Screenshot manifest has an invalid {field} guarantee.")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest.get("frontend_source_commit", "")):
        errors.append("Screenshot manifest must pin a full source commit.")
    if manifest.get("source_pin_kind") != "merged":
        errors.append("Public screenshots must use a merged source pin.")
    for entry in manifest["images"]:
        path = directory / entry["file"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            errors.append(f"Screenshot does not match its capture manifest: {entry['file']}")
        language = "zh-TW" if entry["file"].endswith("-zh.png") else "en-US"
        height = 1600 if entry["scenario"] == "requirements" else 960
        if entry.get("locale") != language or entry.get("theme") != "light" or entry.get("viewport") != {"width": 1440, "height": height}:
            errors.append(f"Screenshot has incorrect capture settings: {entry['file']}")
        if path.is_file():
            png = path.read_bytes()
            size = {"width": int.from_bytes(png[16:20], "big"), "height": int.from_bytes(png[20:24], "big")}
            if entry.get("image_size") != size or size["width"] != 1440 or size["height"] < height or entry.get("full_page") is not True:
                errors.append(f"Screenshot is missing full-page dimensions: {entry['file']}")
        checks = entry.get("responsive_checks", [])
        if [check.get("viewport") for check in checks] != [390, 768, 1440] or any(
            check.get("horizontal_overflow") is not False or check.get("content", float("inf")) > check.get("viewport", 0)
            for check in checks
        ):
            errors.append(f"Screenshot is missing passing responsive checks: {entry['file']}")

    return errors


def capture_source_errors(manifest, root: Path, commit: str) -> list[str]:
    """Verify capture sources without depending on the caller's checkout."""
    errors: list[str] = []
    git = shutil.which("git")
    if git is None:
        raise SystemExit("Git is required to check screenshot capture sources.")

    def pinned(spec, text=False):
        # Resolved executable and fixed read-only verbs.
        result = subprocess.run([git, "-C", str(root), *spec], capture_output=True, text=text)  # noqa: S603
        return result.stdout if result.returncode == 0 else None

    if pinned(["cat-file", "-e", f"{commit}^{{commit}}"]) is None:
        errors.append(f"Pinned frontend commit is unavailable; fetch {commit} before checking capture sources.")
    else:
        if (pinned(["rev-parse", f"{commit}:desktop/src"], text=True) or "").strip() != manifest.get("frontend_source_tree"):
            errors.append("Screenshot manifest tree does not match its pinned commit.")
        for entry in manifest["source_files"]:
            file = entry["file"]
            if any(file.startswith(path) if path.endswith("/") else file == path for path in PINNED_PATHS):
                data = pinned(["cat-file", "blob", f"{commit}:{entry['file']}"])
            elif file in WORKING_TREE_INPUTS:
                path = root / file
                data = path.read_bytes() if path.is_file() else None
            else:
                errors.append(f"Capture source is neither pinned nor an allowed working-tree input: {file}")
                continue
            try:
                expected_entry = source_entry(entry["file"], data) if data is not None else None
            except ValueError as exc:
                errors.append(f"Capture source is invalid ({entry['file']}): {exc}")
                continue
            if expected_entry != entry:
                errors.append(f"Capture source does not match its manifest: {entry['file']}")
    return errors


def main() -> None:
    errors: list[str] = []
    links = 0
    for path in SITE.glob("*.html"):
        page = Page(path)
        errors.extend(f"{path.relative_to(ROOT)}: {error}" for error in page.errors)
        for value in page.links:
            check_link(path, value, errors)
            links += 1
    for path in [
        ROOT / "README.md",
        ROOT / "README.zh-TW.md",
        ROOT / "CHANGELOG.md",
        ROOT / "docs/getting-started.md",
        ROOT / "docs/getting-started.zh-TW.md",
        ROOT / "docs/windows.md",
        ROOT / "docs/windows.zh-TW.md",
        ROOT / "docs/design/managed-installation.md",
        ROOT / "docs/product/progress-2026-10-12.md",
        ROOT / "docs/product/progress-2026-10-14.md",
        ROOT / "docs/product/progress-2026-10-16.md",
        ROOT / "docs/product/implementation-status.md",
        ROOT / "docs/product/pm-v1.2-status.md",
        ROOT / "docs/product/pm-v1.2-history-through-2026-10-12.md",
        ROOT / "docs/handoff/2026-10-12-pm-v1.2.md",
        ROOT / "docs/handoff/2026-10-14-docs-ux-v2.md",
        SITE / "README.md",
        SITE / "DESIGN.md",
        SITE / "images/README.md",
    ]:
        for value in re.findall(r"\]\(([^\s)]+)\)", path.read_text()):
            check_link(path, value, errors)
            links += 1
    manifest = read_screenshot_manifest(SITE / "images/manifest.json")
    if manifest.get("frontend_source_commit") != "437ee6a7143a82e32eae1d6a008203b2df072aaf":
        errors.append("Public screenshots must pin published preview source at 437ee6a7.")
    errors.extend(screenshot_errors(manifest, SITE / "images"))
    errors.extend(capture_source_errors(manifest, ROOT, manifest.get("frontend_source_commit", "")))
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Checked {links} links and static assets; no missing local targets, fragments or duplicate IDs.")


if __name__ == "__main__":
    main()
