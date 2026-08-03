"""Fetch a small allowlisted set of public CSS Mindshare pages for JACK."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from time import sleep
from urllib.request import Request, urlopen
import os

PAGES = (
    "https://css-mindshare.com/",
    "https://css-mindshare.com/about-us/",
    "https://css-mindshare.com/why-mindshare/",
    "https://css-mindshare.com/solutions/",
    "https://css-mindshare.com/downloads/",
    "https://css-mindshare.com/faqs/",
)


class Text(HTMLParser):
    def __init__(self): super().__init__(); self.parts = []
    def handle_data(self, data):
        value = " ".join(data.split())
        if value: self.parts.append(value)


def run_once(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for index, url in enumerate(PAGES, 1):
        request = Request(url, headers={"User-Agent": "LCDash-JACK-Public-Knowledge/1.0"})
        with urlopen(request, timeout=20) as response:
            parser = Text(); parser.feed(response.read().decode("utf-8", "replace"))
        (target / f"css_mindshare_{index:02d}.txt").write_text(
            f"Public CSS Mindshare website source\nURL: {url}\n\n" + "\n".join(parser.parts) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    root = Path(os.getenv("MINDSHARE_PUBLIC_SITE_TARGET", "/knowledge/mindshare/Public CSS Mindshare Website"))
    interval = max(int(os.getenv("MINDSHARE_PUBLIC_SITE_SYNC_SECONDS", "86400")), 3600)
    while True:
        try: run_once(root)
        except Exception as exc: print({"public_site_sync_error": str(exc)}, flush=True)
        sleep(interval)
