"""Check static assets, fragment targets and repository documentation links offline."""

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
PREFIX = "/bat-agent-connector/"
REPO = "https://github.com/teddashh/bat-agent-connector/blob/main/"


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


def check_link(source, value):
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


errors = []
links = 0
for path in SITE.glob("*.html"):
    page = Page(path)
    errors.extend(f"{path.relative_to(ROOT)}: {error}" for error in page.errors)
    for value in page.links:
        check_link(path, value)
        links += 1
for path in [
    ROOT / "README.md",
    ROOT / "README.zh-TW.md",
    ROOT / "docs/getting-started.md",
    ROOT / "docs/getting-started.zh-TW.md",
    ROOT / "docs/windows.md",
    ROOT / "docs/windows.zh-TW.md",
    ROOT / "docs/design/managed-installation.md",
    ROOT / "docs/product/progress-2026-10-12.md",
    ROOT / "docs/product/pm-v1.2-status.md",
    ROOT / "docs/product/pm-v1.2-history-through-2026-10-12.md",
    ROOT / "docs/handoff/2026-10-12-pm-v1.2.md",
    SITE / "README.md",
    SITE / "images/README.md",
]:
    for value in re.findall(r"\]\(([^\s)]+)\)", path.read_text()):
        check_link(path, value)
        links += 1
manifest = json.loads((SITE / "images/manifest.json").read_text())
expected_images = {
    f"{view}-{lang}.png"
    for view in ("workspace", "dispatch", "pm-planning", "pm-kanban")
    for lang in ("en", "zh")
}
if {entry["file"] for entry in manifest["images"]} != expected_images:
    errors.append("Screenshot manifest must describe all eight public images.")
for entry in manifest["images"]:
    path = SITE / "images" / entry["file"]
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        errors.append(f"Screenshot does not match its capture manifest: {entry['file']}")
if errors:
    raise SystemExit("\n".join(errors))
print(f"Checked {links} links and static assets; no missing local targets, fragments or duplicate IDs.")
