"""Assemble authoritative Markdown, build the site, and check local links."""

import argparse
from html.parser import HTMLParser
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit

from markdown.extensions.toc import slugify


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".state/docs"
SITE = ROOT / ".state/site"
REPO = "https://github.com/rolandkakonyi/decision/blob/main/"
BASE = "/decision/"
SECTIONS = {
    "Install": "quickstart.md",
    "Quickstart": "quickstart.md",
    "Ask several questions together": "cli.md",
    "Response stats": "cli.md",
    "Images and Box": "images.md",
    "Mix Noul, Box, and Choice in one request": "images.md",
    "Provider settings": "providers.md",
    "MCP in the same command": "mcp.md",
    "Generate a Codex plugin": "agent-integrations.md",
    "Generated skill": "agent-integrations.md",
    "Development": "development.md",
}
DOCUMENTS = {
    "docs/provider-plugins.md": "provider-plugins.md",
    "docs/standalone.md": "standalone.md",
    "skills/decision/references/examples.md": "examples.md",
    "skills/decision/references/plugin.md": "plugin-reference.md",
    "skills/decision/SKILL.md": "skill-reference.md",
    "CONTRIBUTING.md": "contributing.md",
}


def assemble():
    if SOURCE.exists():
        shutil.rmtree(SOURCE)
    shutil.copytree(ROOT / "docs/site", SOURCE, dirs_exist_ok=True)
    shutil.copytree(ROOT / "docs/assets", SOURCE / "assets", dirs_exist_ok=True)
    references = ROOT / "skills/decision/references"
    for path in references.glob("*.json"):
        shutil.copyfile(path, SOURCE / path.name)
    shutil.copytree(ROOT / "examples", SOURCE / "downloads", dirs_exist_ok=True)

    readme = (ROOT / "README.md").read_text()
    parts = re.split(r"(?m)^(## .+)$", readme)
    sections = dict(zip((heading[3:] for heading in parts[1::2]), parts[2::2]))
    missing = SECTIONS.keys() - sections.keys()
    if missing:
        raise ValueError(f"README sections missing: {sorted(missing)}")
    anchors = {}
    for heading, destination in SECTIONS.items():
        anchors[slugify(heading, "-")] = destination
        for subheading in re.findall(r"(?m)^#{3,6} (.+)$", sections[heading]):
            anchors[slugify(subheading, "-")] = destination

    def rewrite(text, source, destination):
        def link(match):
            label, target = match.groups()
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                return match.group()
            path = posixpath.normpath(posixpath.join(posixpath.dirname(source), parsed.path)) if parsed.path else source
            fragment = f"#{parsed.fragment}" if parsed.fragment else ""
            if path == "README.md" and parsed.fragment in anchors:
                mapped = anchors[parsed.fragment]
            elif path == "README.md" and not parsed.fragment:
                mapped = "index.md"
            elif path in DOCUMENTS:
                mapped = DOCUMENTS[path]
            elif path.startswith("skills/decision/references/") and path.endswith(".json"):
                mapped = posixpath.basename(path)
            elif path.startswith("examples/"):
                mapped = "downloads/" + path.removeprefix("examples/")
            elif path.startswith("docs/assets/"):
                mapped = "assets/" + path.removeprefix("docs/assets/")
            elif source.startswith("docs/site/"):
                mapped = path.removeprefix("docs/site/")
            else:
                if not (ROOT / path).exists():
                    raise ValueError(f"Missing source link in {source}: {target}")
                repository_url = REPO.replace("/blob/", "/tree/") if (ROOT / path).is_dir() else REPO
                return f"{label}({repository_url}{path}{fragment})"
            relative = posixpath.relpath(mapped, posixpath.dirname(destination) or ".")
            return f"{label}({relative}{fragment})"

        # Source documents use inline Markdown links; fenced examples are left intact.
        pieces = re.split(r"(?ms)(^```.*?^```[^\n]*$)", text)
        for index in range(0, len(pieces), 2):
            pieces[index] = re.sub(r"(!?\[[^\]]*\])\(([^\s)]+)\)", link, pieces[index])
        return "".join(pieces)

    for destination in dict.fromkeys(SECTIONS.values()):
        content = "\n".join(f"## {heading}\n{sections[heading]}" for heading, page in SECTIONS.items() if page == destination)
        title = {
            "quickstart.md": "Install and quickstart", "cli.md": "CLI and batches",
            "images.md": "Images and bounding boxes", "providers.md": "Provider configuration",
            "mcp.md": "MCP server", "agent-integrations.md": "Codex plugin and Agent Skill",
            "development.md": "Development",
        }[destination]
        (SOURCE / destination).write_text(rewrite(f"# {title}\n\n{content}", "README.md", destination))
    for source, destination in DOCUMENTS.items():
        text = (ROOT / source).read_text()
        (SOURCE / destination).write_text(rewrite(text, source, destination))
    for path in (ROOT / "docs/site").glob("*.md"):
        (SOURCE / path.name).write_text(rewrite(path.read_text(), f"docs/site/{path.name}", path.name))
    help_text = (references / "cli-help.txt").read_text()
    commands = re.split(r"(?m)^(decision[^\n]*)$", help_text)[1:]
    content = "# Command reference\n\nGenerated from the running CLI's command definitions.\n"
    for command, flags in zip(commands[::2], commands[1::2]):
        content += f"\n## `{command}`\n\n```text\n{flags.strip()}\n```\n"
    (SOURCE / "command-reference.md").write_text(content)
    schema = (references / "request-schema.json").read_text()
    (SOURCE / "schema.md").write_text(
        "# Request schema\n\nThe common request envelope generated by Decision. Provider constraints "
        "may narrow this schema; MCP advertises its configured provider's limits.\n\n"
        "[Download JSON Schema](request-schema.json)\n\n```json\n" + schema.rstrip() + "\n```\n"
    )


class Page(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.ids = set()
        self.links = []
        self.feed(path.read_text())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for name in ("href", "src"):
            if name in attrs:
                self.links.append(attrs[name])


def check_links():
    pages = {path: Page(path) for path in SITE.rglob("*.html")}
    failures = []
    for path, page in pages.items():
        for link in page.links:
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc:
                continue
            target = unquote(parsed.path)
            if target.startswith("/"):
                if not target.startswith(BASE):
                    failures.append(f"{path.relative_to(SITE)}: outside project base: {link}")
                    continue
                resolved = SITE / target.removeprefix(BASE)
            else:
                resolved = (path.parent / target).resolve() if target else path
            if resolved.is_dir():
                resolved /= "index.html"
            if not resolved.exists():
                failures.append(f"{path.relative_to(SITE)}: missing target: {link}")
            elif parsed.fragment and resolved in pages and unquote(parsed.fragment) not in pages[resolved].ids:
                failures.append(f"{path.relative_to(SITE)}: missing anchor: {link}")
    if failures:
        raise ValueError("\n".join(failures))
    print(f"Checked links, assets, and fragments in {len(pages)} HTML pages under {BASE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Assemble Markdown for zensical serve")
    args = parser.parse_args()
    assemble()
    if not args.prepare_only:
        subprocess.run(["zensical", "build", "--clean"], cwd=ROOT, check=True)
        check_links()
