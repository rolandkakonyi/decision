"""Check rendered links and assets, including raw HTML, under the Pages base."""

from html.parser import HTMLParser
from pathlib import Path
import tomllib
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "zensical.toml").read_text())["project"]
SITE = ROOT / PROJECT["site_dir"]
BASE = urlsplit(PROJECT["site_url"]).path


class Page(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.ids = set()
        self.links = []
        self.feed(path.read_text(encoding="utf-8"))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for name in ("href", "src"):
            if name in attrs:
                self.links.append(attrs[name])


def check():
    if not (SITE / "index.html").is_file():
        raise ValueError("Build the site with zensical before checking links")
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
            if not resolved.is_relative_to(SITE):
                failures.append(f"{path.relative_to(SITE)}: outside project base: {link}")
            elif not resolved.is_file():
                failures.append(f"{path.relative_to(SITE)}: missing target: {link}")
            elif parsed.fragment and resolved in pages and unquote(parsed.fragment) not in pages[resolved].ids:
                failures.append(f"{path.relative_to(SITE)}: missing anchor: {link}")
    if failures:
        raise ValueError("\n".join(failures))
    print(f"Checked links, assets, and fragments in {len(pages)} HTML pages under {BASE}")


if __name__ == "__main__":
    check()
