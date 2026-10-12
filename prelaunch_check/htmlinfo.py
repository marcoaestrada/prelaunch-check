"""A tiny HTML reader: collects only what the checks need."""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Dict, List


class PageInfo(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: Dict[str, str] = {}
        self.title = ""
        self.h1_count = 0
        self.links: List[str] = []
        self.resources: List[str] = []  # script/img/iframe/source/stylesheet URLs
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name:
                self.meta[name] = a.get("content", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag in ("script", "img", "iframe", "source") and a.get("src"):
            self.resources.append(a["src"])
        elif tag == "link" and a.get("href") and "stylesheet" in a.get("rel", "").lower():
            self.resources.append(a["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def parse(html: str) -> PageInfo:
    info = PageInfo()
    info.feed(html)
    info.close()
    return info
