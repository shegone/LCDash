"""Fetch a small allowlisted set of public CSS Mindshare pages for JACK."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from time import sleep
from urllib.parse import urljoin, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import os

PAGES = (
    "https://css-mindshare.com/",
    "https://css-mindshare.com/about-us/",
    "https://css-mindshare.com/why-mindshare/",
    "https://css-mindshare.com/solutions/",
    "https://css-mindshare.com/downloads/",
    "https://css-mindshare.com/case-studies/",
    "https://css-mindshare.com/faqs/",
)
PDF_PATH_PREFIX = "/wp-content/uploads/"
MAX_PDF_BYTES = 25 * 1024 * 1024


class Text(HTMLParser):
    def __init__(self): super().__init__(); self.parts = []; self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "").strip()
            if href: self.links.append(href)
    def handle_data(self, data):
        value = " ".join(data.split())
        if value: self.parts.append(value)


def _approved_pdf_url(page_url: str, href: str) -> str | None:
    url = urljoin(page_url, href)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"css-mindshare.com", "www.css-mindshare.com"}:
        return None
    if not parsed.path.startswith(PDF_PATH_PREFIX):
        return None
    if not parsed.path.lower().endswith(".pdf"):
        return None
    return url


def _safe_pdf_name(url: str) -> str:
    name = Path(urlparse(url).path).name
    return "css_mindshare_public_" + "".join(
        character if character.isalnum() or character in ".-_" else "_"
        for character in name
    )


def run_once(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    pdf_urls = set()
    for index, url in enumerate(PAGES, 1):
        request = Request(url, headers={"User-Agent": "LCDash-JACK-Public-Knowledge/1.0"})
        with urlopen(request, timeout=20) as response:
            parser = Text(); parser.feed(response.read().decode("utf-8", "replace"))
        pdf_urls.update(
            approved
            for href in parser.links
            if (approved := _approved_pdf_url(url, href))
        )
        (target / f"css_mindshare_{index:02d}.txt").write_text(
            f"Public CSS Mindshare website source\nURL: {url}\n\n" + "\n".join(parser.parts) + "\n",
            encoding="utf-8",
        )
    for url in sorted(pdf_urls):
        request = Request(url, headers={"User-Agent": "LCDash-JACK-Public-Knowledge/1.0"})
        try:
            with urlopen(request, timeout=30) as response:
                declared_size = int(response.headers.get("Content-Length") or 0)
                if declared_size > MAX_PDF_BYTES:
                    continue
                content = response.read(MAX_PDF_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            print({"public_pdf_sync_error": url, "detail": str(exc)}, flush=True)
            continue
        if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF"):
            continue
        (target / _safe_pdf_name(url)).write_bytes(content)


if __name__ == "__main__":
    root = Path(os.getenv("MINDSHARE_PUBLIC_SITE_TARGET", "/knowledge/mindshare/Public CSS Mindshare Website"))
    interval = max(int(os.getenv("MINDSHARE_PUBLIC_SITE_SYNC_SECONDS", "86400")), 3600)
    while True:
        try: run_once(root)
        except Exception as exc: print({"public_site_sync_error": str(exc)}, flush=True)
        sleep(interval)
